from typing import Literal, Dict, Optional, TypedDict, List, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
import json

from agent.nodes.assess_sensitivity.system_prompt import PROVOCATIVENESS_PROMPT
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
class ProvocativenessInput(TypedDict):
    generated_question: StructuredQuestionOutput

class ProvocativenessOutput(BaseModel):
    provocativeness_score: int = Field(
        description="Оценка провокационности вопроса по 3-балльной шкале",
        ge=1, le=3
    )
    explanation: str = Field(
        description="Краткое объяснение оценки провокационности"
    )

def create_provocativeness_chain() -> Runnable[ProvocativenessInput, ProvocativenessOutput]:

    
    system_prompt = PromptTemplate.from_template(PROVOCATIVENESS_PROMPT)
    
    llm = ChatOpenAI(
        model=LLM_MODEL_NAME,
        openai_api_base=LLM_URL_MODEL,
        openai_api_key=LLM_API_KEY,
        temperature=0.0,
        max_retries=3,
        stream=False,  # Отключаем стриминг для получения полного ответа сразу
        timeout=30,    # Увеличиваем timeout для больших промптов
        max_tokens=256  # Ограничиваем длину ответа для ускорения
    )

    parser = JsonOutputParser(pydantic_object=StructuredQuestionOutput)


    class ProvocativenessRunnable(Runnable[ProvocativenessInput, ProvocativenessOutput]):
        def invoke(self, input_data: ProvocativenessInput) -> ProvocativenessOutput:
            question_data = input_data['generated_question']
            question_data = StructuredQuestionOutput(**question_data)
        
            prompt = system_prompt.format(
                question=question_data.task,
                context=question_data.text if question_data.text else "Нет дополнительного контекста"
            )
                        # Создаем цепочку обработки
            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=prompt)
            ])
            chain =  prompt | llm | parser
            result = chain.invoke({})
            return ProvocativenessOutput(**result)
    
    return ProvocativenessRunnable()