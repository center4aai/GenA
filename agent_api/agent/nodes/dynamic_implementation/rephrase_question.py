# agent/chains/rephrase_question.py

from typing import List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import PydanticOutputParser

import logging
from config import LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RephrasedQuestionOutput(BaseModel):
    rephrased_question: str = Field(description="Переформулированный вопрос, сохраняющий исходный смысл.")
    original_question: str = Field(default="", description="Оригинальный вопрос для справки.")


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


def _get_rephrase_chain():
    llm = ChatOpenAI(
        model=LLM_MODEL_NAME,
        temperature=0.0,
        openai_api_base=LLM_URL_MODEL,
        openai_api_key=LLM_API_KEY,
        max_retries=3,
        stream=False,
        timeout=30,
        max_tokens=256
    )

    parser = PydanticOutputParser(pydantic_object=RephrasedQuestionOutput)
    
    # Экранируем фигурные скобки в инструкциях парсера!
    format_instructions = _escape_curly_braces(parser.get_format_instructions())
    
    system_prompt = f"{SYSTEM_PROMPT_REPHRASE}\n\n{format_instructions}"

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Переформулируй следующий вопрос:\n\nВопрос: {question}\n\nПереформулированный вопрос:")
    ])

    return prompt | llm | parser

def rephrase_question(question: str) -> str:
    try:
        logger.info(f"Rephrasing question: {question[:60]}...")
        chain = _get_rephrase_chain()
        result: RephrasedQuestionOutput = chain.invoke({"question": question})
        logger.info(f"Rephrased to: {result.rephrased_question[:60]}...")
        return result.rephrased_question
    except Exception as e:
        logger.error(f"Error rephrasing question: {str(e)}")
        return question


def rephrase_questions(questions: List[str]) -> List[str]:
    return [rephrase_question(q) for q in questions]