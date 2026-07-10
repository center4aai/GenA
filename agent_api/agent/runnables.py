from dataclasses import dataclass
from typing import Dict, Optional

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from agent.nodes.generate_question.generate_question import GenerateQuestionInput, create_generate_question_chain
from agent.nodes.assess_sensitivity.estimation import ProvocativenessInput, create_provocativeness_chain
from agent.nodes.llm_validator.validation_chain import ValidationInput, ValidationOutput, create_validation_chain
from agent.nodes.refine_question.refine_question import RefineQuestionInput, create_refine_question_chain
from agent.nodes.assess_difficulty.estimation import DifficultyInput,DifficultyOutput, create_difficulty_chain
from agent.nodes.chunk_gate.chunk_gate import ChunkGateInput, ChunkGateOutput, create_chunk_gate_chain

@dataclass
class GENAARunnablesOllama:
    generate_question_chain: Runnable[GenerateQuestionInput, AIMessage]
    provocativeness_chain: Runnable[ProvocativenessInput, AIMessage]
    validation_chain: Runnable[ValidationInput, ValidationOutput]
    difficulty_chain: Runnable[DifficultyInput,DifficultyOutput]
    refine_question_chain: Runnable[RefineQuestionInput, dict]
    chunk_gate_chain: Runnable[ChunkGateInput, ChunkGateOutput] = None

def _extract_provider_kwargs(model_dict: dict) -> dict:
    """Извлекает provider-специфичные kwargs из словаря модели."""
    extra = model_dict.get("extra", {})
    result = {}
    if "scope" in extra:
        result["scope"] = extra["scope"]
    if "folder_id" in extra:
        result["folder_id"] = extra["folder_id"]
    return result


def create_GENA_runnables_ollama(
    generation_model: Optional[Dict[str, str]] = None,
    validation_model: Optional[Dict[str, str]] = None,
) -> GENAARunnablesOllama:
    """
    Создаёт набор цепочек.

    Args:
        generation_model: {"model_name": ..., "base_url": ..., "api_key": ...,
                           "provider": ..., "extra": {...}}
                          используется для генерации, провокативности, сложности.
        validation_model:  аналогично, но для валидации.
                          Если не указан — берётся generation_model.
    """
    gen = generation_model or {}
    val = validation_model or {}
    gen_kw = _extract_provider_kwargs(gen)
    val_kw = _extract_provider_kwargs(val)

    generate_question_chain = create_generate_question_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
        provider=gen.get("provider", "openai"),
        **gen_kw,
    )
    provocativeness_chain = create_provocativeness_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
        provider=gen.get("provider", "openai"),
        **gen_kw,
    )
    difficulty_chain = create_difficulty_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
        provider=gen.get("provider", "openai"),
        **gen_kw,
    )
    validation_chain = create_validation_chain(
        model_name=val.get("model_name"),
        base_url=val.get("base_url"),
        api_key=val.get("api_key"),
        provider=val.get("provider", "openai"),
        **val_kw,
    )
    refine_question_chain = create_refine_question_chain(
        model_name=gen.get("model_name"),
        base_url=gen.get("base_url"),
        api_key=gen.get("api_key"),
        provider=gen.get("provider", "openai"),
        **gen_kw,
    )
    chunk_gate_chain = create_chunk_gate_chain(
        model_name=val.get("model_name"),
        base_url=val.get("base_url"),
        api_key=val.get("api_key"),
        provider=val.get("provider", "openai"),
        **val_kw,
    )

    return GENAARunnablesOllama(
        generate_question_chain=generate_question_chain,
        provocativeness_chain=provocativeness_chain,
        validation_chain=validation_chain,
        difficulty_chain=difficulty_chain,
        refine_question_chain=refine_question_chain,
        chunk_gate_chain=chunk_gate_chain,
    )
