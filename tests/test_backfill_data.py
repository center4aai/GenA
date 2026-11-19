"""Unit tests for backfill_data normalization helpers (no MongoDB)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backfill_data import (  # noqa: E402
    chunks_needing_gate_passed_filter,
    ensure_question_ids,
    infer_gate_passed,
    normalize_question_scores,
    normalize_score,
)


def test_normalize_score_maps_zero_to_default():
    assert normalize_score(0) == "2"
    assert normalize_score("0") == "2"


def test_normalize_score_keeps_one_to_three():
    assert normalize_score(1) == "1"
    assert normalize_score("3") == "3"


def test_normalize_question_scores_aliases_sensitivity_level():
    q = {"sensitivity_level": "3", "provocativeness": 0}
    assert normalize_question_scores(q) is True
    assert q["provocativeness"] == "3"
    assert q["sensitivity_level"] == "3"


def test_ensure_question_ids_adds_uuid():
    questions = [{"chunk_id": 1, "question_type": "one"}]
    added = ensure_question_ids("ds1", questions)
    assert added == 1
    assert questions[0]["question_id"]
    assert questions[0]["idempotency_key"] == "1:one"


def test_infer_gate_passed_from_gate_result():
    assert infer_gate_passed({"gate_result": {"passed": False}}) is False
    assert infer_gate_passed({}) is True


def test_infer_gate_passed_explicit_field_wins():
    assert infer_gate_passed({"gate_passed": False, "gate_result": {"passed": True}}) is False
    assert infer_gate_passed({"gate_passed": True, "gate_result": {"passed": False}}) is True


def test_chunks_needing_gate_passed_filter_includes_null():
    filt = chunks_needing_gate_passed_filter("ds1")
    assert filt["dataset_id"] == "ds1"
    assert {"gate_passed": {"$exists": False}} in filt["$or"]
    assert {"gate_passed": None} in filt["$or"]


def test_infer_gate_passed_rejection_reason_without_passed():
    assert infer_gate_passed({"gate_result": {"rejection_reason": "chunk_not_informative"}}) is False


def test_infer_gate_passed_gate_disabled_reason():
    assert infer_gate_passed(
        {"gate_result": {"rejection_reason": "gate_disabled_by_pipeline_mode"}}
    ) is True


def test_infer_gate_passed_derives_from_c1_c2_scores():
    assert infer_gate_passed(
        {"gate_result": {"c1_chunk_informative": [0], "c2_chunk_reference_clarity": [1]}}
    ) is False
    assert infer_gate_passed(
        {"gate_result": {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1]}}
    ) is True


def test_normalize_question_scores_difficulty_zero_uses_level():
    q = {"difficulty": 0, "difficulty_level": "3", "provocativeness": "2"}
    assert normalize_question_scores(q) is True
    assert q["difficulty"] == "3"
    assert q["difficulty_level"] == "3"
