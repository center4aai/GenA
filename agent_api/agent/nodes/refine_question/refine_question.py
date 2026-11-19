"""
Нода доработки вопросов, не прошедших валидацию.

Принимает вопрос + результат валидации с обоснованиями,
формирует промпт для LLM с конкретными замечаниями и
возвращает исправленный вопрос в том же формате.
"""

import json
import logging
from typing import Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from agent.llm_parsing import parse_pydantic_output
from agent.nodes.generate_question.generate_question import (
    _SUPPORTED_LANGUAGES,
    detect_language,
    get_language_instruction,
)
from agent.nodes.generate_question.system_prompt import KIND_INSTRUCTIONS, block_instructions_for
from agent.nodes.normalize_output import normalize_question_output
from agent.schemas.question import StructuredQuestionOutput, fence_user_content
from llm_factory import TRANSIENT_LLM_ERRORS, create_chat_llm
from agent.document_kinds import normalize_document_kind

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — эксперт по составлению заданий (тестовых вопросов).\n"
    "Тебе дан вопрос, который не прошёл проверку качества.\n"
    "Ниже перечислены критерии, по которым вопрос получил оценку 0, "
    "и обоснования проблем от рецензента.\n\n"
    "Твоя задача — **исправить задание**, устранив каждую из указанных проблем, "
    "и вернуть результат **строго** в том же JSON-формате, что и входное задание.\n"
    "Не меняй то, что и так хорошо. Сосредоточься только на указанных проблемах.\n"
    "Текст вопроса и ответов должен оставаться на том же языке, что и оригинал."
)

_TYPE_SHAPE_INSTRUCTIONS = {
    "open": (
        "Тип задания: open (открытый ответ). "
        "Поля option_1–option_9 должны быть null. "
        "Поле outputs — текст правильного ответа."
    ),
    "one": (
        "Тип задания: one (один правильный вариант). "
        "Заполни option_1–option_4, outputs — номер единственного правильного варианта (1–4)."
    ),
    "multi": (
        "Тип задания: multi (несколько правильных вариантов). "
        "Заполни option_1–option_6, outputs — номера правильных вариантов через запятую без пробелов."
    ),
}


class RefineQuestionInput(TypedDict):
    block_type: Optional[str]  # body | list | inset | caption | glossary_entry
    question_type: Literal["open", "one", "multi"]
    source_text: Optional[str]
    question: Dict[str, Any]
    validation_result: Dict[str, Any]
    language: Optional[str]
    document_kind: Optional[str]


def _build_issues_section(validation_result: Dict[str, Any]) -> str:
    """Формирует текстовый список проблем из by_block + justifications."""
    by_block = validation_result.get("by_block", {})
    justifications = validation_result.get("justifications", {})
    lines: List[str] = []
    for block_key, scores in by_block.items():
        block_justs = justifications.get(block_key, [])
        for i, score in enumerate(scores):
            if score == 0:
                reason = block_justs[i] if i < len(block_justs) and block_justs[i] else "нет пояснения"
                lines.append(f"- [{block_key}][{i}] оценка 0 — {reason}")
    return "\n".join(lines) if lines else "Конкретные замечания не указаны."


