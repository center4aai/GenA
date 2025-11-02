from typing import Literal, Dict, Optional, TypedDict, List, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
import logging
from agent.nodes.generate_question.system_prompt import PROMPT_TEMPLATE_ONE, PROMPT_TEMPLATE_MULTI, PROMPT_TEMPLATE_OPEN, SYSTEM_PROMPT_ONE, SYSTEM_PROMPT_MULTI, SYSTEM_PROMPT_OPEN
from config import LLM_MODEL_NAME, LLM_URL_MODEL, LLM_API_KEY
from langchain_core.output_parsers import PydanticOutputParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


QuestionType = Literal["one", "multi", "open"]

class GenerateQuestionInput(TypedDict):
    input_text: str
    question_type: QuestionType

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
        description="Правильный ответ: для типов 'one' - номер варианта (1-9), 'multi' - номера через запятую без пробелов, 'open' - текст ответа."
    ) 
    source_text: Optional[str] = Field(default=None, description="Исходный текст, по которому был сгенерирован вопрос.")

def create_generate_question_chain() -> Runnable[GenerateQuestionInput, StructuredQuestionOutput]:
    PROMPT_MAPPING = {
        "one": {
            "system": SYSTEM_PROMPT_ONE,
            "template": PROMPT_TEMPLATE_ONE
        },
        "multi": {
            "system": SYSTEM_PROMPT_MULTI,
            "template": PROMPT_TEMPLATE_MULTI
        },
        "open": {
            "system": SYSTEM_PROMPT_OPEN,
            "template": PROMPT_TEMPLATE_OPEN
        }
    }
    
    llm = ChatOpenAI(
        model=LLM_MODEL_NAME,
        temperature=0.0,
        openai_api_base=LLM_URL_MODEL,
        openai_api_key=LLM_API_KEY,
        max_retries=3,
        stream=False,  # Отключаем стриминг для получения полного ответа сразу
        timeout=30,    # Увеличиваем timeout для больших промптов
        max_tokens=1024 # Больше токенов для генерации вопросов
    )
    
    parser = PydanticOutputParser(pydantic_object=StructuredQuestionOutput)

    class GenerateQuestionRunnable(Runnable[GenerateQuestionInput, StructuredQuestionOutput]):
        def invoke(self, input_data: GenerateQuestionInput) -> StructuredQuestionOutput:
            try:
                question_type = input_data["question_type"]
                logger.info(f"Processing question type: {question_type}")
                
                prompts = PROMPT_MAPPING.get(question_type)
                if not prompts:
                    raise ValueError(f"Unsupported question type: {question_type}")
                
                system_prompt = f"{prompts['system']}\n\n{parser.get_format_instructions()}"
                human_prompt = prompts["template"].format(original_text=input_data["input_text"])
                
                prompt = ChatPromptTemplate.from_messages([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=human_prompt)
                ])
                
                chain = prompt | llm | parser
                pyd_out = chain.invoke({}) 
                out: Dict = pyd_out.model_dump()
                out["source_text"] = input_data["input_text"]
                logger.info("Successfully generated question")
                return out
            except Exception as e:
                logger.error(f"Error generating question: {str(e)}")
                raise

    return GenerateQuestionRunnable()