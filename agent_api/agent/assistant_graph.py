from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.runnables import Runnable
from dataclasses import dataclass
import logging
import random

from typing import Optional
from llm_factory import TRANSIENT_LLM_ERRORS
from agent.pipeline_modes import (
    normalize_pipeline_mode,
    pipeline_gate_enabled,
    pipeline_refine_enabled,
)
from agent.nodes.normalize_output import normalize_question_output
from agent.nodes.chunk_gate.chunk_gate import synthetic_gate_pass

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    chunk: str
    question_type: str
    source: str
    source_text: str
    language: Optional[str]  # ru, be, tg
    pipeline_mode: str
    document_kind: str
    # body | list | inset | caption | glossary_entry (None for legacy chunks).
    # Declared here because LangGraph drops undeclared state keys.
    block_type: Optional[str]
    chunk_pre_validated: bool
    refine_available: bool
    chunk_gate_result: dict
    chunk_rejected: bool
    generated_question: dict
    sensitivity_score: dict
    difficulty_score: dict
    validation_result: dict
    # True, если хотя бы одна из оценок (provocativeness/difficulty) не
    # состоялась; тогда её score/explanation равны None, а причина — в
    # assessment_errors. Воркер по этому маркеру не сохраняет «оценку».
    assessment_failed: bool
    assessment_errors: List[str]
    # Refine вернул исходный вопрос с маркером refine_failed: граф завершается,
    # не гоняя тот же вопрос по оценкам/валидации ещё раз.
    refine_failed: bool
    questions: Optional[List[str]]
    retry_count: int
    # Refine can make a question worse, so the best attempt is carried along and
    # restored at the end. These MUST stay declared: LangGraph drops any key a
    # node returns that is not part of the state schema, which silently killed
    # this logic before.
    original_question: dict
    best_question: dict
    best_validation_total: Optional[float]
    best_validation_result: dict


def shuffle_answer_options(question_data: dict, question_type: str) -> dict:
    """
    Перемешивает варианты ответа и обновляет поле outputs с новыми номерами правильных ответов.
    
    Args:
        question_data: Словарь с данными вопроса (option_1, option_2, ..., outputs)
        question_type: Тип вопроса ("one", "multi", "open")
    
    Returns:
        Обновленный словарь с перемешанными вариантами ответа
    """
    # Для типа "open" вариантов ответа нет, ничего не делаем
    if question_type == "open":
        return question_data
    
    # Преобразуем в словарь, если это Pydantic модель
    if not isinstance(question_data, dict):
        if hasattr(question_data, 'model_dump'):
            question_data = question_data.model_dump()
        elif hasattr(question_data, 'dict'):
            question_data = question_data.dict()
        else:
            return question_data
    
    # Собираем все непустые варианты ответа с исходными позициями (1-based)
    option_pairs = []
    for i in range(1, 10):
        key = f"option_{i}"
        value = question_data.get(key)
        if value and value not in [None, "None"]:
            option_pairs.append((i, value))

    if not option_pairs:
        return question_data

    # Определяем правильные ответы из поля outputs
    outputs = question_data.get("outputs", "")
    if isinstance(outputs, int):
        outputs = str(outputs)
    elif not isinstance(outputs, str):
        outputs = str(outputs)

    # Парсим правильные ответы
    if question_type == "one":
        try:
            correct_indices = [int(outputs.strip())]
        except (ValueError, AttributeError):
            return question_data
    elif question_type == "multi":
        try:
            correct_indices = [int(x.strip()) for x in outputs.split(",") if x.strip()]
        except (ValueError, AttributeError):
            return question_data
    else:
        return question_data

    valid_positions = {pos for pos, _ in option_pairs}
    correct_set = {idx for idx in correct_indices if idx in valid_positions}
    if not correct_set:
        return question_data

    # Перемешиваем пары (позиция, значение) и пересчитываем индексы по перестановке
    shuffled_pairs = option_pairs.copy()
    random.shuffle(shuffled_pairs)

    old_to_new = {old_pos: new_pos for new_pos, (old_pos, _) in enumerate(shuffled_pairs, start=1)}

    for i in range(1, 10):
        question_data[f"option_{i}"] = None

    for new_pos, (_, value) in enumerate(shuffled_pairs, start=1):
        question_data[f"option_{new_pos}"] = value

    new_correct_indices = sorted(old_to_new[idx] for idx in correct_set if idx in old_to_new)

    if question_type == "one":
        if new_correct_indices:
            question_data["outputs"] = new_correct_indices[0]
    elif question_type == "multi":
        if new_correct_indices:
            question_data["outputs"] = ",".join(map(str, new_correct_indices))
    
    return question_data


