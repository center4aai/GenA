from typing import TypedDict, Optional, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.messages import SystemMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser

from agent.nodes.assess_difficulty.prompt_difficulty import DIFFICULTY_PROMPT
from config import LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY



class StructuredQuestionOutput(BaseModel):
    task: str = Field(description="Текст вопроса.")
    text: Optional[str] = Field(default=None, description="Дополнительный контекст вопроса.")
    option_1: Optional[str] = Field(default=None, description="Вариант ответа 1.")
    option_2: Optional[str] = Field(default=None, description="Вариант ответа 2.")
    option_3: Optional[str] = Field(default=None, description="Вариант ответа 3.")
    option_4: Optional[str] = Field(default=None, description="Вариант ответа 4.")
    option_5: Optional[str] = Field(default="None", description="Вариант ответа 5.")
    option_6: Optional[str] = Field(default="None", description="Вариант ответа 6.")
    option_7: Optional[str] = Field(default="None", description="Вариант ответа 7.")
    option_8: Optional[str] = Field(default="None", description="Вариант ответа 8.")
    option_9: Optional[str] = Field(default="None", description="Вариант ответа 9.")
    outputs: Union[int, str] = Field(
        description="Правильный ответ: для типов 'one' - номер варианта (1-9), 'multi' - номера через запятую без пробелов (например, '1,3'), 'open' - текст ответа."
    )
class DifficultyInput(TypedDict):
    generated_question: StructuredQuestionOutput


class DifficultyOutput(BaseModel):
    difficulty: int = Field(ge=1, le=3)
    explanation: str


def create_difficulty_chain() -> Runnable[DifficultyInput, DifficultyOutput]:
    system_prompt = PromptTemplate.from_template(DIFFICULTY_PROMPT)

    llm = ChatOpenAI(
        model=LLM_MODEL_NAME,
        openai_api_base=LLM_URL_MODEL,
        openai_api_key=LLM_API_KEY,
        temperature=0.0,
        max_retries=3,
        stream=False,
        timeout=30,
        max_tokens=256,
    )

    parser = JsonOutputParser(pydantic_object=DifficultyOutput)

    class DifficultyRunnable(Runnable[DifficultyInput, DifficultyOutput]):
        def invoke(self, input_data: DifficultyInput) -> DifficultyOutput:
            question_data = input_data['generated_question']
            question_data = StructuredQuestionOutput(**question_data)

            prompt = system_prompt.format(
                question=question_data.task,
                context=question_data.text if question_data.text else "Нет дополнительного контекста"
            )

            chat_prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=prompt)
            ])

            chain = chat_prompt | llm | parser
            result = chain.invoke({})
            return DifficultyOutput(**result)

    return DifficultyRunnable()