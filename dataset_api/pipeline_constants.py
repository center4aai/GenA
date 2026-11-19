"""Shared pipeline constants (statuses, message codes, rejection reasons).

Keep this as the Python source of truth. Frontend copies live in
``gena_frontend/src/shared/lib/taskMessage.ts`` (MESSAGE_CODE_KEYS) and
``progress.ts`` (task/dataset statuses). Tests in ``tests/test_pipeline_constants.py``
assert the two sides stay aligned.
"""

from __future__ import annotations

from typing import FrozenSet

TASK_STATUSES: FrozenSet[str] = frozenset(
    {"pending", "processing", "completed", "failed", "cancelled"}
)
LEGACY_TASK_STATUSES: FrozenSet[str] = frozenset({"skipped"})

DATASET_STATUSES: FrozenSet[str] = frozenset(
    {
        "pending",
        "processing",
        "completed",
        "completed_with_failures",
        "no_valid_chunks",
        "failed",
        "cancelled",
    }
)

PIPELINE_MODES: FrozenSet[str] = frozenset(
    {"full", "generator_validator_gate", "generator_validator"}
)

DOCUMENT_KINDS: FrozenSet[str] = frozenset({"normative", "textbook", "encyclopedia"})

# Повторная проверка качества (Quality revalidation) — отдельные коллекции
# revalidation_runs / revalidation_items, не общая очередь tasks.
REVALIDATION_SCOPES: FrozenSet[str] = frozenset({"all", "required", "user_edited", "paraphrased"})
REVALIDATION_RUN_STATUSES: FrozenSet[str] = frozenset(
    {"processing", "completed", "completed_with_errors", "failed"}
)
REVALIDATION_ITEM_STATUSES: FrozenSet[str] = frozenset({"pending", "processing", "done", "error"})
# Основной статус версии в UI (ТЗ 11.1–11.2).
REVALIDATION_PRIMARY_STATUSES: FrozenSet[str] = frozenset(
    {"not_started", "required", "processing", "completed", "completed_with_errors", "failed"}
)

# Методы чанкинга, выбираемые в UI (mirrors chunker/main.py CHUNKING_METHODS and
# gena_frontend ChunkingMethod). Нормативка поддерживает только rules.
CHUNKING_METHODS: FrozenSet[str] = frozenset({"rules", "layout", "semantic", "llm"})
DEFAULT_CHUNKING_METHOD = "rules"

GATE_REJECTION_REASONS: FrozenSet[str] = frozenset(
    {
        "chunk_not_informative",
        "chunk_not_self_contained",
        "chunk_not_suitable_for_multi",
        # Textbook / encyclopedia gate addenda: c1 = 0 with a category.
        "chunk_exercise_block",
        "chunk_front_matter",
        "gate_llm_error",
        "gate_parse_error",
        "gate_disabled_by_pipeline_mode",
        "chunk_pre_validated",
        "gate_chain_not_configured",
    }
)

MESSAGE_CODES: FrozenSet[str] = frozenset(
    {
        "model_auth_failed",
        "model_output_invalid",
        "model_unavailable",
        "model_error",
        "chunk_too_short",
        "question_save_failed",
        "task_stuck",
        "document_fetch_failed",
        "chunker_failed",
        "chunker_method_unavailable",
        "no_usable_chunks",
        "chunks_save_failed",
        "enqueue_failed",
        "gate_unavailable",
        "gate_timeout",
        "gate_overloaded",
        "internal_error",
        "gate_rejected_chunk_not_informative",
        "gate_rejected_chunk_not_self_contained",
        "gate_rejected_chunk_not_suitable_for_multi",
        "gate_rejected_chunk_exercise_block",
        "gate_rejected_chunk_front_matter",
        # The worker emits ``gate_rejected_<rejection_reason>`` for every
        # rejection, so each reason the gate can fail with needs a code.
        "gate_rejected_gate_llm_error",
        "gate_rejected_gate_parse_error",
        "gate_rejected_gate_chain_not_configured",
        "gate_rejected_unknown",
        "gate_passed_chunk_not_suitable_for_multi",
    }
)