MAX_REFINE_RETRIES = 2


def _read_assessment(result, score_field: str):
    """(score, explanation, failed, error) из результата цепочки оценки.

    Принимает и Pydantic-модель, и словарь. Отсутствующий score трактуется как
    сбой оценки, а не как значение по умолчанию.
    """
    if isinstance(result, dict):
        get = result.get
    else:
        def get(key, default=None):
            return getattr(result, key, default)
    failed = bool(get("assessment_failed", False))
    score = get(score_field)
    explanation = get("explanation")
    error = get("assessment_error")
    if failed or score is None:
        return None, None, True, str(error or f"{score_field} missing")
    return score, explanation, False, None


@dataclass
class GENAAssistant:
    generate_question_chain: Runnable
    provocativeness_chain: Runnable
    validation_chain: Runnable
    difficulty_chain: Runnable
    chunk_gate_chain: Optional[Runnable] = None
    refine_question_chain: Optional[Runnable] = None
    checkpointer: Optional[BaseCheckpointSaver] = None

    def __post_init__(self):
        builder = StateGraph(AgentState)

        builder.add_node("chunk_gate", self.chunk_gate_node)
        builder.add_node("generate_question", self.generate_question_node)
        builder.add_node("provocativeness", self.provocativeness_node)
        builder.add_node("validation", self.validation_node)
        builder.add_node("difficulty", self.difficulty_node)
        builder.add_node("refine_question", self.refine_question_node)

        builder.set_entry_point("chunk_gate")
        builder.add_conditional_edges("chunk_gate", self._should_generate, {
            "end": END,
            "generate": "generate_question",
        })
        builder.add_edge("generate_question", "provocativeness")
        builder.add_edge("provocativeness", "difficulty")
        builder.add_edge("difficulty", "validation")
        builder.add_conditional_edges("validation", self._should_retry, {
            "end": END,
            "refine": "refine_question",
        })
        builder.add_conditional_edges("refine_question", self._after_refine, {
            "end": END,
            "assess": "provocativeness",
        })

        if self.checkpointer is not None:
            self.graph = builder.compile(checkpointer=self.checkpointer)
        else:
            self.graph = builder.compile()

    @staticmethod
    def _should_generate(state: AgentState) -> Literal["end", "generate"]:
        if state.get("chunk_rejected", False):
            return "end"
        return "generate"

    @staticmethod
    def _should_retry(state: AgentState) -> Literal["end", "refine"]:
        if not pipeline_refine_enabled(state.get("pipeline_mode")):
            return "end"
        if not state.get("refine_available", False):
            return "end"
        if state.get("refine_failed", False):
            return "end"
        vr = state.get("validation_result") or {}
        if vr.get("passed") is not False:
            return "end"
        retry_count = state.get("retry_count") or 0
        if retry_count >= MAX_REFINE_RETRIES:
            return "end"
        return "refine"

    @staticmethod
    def _after_refine(state: AgentState) -> Literal["end", "assess"]:
        if state.get("refine_failed", False):
            return "end"
        return "assess"

    def chunk_gate_node(self, state: AgentState) -> AgentState:
        """Предварительная валидация чанка — gate-фильтр.

        Пропускается, если чанк уже прошёл gate на стороне воркера/фронта
        (chunk_pre_validated=True) или если цепочка gate не сконфигурирована.
        """
        pipeline_mode = normalize_pipeline_mode(state.get("pipeline_mode"))
        if not pipeline_gate_enabled(pipeline_mode):
            return {
                **state,
                "pipeline_mode": pipeline_mode,
                "chunk_rejected": False,
                "chunk_gate_result": synthetic_gate_pass("gate_disabled_by_pipeline_mode"),
            }

        if state.get("chunk_pre_validated"):
            return {
                **state,
                "pipeline_mode": pipeline_mode,
                "chunk_rejected": False,
                "chunk_gate_result": synthetic_gate_pass("chunk_pre_validated"),
            }

        if self.chunk_gate_chain is None:
            return {
                **state,
                "pipeline_mode": pipeline_mode,
                "chunk_rejected": False,
                "chunk_gate_result": synthetic_gate_pass("gate_chain_not_configured"),
            }

        result = self.chunk_gate_chain.invoke({
            "chunk": state["chunk"],
            "question_type": state["question_type"],
            "document_kind": state.get("document_kind") or "normative",
            "block_type": state.get("block_type"),
        })

        gate_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)

        return {
            **state,
            "pipeline_mode": pipeline_mode,
            "chunk_gate_result": gate_dict,
            "chunk_rejected": not gate_dict.get("passed", True),
        }

    def generate_question_node(self, state: AgentState) -> AgentState:
        """Генерация вопроса"""
        input_data = {
            "input_text": state["chunk"],
            "question_type": state["question_type"],
            "source": state.get("source", ""),
            "document_kind": state.get("document_kind") or "normative",
            "block_type": state.get("block_type"),
        }
        # Добавляем язык, если он указан в состоянии
        if "language" in state and state.get("language"):
            input_data["language"] = state["language"]
        
        result = self.generate_question_chain.invoke(input_data)

        result = normalize_question_output(result, state["question_type"])
        result = shuffle_answer_options(result, state["question_type"])
        if state.get("source_text"):
            if not isinstance(result, dict):
                result = dict(result) if hasattr(result, "items") else {"task": str(result)}
            result["source_text"] = state["source_text"]

        return {
            **state,
            "generated_question": result,
            "original_question": result,
            "best_question": result,
            "best_validation_total": None,
        }


    def provocativeness_node(self, state: AgentState) -> AgentState:
        """Оценка чувствительности"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
        
        result = self.provocativeness_chain.invoke({
            "generated_question": generated_question,
            "source_text": state.get("source_text", ""),
        })

        provocativeness_score, explanation, failed, error = _read_assessment(
            result, "provocativeness_score"
        )
        if failed:
            logger.warning("Sensitivity assessment failed: %s", error)

        # Первая оценка в круге: сбрасываем маркеры предыдущего круга refine.
        return {
            **state,
            "sensitivity_score": {
                "provocativeness_score": provocativeness_score,
                "explanation": explanation,
                "assessment_failed": failed,
                "assessment_error": error,
            },
            "assessment_failed": failed,
            "assessment_errors": [f"provocativeness: {error}"] if failed else [],
        }
    def difficulty_node(self, state: AgentState) -> AgentState:
        """Оценка сложности вопроса"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
        
        result = self.difficulty_chain.invoke({
            "generated_question": generated_question,
            "source_text": state.get("source_text", ""),
        })

        difficulty, explanation, failed, error = _read_assessment(result, "difficulty")
        if failed:
            logger.warning("Difficulty assessment failed: %s", error)

        errors = list(state.get("assessment_errors") or [])
        if failed:
            errors.append(f"difficulty: {error}")
        return {
            **state,
            "difficulty_score": {
                "difficulty": difficulty,
                "explanation": explanation,
                "assessment_failed": failed,
                "assessment_error": error,
            },
            "assessment_failed": bool(state.get("assessment_failed")) or failed,
            "assessment_errors": errors,
        }

    def validation_node(self, state: AgentState) -> AgentState:
        """Валидация качества вопроса"""
        # Убеждаемся, что generated_question - это словарь
        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()
            else:
                raise ValueError(f"Unexpected type for generated_question: {type(generated_question)}")
        
        source_text = state.get("source_text") or generated_question.get("source_text", "")
        result = self.validation_chain.invoke({
            "question_type": state["question_type"],
            "source_text": source_text,
            "question": {k: v for k, v in generated_question.items() if k != "source_text" and v is not None},
            "document_kind": state.get("document_kind") or "normative",
        })
        if isinstance(result, dict):
            validation_result = {
                "type": result.get("type", state["question_type"]),
                "by_block": result.get("by_block", {}),
                "block_weighted": result.get("block_weighted", {}),
                "block_max": result.get("block_max", {}),
                "justifications": result.get("justifications", {}),
                "total": result.get("total", 0.0),
                "max_total": result.get("max_total", 0.0),
                "threshold": result.get("threshold", 0.0),
                "passed": result.get("passed", False),
                "parse_complete": result.get("parse_complete", False),
            }
        else:
            validation_result = {
                "type": result.type,
                "by_block": result.by_block,
                "block_weighted": result.block_weighted,
                "block_max": result.block_max,
                "justifications": result.justifications,
                "total": result.total,
                "max_total": result.max_total,
                "threshold": result.threshold,
                "passed": result.passed,
                "parse_complete": result.parse_complete,
            }
        total = float(validation_result.get("total") or 0)
        best_total = state.get("best_validation_total")
        best_question = state.get("best_question") or generated_question
        best_vr = state.get("best_validation_result") or validation_result
        if best_total is None or total >= float(best_total):
            best_question = generated_question
            best_total = total
            best_vr = validation_result
        else:
            generated_question = best_question
            validation_result = best_vr
        return {
            **state,
            "generated_question": generated_question,
            "validation_result": validation_result,
            "best_question": best_question,
            "best_validation_total": best_total,
            "best_validation_result": best_vr,
        }

    def refine_question_node(self, state: AgentState) -> AgentState:
        """Доработка вопроса по замечаниям валидатора."""
        if self.refine_question_chain is None:
            return {**state, "retry_count": state.get("retry_count", 0) + 1}

        generated_question = state["generated_question"]
        if not isinstance(generated_question, dict):
            if hasattr(generated_question, 'model_dump'):
                generated_question = generated_question.model_dump()
            elif hasattr(generated_question, 'dict'):
                generated_question = generated_question.dict()

        source_text = state.get("source_text") or generated_question.get("source_text", "")
        refine_input = {
            "question_type": state["question_type"],
            "source_text": source_text,
            "question": {k: v for k, v in generated_question.items() if k != "source_text" and v is not None},
            "validation_result": state.get("validation_result", {}),
            "document_kind": state.get("document_kind") or "normative",
            "block_type": state.get("block_type"),
        }
        if state.get("language"):
            refine_input["language"] = state["language"]

        try:
            result = self.refine_question_chain.invoke(refine_input)
        except TRANSIENT_LLM_ERRORS:
            raise
        except Exception as exc:
            logger.warning("Refine failed, keeping previous question: %s", exc)
            return {
                **state,
                "refine_failed": True,
                "retry_count": (state.get("retry_count") or 0) + 1,
            }

        refine_failed = (
            result.get("refine_failed") if isinstance(result, dict)
            else getattr(result, "refine_failed", False)
        )
        if refine_failed:
            refine_error = (
                result.get("refine_error") if isinstance(result, dict)
                else getattr(result, "refine_error", None)
            )
            logger.warning(
                "Refine returned the original question (%s); stopping without re-assessment",
                refine_error,
            )
            return {
                **state,
                "refine_failed": True,
                "retry_count": (state.get("retry_count") or 0) + 1,
            }

        result = normalize_question_output(result, state["question_type"])
        result = shuffle_answer_options(result, state["question_type"])
        if source_text:
            if not isinstance(result, dict):
                result = dict(result) if hasattr(result, "items") else {"task": str(result)}
            result["source_text"] = source_text

        return {
            **state,
            "generated_question": result,
            "retry_count": state.get("retry_count", 0) + 1,
        }
