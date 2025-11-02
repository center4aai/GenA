"""
Интерфейс для LLM-валидатора, интегрированный в архитектуру agent_api.
"""

from typing import Dict, Any, TypedDict, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
import logging

from .validator import LLMValidator

logger = logging.getLogger(__name__)

class ValidationInput(TypedDict):
    question_type: Literal["open", "one", "multi"]
    source_text: Optional[str]
    question: Dict[str, Any]

class ValidationOutput(BaseModel):
    type: str = Field(description="Тип вопроса")
    by_block: Dict[str, list] = Field(description="Оценки по блокам критериев")
    raw: Dict[str, str] = Field(description="Сырые ответы LLM")
    total: int = Field(description="Общий балл")
    max_total: int = Field(description="Максимально возможный балл")
    threshold: int = Field(description="Пороговое значение")
    passed: bool = Field(description="Прошел ли вопрос порог качества")

def create_validation_chain() -> Runnable[ValidationInput, ValidationOutput]:
    """
    Создает цепочку для валидации вопросов.
    
    Returns:
        Runnable: Цепочка для валидации вопросов
    """
    
    class ValidationRunnable(Runnable[ValidationInput, ValidationOutput]):
        def __init__(self):
            self.validator = LLMValidator()
            
        def invoke(self, input_data: ValidationInput) -> ValidationOutput:
            try:
                logger.info(f"Validating question of type: {input_data['question_type']}")
                
                result = self.validator.evaluate(
                    qtype=input_data["question_type"],
                    source_text=input_data.get("source_text"),
                    question=input_data["question"]
                )
                
                logger.info(f"Validation completed: score={result['total']}/{result['max_total']}, passed={result['passed']}")
                
                return ValidationOutput(**result)
                
            except Exception as e:
                logger.error(f"Error during validation: {str(e)}")
                raise
    
    return ValidationRunnable()
