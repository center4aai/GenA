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
) -> GENAARunnablesOllama:
    
    generate_question_chain = create_generate_question_chain()
    provocativeness_chain = create_provocativeness_chain()
    validation_chain = create_validation_chain()
    difficulty_chain = create_difficulty_chain()
#    format_output_chain = create_format_output_chain()
#    retriever_chain = create_retriever_chain()

    return GENAARunnablesOllama(
        generate_question_chain=generate_question_chain,
        provocativeness_chain=provocativeness_chain,
        validation_chain=validation_chain,
        difficulty_chain=difficulty_chain
#        retriever_chain=retriever_chain,
#        format_output_chain=format_output_chain

    )
