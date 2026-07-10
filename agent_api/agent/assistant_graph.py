from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.runnables import Runnable
from dataclasses import dataclass
import random

from typing import Optional
from agent.pipeline_modes import (
    normalize_pipeline_mode,
    pipeline_gate_enabled,
    pipeline_refine_enabled,
)

class AgentState(TypedDict):
    chunk: str
    question_type: str
    source: str
    language: Optional[str]  # ru, be, tg
    pipeline_mode: str
    chunk_pre_validated: bool
    chunk_gate_result: dict
    chunk_rejected: bool
    generated_question: dict
    sensitivity_score: dict
    difficulty_score: dict
    validation_result: dict
    final_json: dict
    questions: Optional[List[str]]
    retry_count: int


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
    
    # Собираем все непустые варианты ответа
    options = []
    for i in range(1, 10):
        key = f"option_{i}"
        value = question_data.get(key)
        if value and value not in [None, "None"]:
            options.append(value)
    
    if not options:
        return question_data
    
    # Определяем правильные ответы из поля outputs
    outputs = question_data.get("outputs", "")
    if isinstance(outputs, int):
        outputs = str(outputs)
    elif not isinstance(outputs, str):
        outputs = str(outputs)
    
    # Парсим правильные ответы
    if question_type == "one":
        # Для типа "one" outputs - это один номер (строка или число)
        try:
            correct_indices = [int(outputs.strip())]
        except (ValueError, AttributeError):
            return question_data
    elif question_type == "multi":
        # Для типа "multi" outputs - это номера через запятую без пробелов
        try:
            correct_indices = [int(x.strip()) for x in outputs.split(",") if x.strip()]
        except (ValueError, AttributeError):
            return question_data
    else:
        return question_data
    
    # Получаем правильные значения ответов (индексы в options начинаются с 0, а номера option_* с 1)
    correct_values = []
    for idx in correct_indices:
        if 1 <= idx <= len(options):
            correct_values.append(options[idx - 1])
    
    if not correct_values:
        return question_data
    
    # Перемешиваем варианты ответа
    shuffled_options = options.copy()
    random.shuffle(shuffled_options)
    
    for i in range(1, 10):
        question_data[f"option_{i}"] = None
    
    for i, value in enumerate(shuffled_options, start=1):
        question_data[f"option_{i}"] = value
    
    new_correct_indices = []
    for i, value in enumerate(shuffled_options, start=1):
        if value in correct_values:
            new_correct_indices.append(i)
    
    if question_type == "one":
        if new_correct_indices:
            question_data["outputs"] = new_correct_indices[0]
    elif question_type == "multi":
        if new_correct_indices:
            new_correct_indices.sort()
            question_data["outputs"] = ",".join(map(str, new_correct_indices))
    
    return question_data


