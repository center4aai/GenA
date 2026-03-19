from dataclasses import dataclass
from typing import Dict, Optional

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from agent.nodes.generate_question.generate_question import GenerateQuestionInput, create_generate_question_chain
from agent.nodes.assess_sensitivity.estimation import ProvocativenessInput, create_provocativeness_chain
from agent.nodes.llm_validator.validation_chain import ValidationInput, ValidationOutput, create_validation_chain
#from agent.modules.agent.nodes.milvus.retriever import MilvusSearchInput, create_retriever_chain
#from agent.modules.agent.nodes.format_output.formating import FormatOutputInput, create_format_output_chain
from agent.nodes.assess_difficulty.estimation import DifficultyInput,DifficultyOutput, create_difficulty_chain

@dataclass
class GENAARunnablesOllama:
    generate_question_chain: Runnable[GenerateQuestionInput, AIMessage]
    provocativeness_chain: Runnable[ProvocativenessInput, AIMessage]
    validation_chain: Runnable[ValidationInput, ValidationOutput]
    difficulty_chain: Runnable[DifficultyInput,DifficultyOutput] 
#    create_format_output_chain: Runnable[FormatOutputInput, AIMessage]
#    create_retriever_chain: Runnable[MilvusSearchInput, AIMessage]

def create_GENA_runnables_ollama(
    generation_model: Optional[Dict[str, str]] = None,
    validation_model: Optional[Dict[str, str]] = None,
) -> GENAARunnablesOllama:
    """
    Создаёт набор цепочек.

    Args:
        generation_model: {"model_name": ..., "base_url": ..., "api_key": ...}
                          используется для генерации, провокативности, сложности.
        validation_model:  аналогично, но для валидации.
                          Если не указан — берётся generation_model.
    """
    gen = generation_model or {}
    val = validation_model or {}

    generate_question_chain = create_generate_question_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
    )
    provocativeness_chain = create_provocativeness_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
    )
    difficulty_chain = create_difficulty_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
    )
    validation_chain = create_validation_chain(
        model_name=val.get("model_name"),
        base_url=val.get("base_url"),
        api_key=val.get("api_key"),
    )

    return GENAARunnablesOllama(
        generate_question_chain=generate_question_chain,
        provocativeness_chain=provocativeness_chain,
        validation_chain=validation_chain,
        difficulty_chain=difficulty_chain,
    )
