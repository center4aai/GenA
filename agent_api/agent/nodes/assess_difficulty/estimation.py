import logging
from typing import TypedDict, Optional

from pydantic import BaseModel, Field, ValidationError
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable

from llm_factory import TRANSIENT_LLM_ERRORS, create_chat_llm
from agent.nodes.assess_difficulty.prompt_difficulty import DIFFICULTY_PROMPT
from agent.schemas.question import (
    StructuredQuestionOutput,
    serialize_question_options,
    fence_user_content,
    format_question_context,
)
from agent.llm_parsing import parse_assessment_model

logger = logging.getLogger(__name__)


class DifficultyInput(TypedDict):
    generated_question: StructuredQuestionOutput
    source_text: Optional[str]


class DifficultyOutput(BaseModel):
    # None + assessment_failed=True — оценка не состоялась (парсинг, невалидный
    # вход, нетранзиентная ошибка LLM). Раньше сюда подставлялась «1» и воркер
    # сохранял её как настоящую оценку.
    difficulty: Optional[int] = Field(default=None, ge=1, le=3)
    explanation: Optional[str] = None
    assessment_failed: bool = False
    assessment_error: Optional[str] = None


def _failed_assessment(error: str) -> DifficultyOutput:
    return DifficultyOutput(
        difficulty=None,
        explanation=None,
        assessment_failed=True,
        assessment_error=str(error)[:300],
    )


def create_difficulty_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
) -> Runnable[DifficultyInput, DifficultyOutput]:
    system_prompt = PromptTemplate.from_template(DIFFICULTY_PROMPT)

    llm = create_chat_llm(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        max_tokens=256,
        **provider_kwargs,
    )

    # Используется parse_assessment_model как источник значений по умолчанию:
    # score None означает «поле отсутствует», а не подставленную оценку.
    fallback = _failed_assessment("score missing in LLM response")

    class DifficultyRunnable(Runnable[DifficultyInput, DifficultyOutput]):
        def invoke(self, input_data: DifficultyInput) -> DifficultyOutput:
            raw_question = input_data["generated_question"]
            try:
                if isinstance(raw_question, StructuredQuestionOutput):
                    question_data = raw_question
                else:
                    question_data = StructuredQuestionOutput(**raw_question)
            except (ValidationError, TypeError, ValueError) as exc:
                logger.warning("Invalid question payload for difficulty assessment: %s", exc)
                return _failed_assessment(f"invalid question payload: {exc}")

            source_text = (
                (input_data.get("source_text") or "").strip()
                or (question_data.source_text or "").strip()
            )
            options_text = serialize_question_options(question_data)

            prompt = system_prompt.format(
                question=question_data.task,
                context=format_question_context(question_data.text),
                options=options_text,
                source_text=fence_user_content(
                    source_text or "(не указан)", "SOURCE_TEXT"
                ),
            )

            chat_prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=prompt),
                HumanMessage(content="Выполни оценку сложности."),
            ])

            try:
                response = (chat_prompt | llm).invoke({})
                raw = response.content if hasattr(response, "content") else str(response)
            except TRANSIENT_LLM_ERRORS:
                # Таймаут/503/429/auth — не вердикт по содержанию; пусть долетит
                # до API и станет 5xx/401, чтобы очередь повторила задачу.
                raise
            except Exception as exc:
                logger.warning("Difficulty assessment LLM call failed: %s", exc)
                return _failed_assessment(f"llm call failed: {exc}")

            parsed = parse_assessment_model(
                raw,
                DifficultyOutput,
                "difficulty",
                fallback,
                score_aliases=("score", "difficulty_level", "level"),
            )
            if parsed.difficulty is None:
                logger.warning("Difficulty assessment returned no usable score")
                return fallback
            return parsed

    return DifficultyRunnable()
