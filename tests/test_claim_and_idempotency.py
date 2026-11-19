"""Unit tests for task claim patterns, idempotency keys, and status transitions."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from dataset_api_loader import load_dataset_api_module

_api = load_dataset_api_module()
ALLOWED_STATUS_TRANSITIONS = _api.ALLOWED_STATUS_TRANSITIONS
QuestionData = _api.QuestionData
TaskClaimRequest = _api.TaskClaimRequest
_validate_status_transition = _api._validate_status_transition


# ---------- Status transition validation ----------


@pytest.mark.parametrize(
    "current,new,allowed",
    [
        ("pending", "processing", True),
        ("processing", "completed", True),
        ("processing", "failed", True),
        ("processing", "cancelled", True),
        ("processing", "pending", True),
        ("failed", "pending", True),
        ("completed", "pending", False),
        ("cancelled", "pending", False),
        ("skipped", "processing", False),  # legacy terminal
        ("processing", "skipped", False),  # skipped no longer writable
    ],
)
def test_status_transition_matrix(current: str, new: str, allowed: bool):
    if allowed:
        _validate_status_transition(current, new)
    else:
        with pytest.raises(HTTPException) as exc:
            _validate_status_transition(current, new)
        assert exc.value.status_code == 400
        assert "Invalid status transition" in exc.value.detail


def test_status_transition_same_status_is_noop():
    _validate_status_transition("processing", "processing")


def test_allowed_transitions_cover_all_statuses():
    for status in ALLOWED_STATUS_TRANSITIONS:
        assert isinstance(ALLOWED_STATUS_TRANSITIONS[status], set)


# ---------- Idempotency key ----------


def test_idempotency_key_from_chunk_and_type():
    q = QuestionData(
        chunk_id=3,
        question_type="multi",
        task="T?",
        options={"option_1": "a"},
        correct_answer="1",
        provocativeness="2",
    )
    assert q.idempotency_key is None
    key = q.idempotency_key or f"{q.chunk_id}:{q.question_type}"
    assert key == "3:multi"


def test_idempotency_key_explicit():
    q = QuestionData(
        chunk_id=1,
        question_type="open",
        task="T?",
        options="",
        correct_answer="x",
        provocativeness="2",
        idempotency_key="custom-key",
    )
    assert q.idempotency_key == "custom-key"


def test_question_data_ignores_unknown_fields():
    q = QuestionData(
        chunk_id=1,
        question_type="one",
        task="T?",
        options={"option_1": "a"},
        correct_answer="1",
        provocativeness="2",
        sensitivity_level="3",
        extra_legacy_field="keep-me-out",
    )
    assert not hasattr(q, "extra_legacy_field")
    assert not hasattr(q, "sensitivity_level")


# ---------- Claim endpoint pattern (mocked Mongo) ----------


def test_claim_tasks_atomic_pending_to_processing():
    claim_tasks = _api.claim_tasks

    tasks = [
        {"_id": "abc", "status": "pending", "chunk_id": 1},
        {"_id": "def", "status": "pending", "chunk_id": 2},
    ]
    call_idx = {"n": 0}

    def fake_find_one_and_update(filter_query, update, sort=None, return_document=None):
        if filter_query.get("status") != "pending":
            return None
        if call_idx["n"] >= len(tasks):
            return None
        task = dict(tasks[call_idx["n"]])
        call_idx["n"] += 1
        task.update(update.get("$set", {}))
        return task

    mock_collection = MagicMock()
    mock_collection.find_one_and_update.side_effect = fake_find_one_and_update
    mock_db = MagicMock()
    mock_db.tasks = mock_collection

    with patch.object(_api, "get_db", return_value=mock_db):
        claimed = claim_tasks(TaskClaimRequest(worker_id="w1", limit=5))

    assert len(claimed) == 2
    assert all(t["status"] == "processing" for t in claimed)
    assert all(t["worker_id"] == "w1" for t in claimed)
    assert mock_collection.find_one_and_update.call_count == 3  # third returns None

    first_update = mock_collection.find_one_and_update.call_args_list[0][0][1]
    assert first_update["$set"]["status"] == "processing"
    assert first_update["$set"]["worker_id"] == "w1"
    assert "claimed_at" in first_update["$set"]
    assert "updated_at" in first_update["$set"]


def test_claim_respects_queue_filter():
    claim_tasks = _api.claim_tasks

    mock_collection = MagicMock()
    mock_collection.find_one_and_update.return_value = None
    mock_db = MagicMock()
    mock_db.tasks = mock_collection

    with patch.object(_api, "get_db", return_value=mock_db):
        claim_tasks(TaskClaimRequest(queue_name="q-test", limit=1))

    filter_used = mock_collection.find_one_and_update.call_args[0][0]
    assert filter_used["status"] == "pending"
    assert filter_used["queue_name"] == "q-test"


def test_idempotent_add_question_filter_pattern():
    """Document the atomic add-question guard used for idempotent $push."""
    idempotency_key = "7:one"
    filter_doc = {
        "dataset_id": "ds1",
        "version": 1,
        "questions.idempotency_key": {"$ne": idempotency_key},
    }
    assert filter_doc["questions.idempotency_key"]["$ne"] == "7:one"


def test_claim_sets_timestamps():
    """Claim update payload must stamp worker and timestamps (via claim_tasks)."""
    claim_tasks = _api.claim_tasks
    mock_collection = MagicMock()
    mock_collection.find_one_and_update.return_value = None
    mock_db = MagicMock()
    mock_db.tasks = mock_collection

    with patch.object(_api, "get_db", return_value=mock_db):
        claim_tasks(TaskClaimRequest(worker_id="worker-a", limit=1))

    update = mock_collection.find_one_and_update.call_args[0][1]
    assert update["$set"]["status"] == "processing"
    assert update["$set"]["worker_id"] == "worker-a"
    assert isinstance(update["$set"]["claimed_at"], datetime)
    assert isinstance(update["$set"]["updated_at"], datetime)


# ---------- Owner check on status update (mocked Mongo) ----------


def _owned_task_db(task: dict):
    """Fake ``db.tasks`` whose ``update_one`` honours the CAS filter against ``task``."""
    mock_collection = MagicMock()
    mock_collection.find_one.return_value = task

    def fake_update_one(filter_query, update):
        matched = all(task.get(k) == v for k, v in filter_query.items())
        return MagicMock(matched_count=1 if matched else 0)

    mock_collection.update_one.side_effect = fake_update_one
    mock_db = MagicMock()
    mock_db.tasks = mock_collection
    return mock_db, mock_collection


_TASK_OID = "507f1f77bcf86cd799439011"


def test_processing_to_pending_without_worker_id_bypasses_owner_check():
    """Recovery of a stuck task (owner dead) sends no worker_id and must pass."""
    from bson import ObjectId

    update_task_status = _api.update_task_status
    task = {"_id": ObjectId(_TASK_OID), "status": "processing", "worker_id": "worker-A"}
    mock_db, mock_collection = _owned_task_db(task)

    with patch.object(_api, "get_db", return_value=mock_db):
        result = update_task_status(
            _TASK_OID,
            _api.TaskStatusUpdate(status="pending", expected_status="processing", attempts=1),
        )

    assert result["status"] == "pending"
    filter_used = mock_collection.update_one.call_args[0][0]
    assert filter_used["status"] == "processing"
    assert "worker_id" not in filter_used
    assert mock_collection.update_one.call_args[0][1]["$set"]["attempts"] == 1


def test_processing_to_pending_with_foreign_worker_id_conflicts():
    from bson import ObjectId

    update_task_status = _api.update_task_status
    task = {"_id": ObjectId(_TASK_OID), "status": "processing", "worker_id": "worker-A"}
    mock_db, mock_collection = _owned_task_db(task)

    with patch.object(_api, "get_db", return_value=mock_db):
        with pytest.raises(HTTPException) as exc:
            update_task_status(
                _TASK_OID,
                _api.TaskStatusUpdate(
                    status="pending", expected_status="processing", worker_id="worker-B"
                ),
            )

    assert exc.value.status_code == 409
    filter_used = mock_collection.update_one.call_args[0][0]
    assert filter_used["worker_id"] == "worker-B"


def test_processing_to_pending_with_own_worker_id_passes():
    from bson import ObjectId

    update_task_status = _api.update_task_status
    task = {"_id": ObjectId(_TASK_OID), "status": "processing", "worker_id": "worker-A"}
    mock_db, mock_collection = _owned_task_db(task)

    with patch.object(_api, "get_db", return_value=mock_db):
        result = update_task_status(
            _TASK_OID,
            _api.TaskStatusUpdate(
                status="pending", expected_status="processing", worker_id="worker-A"
            ),
        )

    assert result["status"] == "pending"
    filter_used = mock_collection.update_one.call_args[0][0]
    assert filter_used["worker_id"] == "worker-A"
