from typing import TypedDict, Optional, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.output_parsers import JsonOutputParser

from llm_factory import create_chat_llm
from agent.nodes.assess_difficulty.prompt_difficulty import DIFFICULTY_PROMPT


class StructuredQuestionOutput(BaseModel):
    task: str = Field(description="Текст вопроса.")
    text: Optional[str] = Field(
        default=None, description="Дополнительный контекст вопроса."
    )
    option_1: Optional[str] = Field(default=None, description="Вариант ответа 1.")
    option_2: Optional[str] = Field(default=None, description="Вариант ответа 2.")
    option_3: Optional[str] = Field(default=None, description="Вариант ответа 3.")
    option_4: Optional[str] = Field(default=None, description="Вариант ответа 4.")
    option_5: Optional[str] = Field(default=None, description="Вариант ответа 5.")
    option_6: Optional[str] = Field(default=None, description="Вариант ответа 6.")
    option_7: Optional[str] = Field(default=None, description="Вариант ответа 7.")
    option_8: Optional[str] = Field(default=None, description="Вариант ответа 8.")
    option_9: Optional[str] = Field(default=None, description="Вариант ответа 9.")
    outputs: Union[int, str] = Field(
        description="Правильный ответ: для типов 'one' - номер варианта (1-9), 'multi' - номера через запятую без пробелов (например, '1,3'), 'open' - текст ответа."
    )


class DifficultyInput(TypedDict):
    generated_question: StructuredQuestionOutput


class DifficultyOutput(BaseModel):
    difficulty: int = Field(ge=1, le=3)
    explanation: str


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
        timeout=90,
        **provider_kwargs,
    )

    parser = JsonOutputParser(pydantic_object=DifficultyOutput)

    class DifficultyRunnable(Runnable[DifficultyInput, DifficultyOutput]):
        def invoke(self, input_data: DifficultyInput) -> DifficultyOutput:
            question_data = input_data["generated_question"]
            question_data = StructuredQuestionOutput(**question_data)

            prompt = system_prompt.format(
                question=question_data.task,
                context=(
                    question_data.text
                    if question_data.text
                    else "Нет дополнительного контекста"
                ),
            )

            chat_prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=prompt),
                HumanMessage(content="Выполни оценку сложности."),
            ])

            chain = chat_prompt | llm | parser
            result = chain.invoke({})
            # JsonOutputParser может вернуть словарь или объект Pydantic модели
            if isinstance(result, dict):
                return DifficultyOutput(**result)
            elif isinstance(result, DifficultyOutput):
                return result
            else:
                # Если результат - это что-то другое, пытаемся преобразовать
                if hasattr(result, "model_dump"):
                    return DifficultyOutput(**result.model_dump())
                elif hasattr(result, "dict"):
                    return DifficultyOutput(**result.dict())
                else:
                    raise ValueError(
                        f"Unexpected result type: {type(result)}, value: {result}"
                    )

    return DifficultyRunnable()
