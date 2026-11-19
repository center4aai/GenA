import logging
from typing import Optional, TypedDict

from pydantic import BaseModel, Field, ValidationError
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable

from llm_factory import TRANSIENT_LLM_ERRORS, create_chat_llm
from agent.nodes.assess_sensitivity.system_prompt import PROVOCATIVENESS_PROMPT
from agent.schemas.question import (
    StructuredQuestionOutput,
    serialize_question_options,
    fence_user_content,
    format_question_context,
)
from agent.llm_parsing import parse_assessment_model

logger = logging.getLogger(__name__)


class ProvocativenessInput(TypedDict):
    generated_question: StructuredQuestionOutput
    source_text: Optional[str]


class ProvocativenessOutput(BaseModel):
    # None + assessment_failed=True — оценка не состоялась (парсинг, невалидный
    # вход, нетранзиентная ошибка LLM). Раньше сюда подставлялась fail-closed
    # «3», и воркер сохранял её как настоящую оценку.
    provocativeness_score: Optional[int] = Field(
        default=None,
        description="Оценка провокационности вопроса по 3-балльной шкале",
        ge=1,
        le=3,
    )
    explanation: Optional[str] = Field(
        default=None, description="Краткое объяснение оценки провокационности"
    )
    assessment_failed: bool = False
    assessment_error: Optional[str] = None


def _failed_assessment(error: str) -> ProvocativenessOutput:
    return ProvocativenessOutput(
        provocativeness_score=None,
        explanation=None,
        assessment_failed=True,
        assessment_error=str(error)[:300],
    )


def create_provocativeness_chain(
    model_name: str = None,
    base_url: str = None,
    api_key: str = None,
    provider: str = "openai",
    **provider_kwargs,
) -> (
    Runnable[ProvocativenessInput, ProvocativenessOutput]
):

    system_prompt = PromptTemplate.from_template(PROVOCATIVENESS_PROMPT)

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

    class ProvocativenessRunnable(
        Runnable[ProvocativenessInput, ProvocativenessOutput]
    ):
        def invoke(self, input_data: ProvocativenessInput) -> ProvocativenessOutput:
            raw_question = input_data["generated_question"]
            try:
                if isinstance(raw_question, StructuredQuestionOutput):
                    question_data = raw_question
                else:
                    question_data = StructuredQuestionOutput(**raw_question)
            except (ValidationError, TypeError, ValueError) as exc:
                logger.warning("Invalid question payload for sensitivity assessment: %s", exc)
                return _failed_assessment(f"invalid question payload: {exc}")

            source_text = (
                (input_data.get("source_text") or "").strip()
                or (question_data.source_text or "").strip()
            )
            options_text = serialize_question_options(question_data)

            system_content = system_prompt.format(
                question=question_data.task,
                context=format_question_context(question_data.text),
                options=options_text,
                source_text=fence_user_content(
                    source_text or "(не указан)", "SOURCE_TEXT"
                ),
            )
            chat_prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=system_content),
                HumanMessage(content="Выполни оценку провокационности."),
            ])
            try:
                response = (chat_prompt | llm).invoke({})
                raw = response.content if hasattr(response, "content") else str(response)
            except TRANSIENT_LLM_ERRORS:
                # Таймаут/503/429/auth — не вердикт по содержанию; пусть долетит
                # до API и станет 5xx/401, чтобы очередь повторила задачу.
                raise
            except Exception as exc:
                logger.warning("Sensitivity assessment LLM call failed: %s", exc)
                return _failed_assessment(f"llm call failed: {exc}")

            parsed = parse_assessment_model(
                raw,
                ProvocativenessOutput,
                "provocativeness_score",
                fallback,
                score_aliases=("provocativeness", "sensitivity", "sensitivity_level"),
            )
            if parsed.provocativeness_score is None:
                logger.warning("Sensitivity assessment returned no usable score")
                return fallback
            return parsed

    return ProvocativenessRunnable()
