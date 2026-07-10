"""
Нода доработки вопросов, не прошедших валидацию.

Принимает вопрос + результат валидации с обоснованиями,
формирует промпт для LLM с конкретными замечаниями и
возвращает исправленный вопрос в том же формате.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from llm_factory import create_chat_llm

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


class RefineQuestionInput(TypedDict):
    question_type: Literal["open", "one", "multi"]
    source_text: Optional[str]
    question: Dict[str, Any]
    validation_result: Dict[str, Any]


class StructuredQuestionOutput(BaseModel):
    task: str = Field(description="Текст вопроса.")
    text: Optional[str] = Field(default=None, description="Дополнительный контекст.")
    option_1: Optional[str] = Field(default=None)
    option_2: Optional[str] = Field(default=None)
    option_3: Optional[str] = Field(default=None)
    option_4: Optional[str] = Field(default=None)
    option_5: Optional[str] = Field(default=None)
    option_6: Optional[str] = Field(default=None)
    option_7: Optional[str] = Field(default=None)
    option_8: Optional[str] = Field(default=None)
    option_9: Optional[str] = Field(default=None)
    outputs: Union[int, str] = Field(description="Правильный ответ.")
    source_text: Optional[str] = Field(default=None)


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
        timeout=90,
        **provider_kwargs,
    )

    parser = PydanticOutputParser(pydantic_object=StructuredQuestionOutput)

    class RefineQuestionRunnable(Runnable[RefineQuestionInput, Dict]):
        def invoke(self, input_data: RefineQuestionInput, config=None) -> Dict:
            question = input_data["question"]
            source_text = input_data.get("source_text", "")
            validation_result = input_data["validation_result"]

            issues = _build_issues_section(validation_result)
            question_json = json.dumps(question, ensure_ascii=False, indent=2)

            human_text = (
                f"### Замечания рецензента\n{issues}\n\n"
                f"### Исходный текст\n{source_text or '(не указан)'}\n\n"
                f"### Текущее задание (JSON)\n{question_json}\n\n"
                f"Исправь задание и верни результат.\n\n"
                f"{parser.get_format_instructions()}"
            )

            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ])

            chain = prompt | llm | parser
            result = chain.invoke({})
            out = result.model_dump()
            out["source_text"] = source_text
            logger.info(
                "Refined question (score was %s/%s)",
                validation_result.get("total", "?"),
                validation_result.get("max_total", "?"),
            )
            return out

    return RefineQuestionRunnable()