MAX_REFINE_RETRIES = 2


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
        builder.add_edge("refine_question", "validation")

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
        vr = state.get("validation_result", {})
        if vr.get("passed", True):
            return "end"
        if state.get("retry_count", 0) >= MAX_REFINE_RETRIES:
            return "end"
        return "refine"

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
                "chunk_gate_result": {
                    "passed": True,
                    "rejection_reason": "gate_disabled_by_pipeline_mode",
                },
            }

        if state.get("chunk_pre_validated"):
            return {**state, "pipeline_mode": pipeline_mode, "chunk_rejected": False, "chunk_gate_result": {}}

        if self.chunk_gate_chain is None:
            return {**state, "pipeline_mode": pipeline_mode, "chunk_rejected": False, "chunk_gate_result": {}}

        result = self.chunk_gate_chain.invoke({
            "chunk": state["chunk"],
            "question_type": state["question_type"],
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
            "source": state.get("source", "")
        }
        # Добавляем язык, если он указан в состоянии
        if "language" in state and state.get("language"):
            input_data["language"] = state["language"]
        
        result = self.generate_question_chain.invoke(input_data)
        
        # Перемешиваем варианты ответа после генерации
        result = shuffle_answer_options(result, state["question_type"])
        
        return {
            **state, 
            "generated_question": result
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
            "generated_question": generated_question
            })
        
        # Убеждаемся, что result - это объект ProvocativenessOutput
        if isinstance(result, dict):
            provocativeness_score = result.get('provocativeness_score', 0)
            explanation = result.get('explanation', '')
        else:
            provocativeness_score = result.provocativeness_score
            explanation = result.explanation
            
        return {
            **state, 
            "sensitivity_score": {'provocativeness_score': provocativeness_score, 'explanation': explanation}
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
            "generated_question": generated_question
        })
        
        # Убеждаемся, что result - это объект DifficultyOutput
        if isinstance(result, dict):
            difficulty = result.get('difficulty', 0)
            explanation = result.get('explanation', '')
        else:
            difficulty = result.difficulty
            explanation = result.explanation
            
        return {
            **state,
            "difficulty_score": {
                "difficulty": difficulty,
                "explanation": explanation
            }
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
        
        result = self.validation_chain.invoke({
            "question_type": state["question_type"],
            "source_text": generated_question.get("source_text", ""),
            "question": {k: v for k, v in generated_question.items() if k != "source_text" and v is not None}
        })
        return {
            **state, 
            "validation_result": {
                "type": result.type,
                "by_block": result.by_block,
                "justifications": result.justifications,
                "total": result.total,
                "max_total": result.max_total,
                "threshold": result.threshold,
                "passed": result.passed
            }
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

        result = self.refine_question_chain.invoke({
            "question_type": state["question_type"],
            "source_text": generated_question.get("source_text", ""),
            "question": {k: v for k, v in generated_question.items() if k != "source_text" and v is not None},
            "validation_result": state.get("validation_result", {}),
        })

        result = shuffle_answer_options(result, state["question_type"])

        return {
            **state,
            "generated_question": result,
            "retry_count": state.get("retry_count", 0) + 1,
        }

    # def search_milvus_node(self, state: AgentState) -> AgentState:
    #     """Поиск в Milvus"""
    #     result = self.retriever_chain.invoke({
    #         "query": state["generated_question"]
    #     })
    #     return {
    #         **state, 
    #         "milvus_results": result.search_result
    #     }
        
    # def format_output_node(self, state: AgentState) -> AgentState:
    #     """Форматирование вывода"""
    #     result = self.format_output_chain.invoke({
    #         "generated_question": state["generated_question"],
    #         "milvus_results": state["milvus_results"],
    #         "sensitivity_score": state["sensitivity_score"]
    #     })
    #     return {
    #         **state, 
    #         "final_json": result.final_json
    #     }

    # ────────────────────────────────────────────────────────────────────
    # PLACEHOLDER: chunk_transformer_node — будущая замена chunk_gate.
    #
    # Трансформер будет спрашивать у LLM:
    #   - сколько вопросов имеет смысл генерировать по этому чанку
    #   - по каким модальностям (one / multi / open)
    # Промпт и детали реализации — будут добавлены позже.
    #
    # Пример будущего state:
    #   chunk_plan: Optional[dict]  # {"suggested_counts": {"one": 2, "multi": 1}, "modalities": ["one","multi"]}
    #
    # def chunk_transformer_node(self, state: AgentState) -> AgentState:
    #     """Анализ чанка: сколько вопросов и по каким модальностям генерировать.
    #
    #     Заменит chunk_gate_node — вместо бинарного pass/fail
    #     возвращает рекомендацию по количеству и типам вопросов.
    #     """
    #     if self.chunk_transformer_chain is None:
    #         return {**state, "chunk_plan": None}
    #
    #     result = self.chunk_transformer_chain.invoke({
    #         "chunk": state["chunk"],
    #         "question_type": state["question_type"],
    #     })
    #     plan = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    #     return {**state, "chunk_plan": plan}
    # ────────────────────────────────────────────────────────────────────