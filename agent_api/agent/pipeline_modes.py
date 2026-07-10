from typing import Optional, Set


PIPELINE_MODE_GENERATOR_VALIDATOR = "generator_validator"
PIPELINE_MODE_GENERATOR_VALIDATOR_GATE = "generator_validator_gate"
PIPELINE_MODE_FULL = "full"

VALID_PIPELINE_MODES: Set[str] = {
    PIPELINE_MODE_GENERATOR_VALIDATOR,
    PIPELINE_MODE_GENERATOR_VALIDATOR_GATE,
    PIPELINE_MODE_FULL,
}


def normalize_pipeline_mode(pipeline_mode: Optional[str]) -> str:
    mode = (pipeline_mode or PIPELINE_MODE_FULL).strip()
    if mode not in VALID_PIPELINE_MODES:
        valid = ", ".join(sorted(VALID_PIPELINE_MODES))
        raise ValueError(f"Invalid pipeline_mode '{mode}'. Expected one of: {valid}")
    return mode


def pipeline_gate_enabled(pipeline_mode: Optional[str]) -> bool:
    return normalize_pipeline_mode(pipeline_mode) != PIPELINE_MODE_GENERATOR_VALIDATOR


def pipeline_refine_enabled(pipeline_mode: Optional[str]) -> bool:
    return normalize_pipeline_mode(pipeline_mode) == PIPELINE_MODE_FULL
