# agent/chains/rephrase_question.py

from typing import List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

import logging
from llm_factory import create_chat_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

PROMPT_TEMPLATE_REPHRASE = """Переформулируй следующий вопрос:

Вопрос: {question}

Переформулированный вопрос:"""


def _escape_curly_braces(text: str) -> str:
    """Экранирует фигурные скобки для использования в f-строках и LangChain шаблонах."""
    return text.replace("{", "{{").replace("}", "}}")


def _get_rephrase_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
):
    llm = create_chat_llm(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        max_tokens=256,
        timeout=90,
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


def rephrase_question(
    question: str,
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
) -> str:
    try:
        logger.info(f"Rephrasing question: {question[:60]}...")
        chain = _get_rephrase_chain(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            **provider_kwargs,
        )
        result: RephrasedQuestionOutput = chain.invoke({"question": question})
        logger.info(f"Rephrased to: {result.rephrased_question[:60]}...")
        return result.rephrased_question
    except Exception as e:
        logger.error(f"Error rephrasing question: {str(e)}")
        return question


def rephrase_questions(
    questions: List[str],
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
) -> List[str]:
    return [
        rephrase_question(
            q, model_name=model_name, base_url=base_url, api_key=api_key,
            provider=provider, **provider_kwargs,
        )
        for q in questions
    ]