def create_refine_question_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
) -> Runnable[RefineQuestionInput, Dict]:

    llm = create_chat_llm(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        max_tokens=1024,
        **provider_kwargs,
    )

    parser = PydanticOutputParser(pydantic_object=StructuredQuestionOutput)

    def _keep_original(question: Dict[str, Any], source_text, question_type: str, error: str) -> Dict:
        """Исходный вопрос с явным маркером: доработка не состоялась.

        Граф по ``refine_failed`` завершает работу, а не прогоняет тот же
        вопрос через оценки и валидацию ещё раз.
        """
        out = normalize_question_output(dict(question), question_type)
        out["source_text"] = source_text
        out["refine_failed"] = True
        out["refine_error"] = str(error)[:300]
        return out

    class RefineQuestionRunnable(Runnable[RefineQuestionInput, Dict]):
        def invoke(self, input_data: RefineQuestionInput, config=None) -> Dict:
            question = input_data["question"]
            source_text = input_data.get("source_text", "")
            validation_result = input_data["validation_result"]
            question_type = input_data["question_type"]

            requested_language = (input_data.get("language") or "").strip().lower()
            if requested_language in _SUPPORTED_LANGUAGES:
                language = requested_language
            elif source_text:
                language = detect_language(source_text)
            elif question.get("task"):
                language = detect_language(str(question["task"]))
            else:
                language = "ru"
            language_instruction = get_language_instruction(language, question_type)
            shape_instruction = _TYPE_SHAPE_INSTRUCTIONS.get(
                question_type, _TYPE_SHAPE_INSTRUCTIONS["one"]
            )

            format_instructions = parser.get_format_instructions()
            if language == "tg":
                format_instructions = (
                    "⚠️ КРИТИЧЕСКИ ВАЖНО: ВСЕ ПОЛЯ (task, option_1, option_2, и т.д.) "
                    "ДОЛЖНЫ БЫТЬ НА ЗАБОНИ ТОҶИКӢ! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"
                    + format_instructions
                )
            elif language == "be":
                format_instructions = (
                    "⚠️ КРИТИЧЕСКИ ВАЖНО: ВСЕ ПОЛЯ (task, option_1, option_2, и т.д.) "
                    "ДОЛЖНЫ БЫТЬ НА БЕЛАРУСКАЙ МОВЕ! НЕ ИСПОЛЬЗУЙТЕ РУССКИЙ ЯЗЫК!\n\n"
                    + format_instructions
                )

            issues = _build_issues_section(validation_result)
            question_json = json.dumps(question, ensure_ascii=False, indent=2)
            fenced_source = fence_user_content(source_text or "(не указан)", "SOURCE_TEXT")

            document_kind = normalize_document_kind(input_data.get("document_kind"))
            kind_block = (KIND_INSTRUCTIONS.get(document_kind) or "").strip()
            if document_kind in ("textbook", "encyclopedia"):
                # Same block-type hint the generator used, or the refiner would
                # grade the rewrite under different rules.
                block_block = block_instructions_for(input_data.get("block_type"))
                if block_block:
                    kind_block = f"{kind_block}\n\n{block_block}" if kind_block else block_block
            kind_section = (
                f"### Инструкции по типу документа\n{kind_block}\n\n"
                if kind_block
                else ""
            )

            human_text = (
                f"### Замечания рецензента\n{issues}\n\n"
                f"### Тип задания\n{shape_instruction}\n\n"
                f"### Язык\n{language_instruction}\n\n"
                f"{kind_section}"
                f"### Исходный текст\n{fenced_source}\n\n"
                f"### Текущее задание (JSON)\n{question_json}\n\n"
                f"Исправь задание и верни результат, сохраняя форму типа '{question_type}'.\n\n"
                f"{format_instructions}"
            )

            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ])

            try:
                response = (prompt | llm).invoke({})
                raw = response.content if hasattr(response, "content") else str(response)
            except TRANSIENT_LLM_ERRORS:
                # Таймаут/503/429/auth — пусть долетит до API и станет 5xx/401.
                raise
            except Exception as exc:
                logger.warning("Refine LLM call failed, keeping original question: %s", exc)
                return _keep_original(question, source_text, question_type, f"llm call failed: {exc}")

            try:
                result = parse_pydantic_output(raw, StructuredQuestionOutput)
                out = result.model_dump()
            except Exception as parse_err:
                logger.warning("Failed to parse refined question, keeping original: %s", parse_err)
                return _keep_original(question, source_text, question_type, f"parse failed: {parse_err}")
            out = normalize_question_output(out, question_type)
            out["source_text"] = source_text
            logger.info(
                "Refined question (score was %s/%s)",
                validation_result.get("total", "?"),
                validation_result.get("max_total", "?"),
            )
            return out

    return RefineQuestionRunnable()
