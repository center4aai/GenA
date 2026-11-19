# agent/nodes/dynamic_implementation/rephrase_question.py

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

import logging
from llm_factory import TRANSIENT_LLM_ERRORS, create_chat_llm

logger = logging.getLogger(__name__)

_REPHRASE_CHAIN_CACHE: Dict[Tuple[Any, ...], Any] = {}

VALIDATION_RESULT_KEYS = (
    "validation_passed",
    "validation_score",
    "validation_threshold",
    "validation_details",
    "validation_justifications",
    "retry_count",
)


# Инфраструктурные ошибки LLM (таймаут, недоступен, 429/5xx, auth) не глотаем:
# API-слой отдаёт по ним различимый статус, очередь ретраит.
_TRANSIENT_LLM_ERRORS = TRANSIENT_LLM_ERRORS


class RephrasedQuestionOutput(BaseModel):
    rephrased_question: str = Field(
        description="Переформулированный вопрос, сохраняющий исходный смысл."
    )
    original_question: str = Field(
        default="", description="Оригинальный вопрос для справки."
    )


SYSTEM_PROMPT_REPHRASE = """Ты — эксперт по переформулированию вопросов. Твоя задача — перефразировать входящий вопрос, сохранив его смысл, но изменив формулировку, структуру или стиль.

Правила:
- Не меняй суть вопроса.
- Не добавляй новую информацию.
- Не удаляй ключевые детали.
- Используй синонимы, изменяй порядок слов, меняй грамматическую конструкцию.
- Ответ должен быть в формате JSON, строго соответствующем схеме.
"""

def _escape_curly_braces(text: str) -> str:
    """Экранирует фигурные скобки для использования в f-строках и LangChain шаблонах."""
    return text.replace("{", "{{").replace("}", "}}")


def _rephrase_chain_cache_key(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    provider_kwargs: Tuple[Tuple[str, Any], ...] = (),
) -> Tuple[Any, ...]:
    return (model_name, base_url, api_key, provider, timeout, provider_kwargs)


def _build_rephrase_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    **provider_kwargs,
):
    timeout = provider_kwargs.pop("timeout", timeout)
    llm = create_chat_llm(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        max_tokens=256,
        timeout=timeout,
        **provider_kwargs,
    )

    parser = PydanticOutputParser(pydantic_object=RephrasedQuestionOutput)

    # Экранируем фигурные скобки в инструкциях парсера!
    format_instructions = _escape_curly_braces(parser.get_format_instructions())

    system_prompt = f"{SYSTEM_PROMPT_REPHRASE}\n\n{format_instructions}"

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            (
                "human",
                "Переформулируй следующий вопрос:\n\nВопрос: {question}\n\nПереформулированный вопрос:",
            ),
        ]
    )

    return prompt | llm | parser


def _get_rephrase_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    **provider_kwargs,
):
    cache_key = _rephrase_chain_cache_key(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        provider=provider,
        timeout=timeout,
        provider_kwargs=tuple(sorted(provider_kwargs.items())),
    )
    if cache_key not in _REPHRASE_CHAIN_CACHE:
        _REPHRASE_CHAIN_CACHE[cache_key] = _build_rephrase_chain(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            timeout=timeout,
            **dict(provider_kwargs),
        )
    return _REPHRASE_CHAIN_CACHE[cache_key]


def _mark_needs_revalidation(question: Dict[str, Any]) -> None:
    question["needs_revalidation"] = True
    for key in VALIDATION_RESULT_KEYS:
        question.pop(key, None)


def rephrase_question(
    question: str,
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    **provider_kwargs,
) -> str:
    text = question or ""
    try:
        logger.info(f"Rephrasing question: {text[:60]}...")
        chain = _get_rephrase_chain(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            timeout=timeout,
            **provider_kwargs,
        )
        result: RephrasedQuestionOutput = chain.invoke({"question": text})
        rephrased = result.rephrased_question or text
        logger.info(f"Rephrased to: {rephrased[:60]}...")
        return rephrased
    except _TRANSIENT_LLM_ERRORS:
        raise
    except Exception as e:
        logger.error(f"Error rephrasing question: {str(e)}")
        return text


def rephrase_questions(
    questions: List[str],
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    **provider_kwargs,
) -> List[str]:
    return [
        rephrase_question(
            q, model_name=model_name, base_url=base_url, api_key=api_key,
            provider=provider, timeout=timeout, **provider_kwargs,
        )
        for q in questions
    ]


def rephrase_questions_dicts(
    questions: List[Dict[str, Any]],
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    timeout: Optional[int] = None,
    **provider_kwargs,
) -> List[Dict[str, Any]]:
    """Rephrase question dicts in place and clear stale validation fields."""
    for question in questions:
        original = question.get("task") or ""
        rephrased = rephrase_question(
            original,
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            timeout=timeout,
            **provider_kwargs,
        )
        question["task"] = rephrased
        if rephrased != original:
            _mark_needs_revalidation(question)
    return questions
