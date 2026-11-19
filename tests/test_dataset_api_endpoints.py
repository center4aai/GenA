"""Unit tests for every endpoint in dataset_api.py (direct async calls, mocked Mongo)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from dataset_api_loader import load_dataset_api_module

DATASET_ID = "507f1f77bcf86cd799439011"
TASK_ID = "507f1f77bcf86cd799439012"
QUEUE_ID = "507f1f77bcf86cd799439013"

EXPERT = SimpleNamespace(username="tester", role="expert")
USER = SimpleNamespace(username="tester", role="user")


@pytest.fixture
def mod(monkeypatch):
    module = load_dataset_api_module()
    # These tests mock Mongo with MagicMock, whose find_one() is always truthy,
    # so every version would look locked by a running revalidation. The lock
    # itself is covered by tests/test_revalidation_api.py.
    monkeypatch.setattr(module, "_active_revalidation", lambda db, dataset_id, version: None)
    return module


def _sorted_cursor(items):
    """Mock a find().sort() cursor over the questions collection."""
    cursor = MagicMock()
    cursor.sort.return_value = list(items)
    return cursor


def _task_groups(tasks, gen_models=(), val_models=()):
    """Fold a readable list of tasks into the (stage x status) rows _task_matrix reads."""
    counts = {}
    for t in tasks:
        tt = t.get("task_type") or "generation"
        stage = tt if tt in ("chunk", "gate") else "generation"
        key = (stage, t.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "_id": {"stage": stage, "status": status},
            "count": n,
            "gen_models": list(gen_models),
            "val_models": list(val_models),
        }
        for (stage, status), n in counts.items()
    ]


def _chain(*items):
    """Mock find().sort().limit() style cursor chains."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = list(items)
    return cursor


def _chain_sort_only(*items):
    """Mock find().sort() without limit."""
    cursor = MagicMock()
    cursor.sort.return_value = list(items)
    return cursor


def _sample_question(mod):
    return mod.QuestionData(
        chunk_id=1,
        question_type="open",
        task="What?",
        options="",
        correct_answer="x",
        provocativeness="2",
    )


def _sample_task_data(mod, **kwargs):
    defaults = dict(
        chunk_id=1,
        chunk_text="text",
        question_type="open",
        source_document="doc.pdf",
        dataset_name="ds",
    )
    defaults.update(kwargs)
    return mod.TaskData(**defaults)


# ---------- health / metrics ----------


def test_health(mod):
    def run():
        result = mod.health()
        assert result == {"status": "ok", "service": "dataset_api"}

    run()


def test_metrics(mod):
    mock_db = MagicMock()
    mock_db.tasks.count_documents.side_effect = [5, 2, 10, 1, 0, 3]
    mock_db.datasets.count_documents.return_value = 7

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.metrics()
        assert result["tasks_pending"] == 5
        assert result["tasks_processing"] == 2
        assert result["datasets_total"] == 7

    run()


# ---------- datasets CRUD ----------


def test_create_dataset_stamps_created_by(mod):
    mock_db = MagicMock()
    oid = ObjectId(DATASET_ID)
    mock_db.datasets.insert_one.return_value = MagicMock(inserted_id=oid)
    payload = mod.DatasetCreate(
        name="Test DS",
        source_document="doc.pdf",
        questions=[_sample_question(mod)],
        metadata={"created_by": "client-should-be-ignored"},
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.create_dataset(payload, current_user=EXPERT)
        assert result["dataset_id"] == DATASET_ID
        assert result["version"] == 1
        inserted = mock_db.datasets.insert_one.call_args[0][0]
        assert inserted["metadata"]["created_by"] == "tester"
        version_doc = mock_db.dataset_versions.insert_one.call_args[0][0]
        assert version_doc["metadata"]["created_by"] == "tester"

    run()


def test_list_datasets(mod):
    mock_db = MagicMock()
    ds = {
        "_id": ObjectId(DATASET_ID),
        "name": "A",
        "description": "d",
        "source_document": "doc",
        "current_version": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "metadata": {},
    }
    mock_db.datasets.find.return_value = [ds]
    mock_db.chunks.aggregate.return_value = [
        {"_id": DATASET_ID, "total": 3, "valid": 3},
    ]
    # Counts come from the questions collection, grouped by (dataset_id, version).
    mock_db.questions.aggregate.return_value = [
        {"_id": {"dataset_id": DATASET_ID, "version": 1}, "n": 7},
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.list_datasets(current_user=USER)
        assert len(result) == 1
        assert result[0]["_id"] == DATASET_ID
        assert result[0]["chunks_count"] == 3
        assert result[0]["chunks_valid"] == 3
        assert result[0]["questions_count"] == 7

    run()


def test_list_datasets_questions_count_defaults_zero(mod):
    mock_db = MagicMock()
    ds = {
        "_id": ObjectId(DATASET_ID),
        "name": "Empty",
        "description": "",
        "source_document": "doc",
        "current_version": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "metadata": {"total_questions_generated": 0},
    }
    mock_db.datasets.find.return_value = [ds]
    mock_db.chunks.aggregate.return_value = []
    mock_db.dataset_versions.aggregate.return_value = []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.list_datasets(current_user=USER)
        assert result[0]["questions_count"] == 0

    run()


def test_list_datasets_version_summary(mod):
    """Versions column and "last modified" come from one grouped read over versions."""
    mock_db = MagicMock()
    t0, t1, t2, t3 = (datetime(2026, 8, 1, 10, m) for m in range(4))
    base = {
        "description": "", "source_document": "doc",
        "created_at": t0, "updated_at": t3, "metadata": {},
    }
    mock_db.datasets.find.return_value = [
        {"_id": ObjectId(DATASET_ID), "name": "A", "current_version": 3, **base},
        {"_id": ObjectId(TASK_ID), "name": "B", "current_version": 1, **base},
    ]
    mock_db.chunks.aggregate.return_value = []
    mock_db.questions.aggregate.return_value = [
        {"_id": {"dataset_id": DATASET_ID, "version": 3}, "n": 5},
        {"_id": {"dataset_id": TASK_ID, "version": 1}, "n": 2},
    ]
    mock_db.dataset_versions.aggregate.return_value = [
        {"_id": DATASET_ID, "versions": [
            {"version": 1, "created_at": t0, "user_updated_at": None},
            {"version": 2, "created_at": t1, "user_updated_at": t2},
            {"version": 3, "created_at": t3},
        ]},
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.list_datasets(current_user=USER)
        a, b = result
        assert a["versions_count"] == 3
        assert a["last_user_modified_at"] == t3
        assert a["current_version_user_updated_at"] is None
        # a dataset without version rows still reports its pointer as the count
        assert b["versions_count"] == 1
        assert b["last_user_modified_at"] is None
        assert b["current_version_user_updated_at"] is None
        pipeline = mock_db.dataset_versions.aggregate.call_args[0][0]
        assert pipeline[0]["$match"] == {"dataset_id": {"$in": [DATASET_ID, TASK_ID]}}

    run()


_T = [datetime(2026, 9, d, 12, 0) for d in range(1, 5)]


@pytest.mark.parametrize(
    "versions, current, expected_last, expected_current",
    [
        ([], 1, None, None),
        # a single untouched version: generation alone is not a modification
        ([{"version": 1, "created_at": _T[0]}], 1, None, None),
        # an in-place edit of version 1 counts
        ([{"version": 1, "created_at": _T[0], "user_updated_at": _T[1]}], 1, _T[1], _T[1]),
        # a transform (new version) counts, the original creation does not
        ([{"version": 1, "created_at": _T[0]}, {"version": 2, "created_at": _T[2]}], 2, _T[2], None),
        # an ISO-string stamp from a backfill participates in the max
        (
            [{"version": 1, "created_at": _T[0], "user_updated_at": "2026-09-04T12:00:00Z"},
             {"version": 2, "created_at": _T[2]}],
            2, _T[3], None,
        ),
        # tz-aware and naive values compare without raising
        (
            [{"version": 1, "created_at": _T[0], "user_updated_at": _T[1].replace(tzinfo=timezone.utc)},
             {"version": 2, "created_at": _T[2]}],
            1, _T[2], _T[1],
        ),
        # garbage stamps are ignored
        ([{"version": 1, "created_at": _T[0], "user_updated_at": "not a date"}], 1, None, None),
    ],
)
def test_summarize_versions_semantics(mod, versions, current, expected_last, expected_current):
    summary = mod._summarize_versions(versions, current)
    assert summary["versions_count"] == len(versions)
    assert summary["last_user_modified_at"] == expected_last
    assert summary["current_version_user_updated_at"] == expected_current


def test_list_datasets_mine_filter(mod):
    mock_db = MagicMock()
    mock_db.datasets.find.return_value = []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.list_datasets(mine=1, current_user=EXPERT)
        query = mock_db.datasets.find.call_args[0][0]
        assert query == {"metadata.created_by": "tester"}

    run()


def test_get_dataset_found(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "DS",
        "description": "desc",
        "source_document": "doc",
        "current_version": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    mock_db.dataset_versions.find_one.return_value = {
        "metadata": {"status": "completed"},
    }
    mock_db.questions.find.return_value = _sorted_cursor([{"chunk_id": 1}])

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset(DATASET_ID, current_user=USER)
        assert result["dataset_id"] == DATASET_ID
        assert result["questions"] == [{"chunk_id": 1, "validation_is_current": False}]
        assert result["metadata"]["status"] == "completed"

    run()


def test_get_dataset_hides_another_users_dataset(mod):
    """Ownership was enforced only in the list; reads by id were wide open."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {"created_by": "someone-else"},
    }

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.get_dataset(DATASET_ID, current_user=USER)
        # 404 rather than 403: existence is not the caller's business.
        assert exc.value.status_code == 404
        mock_db.dataset_versions.find_one.assert_not_called()

    run()


def test_get_dataset_allows_the_owner(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "n",
        "description": "d",
        "source_document": "s",
        "current_version": 1,
        "created_at": "c",
        "updated_at": "u",
        "metadata": {"created_by": USER.username},
    }
    mock_db.dataset_versions.find_one.return_value = {"questions": [], "metadata": {}}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset(DATASET_ID, current_user=USER)
        assert result["dataset_id"] == DATASET_ID

    run()


def test_get_dataset_expert_sees_any_dataset(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "n",
        "description": "d",
        "source_document": "s",
        "current_version": 1,
        "created_at": "c",
        "updated_at": "u",
        "metadata": {"created_by": "someone-else"},
    }
    mock_db.dataset_versions.find_one.return_value = {"questions": [], "metadata": {}}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset(DATASET_ID, current_user=EXPERT)
        assert result["dataset_id"] == DATASET_ID

    run()


def test_get_dataset_not_found(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = None

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.get_dataset(DATASET_ID, current_user=USER)
            assert exc.value.status_code == 404

    run()


def test_get_dataset_versions(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    versions = [
        {
            "version": 1,
            "created_at": datetime.utcnow(),
            "metadata": {},
            "user_updated_at": None,
        }
    ]
    mock_db.dataset_versions.aggregate.return_value = iter(versions)
    mock_db.questions.aggregate.return_value = iter([{"_id": 1, "n": 12}])

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset_versions(DATASET_ID, current_user=USER)
        assert [v["version"] for v in result] == [1]
        projection = mock_db.dataset_versions.aggregate.call_args[0][0][1]["$project"]
        assert "user_updated_at" in projection
        assert result[0]["questions_count"] == 12

    run()


def _completed_dataset_mocks(mod, status="completed"):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {"status": status},
    }
    mock_db.dataset_versions.find_one.return_value = {"metadata": {"status": status}}
    mock_db.datasets.find_one_and_update.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 2,
    }
    return mock_db


def test_update_dataset(mod):
    mock_db = _completed_dataset_mocks(mod)
    update = mod.DatasetUpdate(
        questions=[_sample_question(mod)],
        metadata={"status": "completed"},
        base_version=1,
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        assert result["new_version"] == 2
        version_doc = mock_db.dataset_versions.insert_one.call_args[0][0]
        assert version_doc["version"] == 2
        assert version_doc["metadata"]["status"] == "completed"

    run()


def test_update_dataset_loses_the_race_instead_of_shadowing(mod):
    """Two transforms off the same base must not both create a version.

    The check-then-act version bump let both pass, producing v4 and v5 where v5
    was built from v3 — silently discarding v4.
    """
    mock_db = _completed_dataset_mocks(mod)
    mock_db.datasets.find_one_and_update.return_value = None  # CAS filter missed
    update = mod.DatasetUpdate(questions=[_sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        assert exc.value.status_code == 409
        mock_db.dataset_versions.insert_one.assert_not_called()

    run()


def test_update_dataset_cas_filter_pins_current_version(mod):
    mock_db = _completed_dataset_mocks(mod)
    update = mod.DatasetUpdate(questions=[_sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        cas_filter = mock_db.datasets.find_one_and_update.call_args[0][0]
        assert cas_filter["current_version"] == 1

    run()


def test_update_dataset_restores_pointer_when_version_insert_fails(mod):
    """A torn write would leave every read of the dataset 404-ing forever."""
    mock_db = _completed_dataset_mocks(mod)
    mock_db.dataset_versions.insert_one.side_effect = RuntimeError("write failed")
    update = mod.DatasetUpdate(questions=[_sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(Exception):
                mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        rollback = mock_db.datasets.update_one.call_args[0]
        assert rollback[1]["$set"]["current_version"] == 1

    run()


def test_update_dataset_requires_base_version(mod):
    """New versions may only branch off the tip, so base_version is mandatory."""
    mock_db = _completed_dataset_mocks(mod)
    update = mod.DatasetUpdate(questions=[_sample_question(mod)])

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        assert exc.value.status_code == 400
        mock_db.dataset_versions.insert_one.assert_not_called()

    run()


def test_update_dataset_rejected_while_generating(mod):
    """Transform is only offered for finished sets; the API enforces it too."""
    mock_db = _completed_dataset_mocks(mod, status="processing")
    update = mod.DatasetUpdate(questions=[_sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        assert exc.value.status_code == 409
        mock_db.datasets.find_one_and_update.assert_not_called()

    run()


def test_delete_dataset(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    mock_db.fs.files.find.return_value = []
    mock_fs = MagicMock()

    def run():
        with (
            patch.object(mod, "get_db", return_value=mock_db),
            patch.object(mod, "get_gridfs", return_value=mock_fs),
        ):
            result = mod.delete_dataset(DATASET_ID, current_user=EXPERT)
        assert result["message"] == "Dataset deleted successfully"
        mock_db.dataset_versions.delete_many.assert_called_once_with({"dataset_id": DATASET_ID})
        mock_db.chunks.delete_many.assert_called_once_with({"dataset_id": DATASET_ID})
        mock_db.tasks.delete_many.assert_called_once_with({"dataset_id": DATASET_ID})
        mock_db.datasets.delete_one.assert_called_once()
        assert result["queue_deleted"] is False
        mock_db.queues.delete_one.assert_not_called()

    run()


def test_delete_dataset_removes_its_queue(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "metadata": {"queue_name": "q1"},
    }
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID)}
    mock_db.fs.files.find.return_value = []

    def run():
        with (
            patch.object(mod, "get_db", return_value=mock_db),
            patch.object(mod, "get_gridfs", return_value=MagicMock()),
        ):
            result = mod.delete_dataset(DATASET_ID, current_user=EXPERT)
        assert result["queue_deleted"] is True
        mock_db.queues.find_one.assert_called_once_with({"name": "q1"}, {"_id": 1})
        mock_db.tasks.delete_many.assert_any_call({"queue_id": QUEUE_ID})
        mock_db.queues.delete_one.assert_called_once_with({"_id": ObjectId(QUEUE_ID)})

    run()


def test_download_document_cyrillic_filename(mod):
    mock_db = MagicMock()
    mock_db.fs.files.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "filename": "учебник тест 1.pdf",
        "contentType": "application/pdf",
    }
    mock_fs = MagicMock()
    mock_fs.get.return_value.read.return_value = b"%PDF-1.4"

    def run():
        with (
            patch.object(mod, "get_db", return_value=mock_db),
            patch.object(mod, "get_gridfs", return_value=mock_fs),
        ):
            resp = mod.download_dataset_document(DATASET_ID, _=None)
        disposition = resp.headers["content-disposition"]
        # Header must survive latin-1 encoding used by the ASGI layer.
        disposition.encode("latin-1")
        assert "filename*=UTF-8''" in disposition
        assert ".pdf" in disposition

    run()


def test_content_disposition_keeps_ascii_name(mod):
    disposition = mod._content_disposition("report.docx")
    assert 'filename="report.docx"' in disposition
    disposition.encode("latin-1")


# ---------- queues ----------


def test_create_queue_success(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = None
    mock_db.queues.insert_one.return_value = MagicMock(inserted_id=ObjectId(QUEUE_ID))
    payload = mod.QueueCreate(name="q1", description="test queue")

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.create_queue(payload, current_user=USER)
        assert result["name"] == "q1"
        assert result["queue_id"] == QUEUE_ID

    run()


def test_create_queue_duplicate_name_400(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"name": "q1"}
    payload = mod.QueueCreate(name="q1")

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.create_queue(payload, current_user=USER)
            assert exc.value.status_code == 400

    run()


@pytest.mark.parametrize("name", ["a#b", "a/b", "a\\b", "a?b", "a%b", "a\tb", "", "  ", "x" * 301])
def test_create_queue_rejects_unroutable_name(mod, name):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = None
    payload = mod.QueueCreate(name=name)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.create_queue(payload, current_user=EXPERT)
        assert exc.value.status_code == 400
        mock_db.queues.insert_one.assert_not_called()

    run()


def test_create_queue_accepts_real_world_name(mod):
    name = "queue_Medinskiy Torkunov, (т. 1) — 2 сент. 2026 г._20260902133829"
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = None
    mock_db.queues.insert_one.return_value = MagicMock(inserted_id=ObjectId(QUEUE_ID))

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.create_queue(mod.QueueCreate(name=name), current_user=EXPERT)
        assert result["name"] == name

    run()


def test_list_queues(mod):
    mock_db = MagicMock()
    queue_doc = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    other_queue = "507f1f77bcf86cd799439099"
    mock_db.queues.find.return_value = [queue_doc]
    # One $group over ALL queues' tasks; rows for other queues must not leak in.
    mock_db.tasks.aggregate.return_value = [
        {"_id": {"queue_id": QUEUE_ID, "status": "pending"}, "count": 2},
        {"_id": {"queue_id": QUEUE_ID, "status": "completed"}, "count": 5},
        {"_id": {"queue_id": QUEUE_ID, "status": "skipped"}, "count": 1},
        {"_id": {"queue_id": other_queue, "status": "failed"}, "count": 9},
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.list_queues(current_user=USER)
        assert len(result) == 1
        assert result[0]["pending_count"] == 2
        assert result[0]["completed_count"] == 5
        assert result[0]["failed_count"] == 0
        # legacy skipped counts toward the total, not any bucket
        assert result[0]["task_count"] == 8
        assert result[0]["_id"] == QUEUE_ID
        # exactly one aggregation regardless of queue count
        assert mock_db.tasks.aggregate.call_count == 1

    run()


def test_add_tasks_to_queue_not_found_404(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = None

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.add_tasks_to_queue(
                    "missing",
                    [_sample_task_data(mod)],
                    current_user=USER,
                )
            assert exc.value.status_code == 404

    run()


def test_add_tasks_to_queue_defaults_and_question_types(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    # One dedupe query for the batch, then one insert_many.
    mock_db.tasks.find.return_value = []
    mock_db.tasks.insert_many.return_value = MagicMock(inserted_ids=[ObjectId(TASK_ID)])
    task = _sample_task_data(
        mod,
        task_type=None,
        question_types=["open", "multi"],
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_tasks_to_queue("q1", [task], current_user=USER)
        assert result["tasks_added"] == 1
        task_doc = mock_db.tasks.insert_many.call_args[0][0][0]
        assert task_doc["task_type"] == "generation"
        assert task_doc["question_types"] == ["open", "multi"]
        assert task_doc["status"] == "pending"
        assert task_doc["document_kind"] == "normative"

    run()


def test_add_tasks_to_queue_preserves_document_kind(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find.return_value = []
    mock_db.tasks.insert_many.return_value = MagicMock(inserted_ids=[ObjectId(TASK_ID)])
    task = _sample_task_data(mod, document_kind="textbook")

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.add_tasks_to_queue("q1", [task], current_user=USER)
        task_doc = mock_db.tasks.insert_many.call_args[0][0][0]
        assert task_doc["document_kind"] == "textbook"

    run()


def test_add_tasks_to_queue_preserves_chunking_method_and_block_type(mod):
    """The UI's chunking method and the chunker's block type must reach the
    worker: pydantic silently drops undeclared fields, which once sent every
    «llm» run through the rules chunker."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find.return_value = []
    mock_db.tasks.insert_many.return_value = MagicMock(inserted_ids=[ObjectId(TASK_ID), ObjectId(TASK_ID)])
    chunk_task = _sample_task_data(mod, task_type="chunk", chunking_method="llm", document_kind="textbook")
    gate_task = _sample_task_data(mod, task_type="gate", block_type="inset", chunk_id=2)
    plain = _sample_task_data(mod, chunk_id=3)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.add_tasks_to_queue("q1", [chunk_task, gate_task, plain], current_user=USER)
        docs = mock_db.tasks.insert_many.call_args[0][0]
        assert docs[0]["chunking_method"] == "llm"
        assert docs[1]["block_type"] == "inset"
        assert "chunking_method" not in docs[2] and "block_type" not in docs[2]

    run()


def test_add_tasks_to_queue_dedupes_within_one_batch(mod):
    """Two identical tasks in one request must not both be inserted."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find.return_value = []
    mock_db.tasks.insert_many.return_value = MagicMock(inserted_ids=[ObjectId(TASK_ID)])
    task = _sample_task_data(mod, dataset_id=DATASET_ID)
    same = _sample_task_data(mod, dataset_id=DATASET_ID)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_tasks_to_queue("q1", [task, same], current_user=USER)
        assert result["tasks_added"] == 1
        assert len(mock_db.tasks.insert_many.call_args[0][0]) == 1

    run()


def test_add_tasks_to_queue_skips_already_queued(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find.return_value = [
        {
            "dataset_id": DATASET_ID,
            "chunk_id": 1,
            "task_type": "generation",
            "question_type": "open",
        }
    ]
    task = _sample_task_data(mod, dataset_id=DATASET_ID)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_tasks_to_queue("q1", [task], current_user=USER)
        assert result["tasks_added"] == 0
        mock_db.tasks.insert_many.assert_not_called()

    run()


def _stages(mock_db):
    """The aggregation pipeline get_queue_tasks handed to Mongo, by stage name."""
    pipeline = mock_db.tasks.aggregate.call_args[0][0]
    return pipeline, {list(st)[0]: st for st in pipeline}


def test_get_queue_tasks(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    task = {"_id": ObjectId(TASK_ID), "status": "pending"}
    mock_db.tasks.aggregate.return_value = [task]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_queue_tasks("q1", status="pending", current_user=EXPERT)
        assert len(result) == 1
        assert result[0]["_id"] == TASK_ID
        _, by_stage = _stages(mock_db)
        assert by_stage["$match"]["$match"]["status"] == "pending"
        assert by_stage["$match"]["$match"]["queue_id"] == QUEUE_ID

    run()


def test_get_queue_tasks_pages_with_a_total_order(mod):
    """skip/limit only page correctly when the sort is a total order."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.aggregate.return_value = []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.get_queue_tasks(
                "q1", limit=25, skip=50, sort_by="updated_at", sort_dir="desc",
                current_user=EXPERT,
            )
        pipeline, by_stage = _stages(mock_db)
        assert by_stage["$sort"]["$sort"] == {"updated_at": -1, "_id": 1}
        assert by_stage["$skip"]["$skip"] == 50
        assert by_stage["$limit"]["$limit"] == 25
        names = [list(st)[0] for st in pipeline]
        assert names.index("$sort") < names.index("$skip") < names.index("$limit")

    run()


def test_get_queue_tasks_unknown_sort_falls_back_to_created_at(mod):
    """A bad ?sort_by must not turn into an unindexed sort over the collection."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.aggregate.return_value = []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.get_queue_tasks("q1", sort_by="chunk_text", current_user=EXPERT)
        _, by_stage = _stages(mock_db)
        assert by_stage["$sort"]["$sort"] == {"created_at": 1, "_id": 1}

    run()


def test_get_queue_tasks_brief_projects_before_sorting(mod):
    """Dropping the large fields first is what keeps the sort within budget."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.aggregate.return_value = []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.get_queue_tasks("q1", brief=True, current_user=EXPERT)
        pipeline, by_stage = _stages(mock_db)
        assert by_stage["$project"]["$project"] == {"chunk_text": 0, "result": 0}
        names = [list(st)[0] for st in pipeline]
        assert names.index("$project") < names.index("$sort")
        assert mock_db.tasks.aggregate.call_args[1]["allowDiskUse"] is True

    run()


def test_delete_queue(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.delete_queue("q1", current_user=EXPERT)
        assert result["queue_name"] == "q1"
        assert result["datasets_cancelled"] == 0
        mock_db.tasks.delete_many.assert_called_once_with({"queue_id": QUEUE_ID})
        mock_db.queues.delete_one.assert_called_once_with({"name": "q1"})

    run()


def test_delete_queue_cancels_processing_dataset(mod):
    """Reconcile skips datasets with no tasks, so the delete must close them."""
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.datasets.find.return_value = [
        {"_id": ObjectId(DATASET_ID), "current_version": 2},
    ]

    def run():
        with (
            patch.object(mod, "get_db", return_value=mock_db),
            patch.object(mod, "_merge_dataset_metadata") as merge,
        ):
            result = mod.delete_queue("q1", current_user=EXPERT)
        assert result["datasets_cancelled"] == 1
        query = mock_db.datasets.find.call_args[0][0]
        assert query["metadata.queue_name"] == "q1"
        assert "processing" in query["metadata.status"]["$in"]
        assert "completed" not in query["metadata.status"]["$in"]
        merge.assert_called_once()
        did, version, patch_ = merge.call_args[0][1:]
        assert (did, version) == (DATASET_ID, 2)
        assert patch_["status"] == "cancelled"
        assert patch_["completed_at"]

    run()


# ---------- tasks ----------


def test_get_stuck_tasks(mod):
    mock_db = MagicMock()
    stuck = [{"_id": ObjectId(TASK_ID), "status": "processing"}]
    mock_db.tasks.find.return_value = _chain_sort_only(*stuck)
    mock_db.tasks.find.return_value.limit.return_value = stuck

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_stuck_tasks(threshold_minutes=15)
        assert len(result) == 1
        assert result[0]["_id"] == TASK_ID
        query = mock_db.tasks.find.call_args[0][0]
        assert query["status"] == "processing"
        assert "$lt" in query["updated_at"]

    run()


def _reconcile_row(**over):
    """One dataset's tally row as the reconcile $group produces it."""
    row = {
        "_id": DATASET_ID,
        "total": 0,
        "active": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "gate_total": 0,
        "gate_rejected": 0,
        "gen_total": 0,
        "completed_rejections": 0,
    }
    row.update(over)
    return row


def _reconcile_status(mod, row):
    """Run the reconcile sweep over one candidate and return the written patch."""
    mock_db = MagicMock()
    candidate = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {"status": "processing"},
    }
    mock_db.datasets.find.return_value = _chain_sort_only(candidate)
    mock_db.datasets.find.return_value.limit.return_value = [candidate]
    mock_db.tasks.aggregate.return_value = [row] if row is not None else []

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.reconcile_dataset_status(limit=10)
        if not result["finalized"]:
            return result, None
        return result, mock_db.dataset_versions.update_one.call_args[0][1]["$set"]

    return run()


def test_reconcile_dataset_status(mod):
    result, patch_set = _reconcile_status(
        mod, _reconcile_row(total=5, completed=5, gen_total=5)
    )
    assert result["scanned"] == 1
    assert result["finalized"] == 1
    assert DATASET_ID in result["dataset_ids"]
    assert patch_set["metadata.status"] == "completed"


def test_reconcile_skips_datasets_with_active_tasks(mod):
    result, patch_set = _reconcile_status(
        mod, _reconcile_row(total=5, active=2, completed=3, gen_total=5)
    )
    assert result["finalized"] == 0
    assert patch_set is None


def test_reconcile_skips_datasets_without_tasks(mod):
    # No tally row at all: the dataset has no tasks yet — leave it alone.
    result, patch_set = _reconcile_status(mod, None)
    assert result["finalized"] == 0
    assert patch_set is None


def test_reconcile_marks_partial_success_as_completed_with_failures(mod):
    """One failed generation task means questions are missing — say so."""
    _, status = _reconcile_status(
        mod, _reconcile_row(total=100, completed=99, failed=1, gen_total=100)
    )
    assert status["metadata.status"] == "completed_with_failures"


def test_reconcile_marks_total_loss_as_failed(mod):
    _, status = _reconcile_status(
        mod, _reconcile_row(total=10, failed=10, gen_total=10)
    )
    assert status["metadata.status"] == "failed"


def test_reconcile_marks_all_rejected_as_no_valid_chunks(mod):
    _, status = _reconcile_status(
        mod, _reconcile_row(total=10, completed=10, gate_total=10, gate_rejected=10,
                            completed_rejections=10)
    )
    assert status["metadata.status"] == "no_valid_chunks"
    assert status["metadata.error"] == "no_valid_chunks"


def test_reconcile_clean_run_stays_completed(mod):
    _, status = _reconcile_status(
        mod, _reconcile_row(total=10, completed=10, gen_total=10)
    )
    assert status["metadata.status"] == "completed"


def test_claim_tasks(mod):
    mock_db = MagicMock()
    claimed_task = {"_id": ObjectId(TASK_ID), "status": "pending"}
    mock_db.tasks.find_one_and_update.side_effect = [
        {**claimed_task, "status": "processing", "worker_id": "w1"},
        None,
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.claim_tasks(mod.TaskClaimRequest(worker_id="w1", limit=2))
        assert len(result) == 1
        assert result[0]["status"] == "processing"

    run()


def test_get_dataset_tasks(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    tasks = [{"_id": ObjectId(TASK_ID), "dataset_id": DATASET_ID}]
    mock_db.tasks.find.return_value = _chain_sort_only(*tasks)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset_tasks(DATASET_ID, current_user=USER)
        assert len(result) == 1
        assert result[0]["_id"] == TASK_ID

    run()


def test_get_task_found(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "status": "pending"}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_task(TASK_ID, current_user=USER)
        assert result["_id"] == TASK_ID

    run()


def test_get_task_not_found(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = None

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.get_task(TASK_ID, current_user=USER)
            assert exc.value.status_code == 404

    run()


# ---------- dataset ownership on the by-id reads ----------

_FOREIGN_DATASET = {
    "_id": ObjectId(DATASET_ID),
    "current_version": 1,
    "metadata": {"created_by": "someone-else", "queue_name": "q1", "status": "completed"},
}


def _foreign_db():
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = dict(_FOREIGN_DATASET)
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "dataset_id": DATASET_ID}
    mock_db.tasks.find.return_value = _chain_sort_only()
    mock_db.tasks.aggregate.return_value = []
    mock_db.chunks.find.return_value = _chain_sort_only()
    mock_db.dataset_versions.find_one.return_value = {"metadata": {}}
    mock_db.questions.find.return_value = _sorted_cursor([])
    return mock_db


_BY_ID_READS = [
    ("chunks", lambda mod, user: mod.get_chunks(DATASET_ID, current_user=user)),
    ("dataset_tasks", lambda mod, user: mod.get_dataset_tasks(DATASET_ID, current_user=user)),
    ("pipeline_stats", lambda mod, user: mod.get_pipeline_stats(DATASET_ID, current_user=user)),
    ("task", lambda mod, user: mod.get_task(TASK_ID, current_user=user)),
    ("queue_tasks", lambda mod, user: mod.get_queue_tasks("q1", current_user=user)),
]


@pytest.mark.parametrize("call", [c for _, c in _BY_ID_READS], ids=[n for n, _ in _BY_ID_READS])
def test_user_cannot_read_another_users_dataset(mod, call):
    """Queue listings hand out ids freely; every by-id read has to check ownership."""
    mock_db = _foreign_db()

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                call(mod, USER)
        # By-id dataset reads hide existence (404, as get_dataset does); the
        # queue is already visible by name, so that one is a plain 403.
        assert exc.value.status_code in (403, 404)
        mock_db.tasks.aggregate.assert_not_called()
        mock_db.chunks.find.assert_not_called()

    run()


@pytest.mark.parametrize("call", [c for _, c in _BY_ID_READS], ids=[n for n, _ in _BY_ID_READS])
def test_expert_reads_any_dataset(mod, call):
    mock_db = _foreign_db()

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            call(mod, EXPERT)

    run()


@pytest.mark.parametrize("call", [c for _, c in _BY_ID_READS], ids=[n for n, _ in _BY_ID_READS])
def test_owner_reads_own_dataset(mod, call):
    mock_db = _foreign_db()
    mock_db.datasets.find_one.return_value["metadata"]["created_by"] = USER.username

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            call(mod, USER)

    run()


def test_user_queue_tasks_403_when_no_dataset_feeds_the_queue(mod):
    mock_db = _foreign_db()
    mock_db.datasets.find_one.return_value = None

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.get_queue_tasks("q1", current_user=USER)
        assert exc.value.status_code == 403

    run()


def test_update_task_status_happy_path(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "status": "processing"}
    mock_db.tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.update_task_status(
                TASK_ID,
                mod.TaskStatusUpdate(status="completed", expected_status="processing"),
            )
        assert result["status"] == "completed"

    run()


def test_update_task_status_unsets_stale_message_code(mod):
    """Successful completion without message_code drops any leftover code."""
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {
        "_id": ObjectId(TASK_ID),
        "status": "processing",
        "message_code": "model_auth_failed",
        "error": "Agent API error: 500 - 401 Unauthorized",
    }
    mock_db.tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_task_status(
                TASK_ID,
                mod.TaskStatusUpdate(status="completed", expected_status="processing"),
            )
        ops = mock_db.tasks.update_one.call_args[0][1]
        assert "$unset" in ops
        assert ops["$unset"]["message_code"] == ""
        assert ops["$unset"]["error"] == ""
        assert "message_code" not in ops["$set"]

    run()


def test_update_task_status_keeps_new_message_code(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "status": "processing"}
    mock_db.tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_task_status(
                TASK_ID,
                mod.TaskStatusUpdate(
                    status="failed",
                    expected_status="processing",
                    error="Agent API error: 500 - 401 Unauthorized",
                    message_code="model_auth_failed",
                ),
            )
        ops = mock_db.tasks.update_one.call_args[0][1]
        assert ops["$set"]["message_code"] == "model_auth_failed"
        assert "$unset" not in ops

    run()


def test_cancel_task_success(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "status": "pending"}
    mock_db.tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.cancel_task(TASK_ID, current_user=EXPERT)
        assert result["status"] == "cancelled"

    run()


def test_cancel_task_non_pending_400(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID), "status": "processing"}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.cancel_task(TASK_ID, current_user=EXPERT)
            assert exc.value.status_code == 400

    run()


# ---------- add question / metadata / retry ----------


def test_add_question_happy_path(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    # One insert into the questions collection; the unique index does the dedupe.
    mock_db.dataset_versions.find_one.return_value = {"_id": 1, "metadata": {}}
    mock_db.questions.count_documents.return_value = 1
    question = _sample_question(mod)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_question_to_dataset(
                DATASET_ID, question, current_user=EXPERT
            )
        assert result["question_added"] is True
        assert result["total_questions"] == 1
        row = mock_db.questions.insert_one.call_args[0][0]
        assert row["idempotency_key"] == "1:open"
        assert row["dataset_id"] == DATASET_ID
        assert row["version"] == 1

    run()


def test_add_question_duplicate_idempotency(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    import pymongo.errors

    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.insert_one.side_effect = pymongo.errors.DuplicateKeyError("dup")
    mock_db.questions.count_documents.return_value = 1
    question = _sample_question(mod)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_question_to_dataset(
                DATASET_ID, question, current_user=EXPERT
            )
        assert result["duplicate"] is True
        assert result["question_added"] is False

    run()


def _version_with_questions(*question_ids):
    """Questions live in their own collection now; the version doc only exists."""
    return [{"question_id": qid} for qid in question_ids]


def _patch_request(mod, *patches):
    return mod.QuestionsPatchRequest(
        questions=[mod.QuestionPatch(**p) for p in patches]
    )


def test_patch_version_questions_updates_only_given_fields(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 3,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = _version_with_questions("q1", "q2")
    mock_db.questions.bulk_write.return_value = MagicMock(matched_count=1)
    update = _patch_request(mod, {"question_id": "q1", "task": "Edited?"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.patch_version_questions(
                DATASET_ID, 3, update, current_user=EXPERT
            )
        assert result["questions_updated"] == 1
        operations = mock_db.questions.bulk_write.call_args[0][0]
        assert len(operations) == 1
        changes = operations[0]._doc["$set"]
        assert changes["task"] == "Edited?"
        # untouched fields must not be written
        assert "correct_answer" not in changes
        # a content edit stamps the version as well as the question
        version_set = mock_db.dataset_versions.update_one.call_args[0][1]["$set"]
        assert version_set["user_updated_at"] is not None
        assert version_set["user_updated_by"] == "tester"

    run()


def _patch_mocks(*question_ids, rows=None):
    """`rows` replaces the bare id stubs with full stored documents when a test needs field values."""
    stored = rows if rows is not None else _version_with_questions(*question_ids)
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = stored
    mock_db.questions.bulk_write.return_value = MagicMock(matched_count=len(stored))
    return mock_db


def _patch_sets(mock_db):
    return [op._doc["$set"] for op in mock_db.questions.bulk_write.call_args[0][0]]


def test_patch_version_questions_marks_content_edit_as_user_edited(mod):
    mock_db = _patch_mocks("q1")
    update = _patch_request(mod, {"question_id": "q1", "task": "Edited?"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert changes["edit_status"] == "user_edited"
        assert isinstance(changes["edited_at"], datetime)
        assert result["content_edited"] is True
        assert result["user_updated_at"] is not None
        version_set = mock_db.dataset_versions.update_one.call_args[0][1]["$set"]
        assert version_set["user_updated_by"] == "tester"

    run()


def test_patch_version_questions_rating_edit_is_user_edited(mod):
    """Sensitivity / difficulty are user judgements: changing them is an edit too."""
    mock_db = _patch_mocks("q1")
    update = _patch_request(mod, {"question_id": "q1", "difficulty": "1"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert changes["edit_status"] == "user_edited"

    run()


def test_patch_version_questions_export_toggle_is_not_an_edit(mod):
    """Include-in-export is a preference: no edit_status, no version stamp."""
    mock_db = _patch_mocks("q1")
    update = _patch_request(mod, {"question_id": "q1", "excluded_from_export": True})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert changes == {"excluded_from_export": True}
        mock_db.dataset_versions.update_one.assert_not_called()
        assert result["questions_updated"] == 1
        assert result["content_edited"] is False
        assert result["user_updated_at"] is None

    run()


def test_patch_version_questions_mixed_batch_stamps_version_once(mod):
    mock_db = _patch_mocks("q1", "q2")
    update = _patch_request(
        mod,
        {"question_id": "q1", "excluded_from_export": False},
        {"question_id": "q2", "task": "Edited?"},
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        toggle, edit = _patch_sets(mock_db)
        assert "edit_status" not in toggle
        assert edit["edit_status"] == "user_edited"
        mock_db.dataset_versions.update_one.assert_called_once()

    run()


def test_patch_version_questions_ignores_client_edit_status(mod):
    """The status is derived server-side; a client cannot mark a toggle as an edit."""
    mock_db = _patch_mocks("q1")
    update = mod.QuestionsPatchRequest(
        questions=[
            mod.QuestionPatch.model_validate(
                {"question_id": "q1", "excluded_from_export": True, "edit_status": "paraphrased"}
            )
        ]
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert "edit_status" not in changes

    run()


def test_patch_version_questions_keeps_original_rating_on_first_change(mod):
    """The model's rating survives the first manual change as <field>_original."""
    mock_db = _patch_mocks(rows=[{"question_id": "q1", "provocativeness": "2", "difficulty": "1"}])
    update = _patch_request(mod, {"question_id": "q1", "provocativeness": "3"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert changes["provocativeness"] == "3"
        assert changes["provocativeness_original"] == "2"
        assert "difficulty_original" not in changes
        assert changes["edit_status"] == "user_edited"

    run()


def test_patch_version_questions_never_overwrites_original_rating(mod):
    """A second change keeps the first original — the model's value, not the previous pick."""
    mock_db = _patch_mocks(
        rows=[{"question_id": "q1", "provocativeness": "3", "provocativeness_original": "2"}]
    )
    update = _patch_request(mod, {"question_id": "q1", "provocativeness": "1"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert changes["provocativeness"] == "1"
        assert "provocativeness_original" not in changes

    run()


def test_patch_version_questions_skips_original_when_unchanged_or_absent(mod):
    """No original for a value re-sent as is or for a rating the model never produced; clearing one keeps it."""
    mock_db = _patch_mocks(
        rows=[
            {"question_id": "q1", "provocativeness": "2", "difficulty": None},
            {"question_id": "q2", "provocativeness": "2", "difficulty": "1"},
        ]
    )
    update = _patch_request(
        mod,
        {"question_id": "q1", "provocativeness": "2", "difficulty": "2"},
        {"question_id": "q2", "difficulty": None},
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        first, second = _patch_sets(mock_db)
        assert "provocativeness_original" not in first
        assert "difficulty_original" not in first
        assert second["difficulty"] is None
        assert second["difficulty_original"] == "1"

    run()


def test_patch_version_questions_ignores_client_original_rating(mod):
    """The original is derived server-side; a client cannot plant one."""
    mock_db = _patch_mocks(rows=[{"question_id": "q1", "provocativeness": "2"}])
    update = mod.QuestionsPatchRequest(
        questions=[
            mod.QuestionPatch.model_validate(
                {"question_id": "q1", "task": "Edited?", "provocativeness_original": "1"}
            )
        ]
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        (changes,) = _patch_sets(mock_db)
        assert "provocativeness_original" not in changes
        assert changes["task"] == "Edited?"

    run()


def test_patch_version_questions_does_not_create_a_version(mod):
    """Manual edits stay inside the version being viewed — only transform branches."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = _version_with_questions("q1")
    mock_db.questions.bulk_write.return_value = MagicMock(matched_count=1)
    update = _patch_request(mod, {"question_id": "q1", "excluded_from_export": True})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        mock_db.datasets.find_one_and_update.assert_not_called()
        mock_db.dataset_versions.insert_one.assert_not_called()

    run()


def test_patch_version_questions_accepts_older_version(mod):
    """Any version is editable, not just the current one."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 5,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = _version_with_questions("q1")
    mock_db.questions.bulk_write.return_value = MagicMock(matched_count=1)
    update = _patch_request(mod, {"question_id": "q1", "task": "Edited?"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.patch_version_questions(
                DATASET_ID, 2, update, current_user=EXPERT
            )
        assert result["version"] == 2
        version_filter = mock_db.dataset_versions.find_one.call_args[0][0]
        assert version_filter["version"] == 2

    run()


def test_patch_version_questions_explains_missing_ids_from_old_data(mod):
    """Legacy questions have no id at all; point at the backfill, not at ids."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = [{"question_id": "q1"}, {}, {}]
    update = _patch_request(mod, {"question_id": "ds:00001", "task": "Edited?"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.patch_version_questions(
                    DATASET_ID, 1, update, current_user=EXPERT
                )
        assert exc.value.status_code == 409
        assert "questions_not_identified" in exc.value.detail
        assert "backfill_data.py" in exc.value.detail

    run()


def test_patch_version_questions_unknown_id_409(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = _version_with_questions("q1")
    update = _patch_request(mod, {"question_id": "ghost", "task": "Edited?"})

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.patch_version_questions(
                    DATASET_ID, 1, update, current_user=EXPERT
                )
        assert exc.value.status_code == 409
        assert "ghost" in exc.value.detail
        mock_db.questions.bulk_write.assert_not_called()

    run()


def test_patch_version_questions_ignores_pipeline_only_fields(mod):
    """A patch cannot rewrite pipeline-owned data such as the source chunk."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1}
    mock_db.questions.find.return_value = _version_with_questions("q1")
    mock_db.questions.bulk_write.return_value = MagicMock(matched_count=1)
    update = mod.QuestionsPatchRequest(
        questions=[
            mod.QuestionPatch.model_validate(
                {"question_id": "q1", "task": "Edited?", "source_chunk": "hacked"}
            )
        ]
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.patch_version_questions(DATASET_ID, 1, update, current_user=EXPERT)
        changes = mock_db.questions.bulk_write.call_args[0][0][0]._doc["$set"]
        assert "source_chunk" not in changes

    run()


def test_add_question_never_writes_into_the_version_document(mod):
    """The 16 MB ceiling came from embedding questions in the version doc."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": 1, "metadata": {}}
    mock_db.questions.count_documents.return_value = 1

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.add_question_to_dataset(
                DATASET_ID, _sample_question(mod), current_user=EXPERT
            )
        # metadata counters are still merged, but no question data goes there
        for call in mock_db.dataset_versions.update_one.call_args_list:
            written = call[0][1].get("$set", {})
            assert not any(k.startswith("questions") for k in written)
        mock_db.questions.insert_one.assert_called_once()

    run()


def test_update_dataset_writes_questions_as_rows(mod):
    mock_db = _completed_dataset_mocks(mod)
    update = mod.DatasetUpdate(
        questions=[_sample_question(mod), _sample_question(mod)], base_version=1
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        version_doc = mock_db.dataset_versions.insert_one.call_args[0][0]
        assert "questions" not in version_doc
        rows = mock_db.questions.insert_many.call_args[0][0]
        assert len(rows) == 2
        assert all(r["version"] == 2 and r["dataset_id"] == DATASET_ID for r in rows)

    run()


def test_update_dataset_keeps_edit_status_on_rows(mod):
    """Transform tells the server which questions it paraphrased; the rows keep it."""
    mock_db = _completed_dataset_mocks(mod)
    paraphrased = mod.QuestionData(
        chunk_id=1,
        question_type="open",
        task="Reworded?",
        options="",
        correct_answer="x",
        provocativeness="2",
        edit_status="paraphrased",
        edited_at="2026-09-08T10:00:00Z",
    )
    update = mod.DatasetUpdate(questions=[paraphrased, _sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        first, second = mock_db.questions.insert_many.call_args[0][0]
        assert first["edit_status"] == "paraphrased"
        assert isinstance(first["edited_at"], datetime)
        assert second.get("edit_status") is None
        assert second.get("edited_at") is None

    run()


def test_update_dataset_keeps_original_ratings_on_rows(mod):
    """A new version (transform) carries the model's pre-edit ratings along."""
    mock_db = _completed_dataset_mocks(mod)
    rated = mod.QuestionData(
        chunk_id=1,
        question_type="one",
        task="Which?",
        options={"option_1": "a", "option_2": "b"},
        correct_answer="1",
        provocativeness="3",
        provocativeness_original="2",
        difficulty="1",
        difficulty_original="3",
    )
    update = mod.DatasetUpdate(questions=[rated, _sample_question(mod)], base_version=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_dataset(DATASET_ID, update, current_user=EXPERT)
        first, second = mock_db.questions.insert_many.call_args[0][0]
        assert first["provocativeness_original"] == "2"
        assert first["difficulty_original"] == "3"
        assert second.get("provocativeness_original") is None
        assert second.get("difficulty_original") is None

    run()


def test_question_data_rejects_unknown_edit_status(mod):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        mod.QuestionData(
            chunk_id=1,
            question_type="open",
            task="?",
            options="",
            correct_answer="x",
            provocativeness="2",
            edit_status="whatever",
        )


def test_delete_dataset_removes_questions_too(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    mock_db.fs.files.find.return_value = []

    def run():
        with (
            patch.object(mod, "get_db", return_value=mock_db),
            patch.object(mod, "get_gridfs", return_value=MagicMock()),
        ):
            mod.delete_dataset(DATASET_ID, current_user=EXPERT)
        mock_db.questions.delete_many.assert_called_once_with({"dataset_id": DATASET_ID})

    run()


def test_get_dataset_still_serves_unmigrated_questions(mod):
    """Deploy and data migration do not have to happen at the same moment."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "DS",
        "description": "d",
        "source_document": "doc",
        "current_version": 1,
        "created_at": "c",
        "updated_at": "u",
    }
    # collection empty, questions still embedded in the version document
    mock_db.questions.find.return_value = _sorted_cursor([])
    mock_db.dataset_versions.find_one.return_value = {
        "metadata": {},
        "questions": [{"chunk_id": 7}],
    }

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_dataset(DATASET_ID, current_user=USER)
        assert result["questions"] == [{"chunk_id": 7, "validation_is_current": False}]

    run()


def test_add_question_folds_in_an_unmigrated_version_first(mod):
    """Otherwise the collection would hold only post-deploy questions."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.side_effect = [
        {"questions": [{"question_id": "old"}]},   # slice probe: not migrated
        {"questions": [{"question_id": "old"}]},   # full legacy read
        {"metadata": {}},
    ]
    mock_db.questions.count_documents.return_value = 2

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.add_question_to_dataset(
                DATASET_ID, _sample_question(mod), current_user=EXPERT
            )
        moved = mock_db.questions.insert_many.call_args[0][0]
        assert moved[0]["question_id"] == "old"
        unset = [
            c for c in mock_db.dataset_versions.update_one.call_args_list
            if "$unset" in c[0][1]
        ]
        assert unset, "the embedded array must be dropped once moved"

    run()


def test_patch_dataset_metadata(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {"status": "processing"},
    }
    mock_db.dataset_versions.find_one.return_value = {
        "metadata": {"status": "processing"},
    }

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.patch_dataset_metadata(
                DATASET_ID, {"status": "completed"}, current_user=EXPERT
            )
        assert result["dataset_id"] == DATASET_ID
        assert result["version"] == 1
        version_set = mock_db.dataset_versions.update_one.call_args[0][1]["$set"]
        assert version_set["metadata.status"] == "completed"

    run()


def test_retry_failed_tasks(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    failed = [
        {"dataset_id": DATASET_ID, "status": "failed"},
    ]
    mock_db.tasks.find.return_value = failed
    mock_db.tasks.update_many.return_value = MagicMock(modified_count=1)
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {},
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": "v1"}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.retry_failed_tasks("q1", current_user=EXPERT)
        assert result["tasks_retried"] == 1
        assert result["datasets_reopened"] == 1
        set_fields = mock_db.tasks.update_many.call_args[0][1]["$set"]
        assert set_fields["error"] is None
        assert set_fields["message_code"] is None
        assert set_fields["status"] == "pending"

    run()


def test_retry_all_failed_tasks(mod):
    mock_db = MagicMock()
    mock_db.tasks.count_documents.return_value = 2
    mock_db.tasks.distinct.return_value = [DATASET_ID]
    mock_db.tasks.update_many.return_value = MagicMock(modified_count=2)
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
        "metadata": {},
    }
    mock_db.dataset_versions.find_one.return_value = {"_id": "v1"}

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.retry_all_failed_tasks(current_user=EXPERT)
        assert result["tasks_retried"] == 2
        assert result["datasets_reopened"] == 1
        set_fields = mock_db.tasks.update_many.call_args[0][1]["$set"]
        assert set_fields["message_code"] is None

    run()


def test_retry_all_failed_tasks_none_found(mod):
    mock_db = MagicMock()
    mock_db.tasks.count_documents.return_value = 0

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.retry_all_failed_tasks(current_user=EXPERT)
        assert result["tasks_retried"] == 0

    run()


# ---------- chunks ----------


def test_save_chunks_persists_gate_pending(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    chunk = mod.ChunkCreate(
        chunk_index=0,
        chunk_text="hello",
        gate_pending=True,
        gate_passed=False,
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mock_db.chunks.find_one.return_value = None
            result = mod.save_chunks(DATASET_ID, [chunk], current_user=EXPERT)
        assert result["chunks_saved"] == 1
        saved_doc = mock_db.chunks.update_one.call_args[0][1]["$set"]
        assert saved_doc["gate_pending"] is True
        assert saved_doc["gate_passed"] is False

    run()


def test_get_chunks_gate_passed_only_filter(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    chunks = [{"_id": ObjectId(), "chunk_index": 0, "gate_passed": True}]
    mock_db.chunks.find.return_value = _chain_sort_only(*chunks)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_chunks(DATASET_ID, gate_passed_only=True, current_user=USER)
        assert len(result) == 1
        query = mock_db.chunks.find.call_args[0][0]
        assert query["gate_passed"] is True

    run()


def test_get_chunks_all_when_filter_off(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    mock_db.chunks.find.return_value = _chain_sort_only()

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.get_chunks(DATASET_ID, gate_passed_only=False, current_user=USER)
        query = mock_db.chunks.find.call_args[0][0]
        assert "gate_passed" not in query

    run()


def test_get_pipeline_stats_aggregates_gate_and_generation(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "ds_full",
        "source_document": "doc.docx",
        "current_version": 1,
    }
    # pipeline-stats reads verdict fields only, so no sort()/full documents.
    mock_db.questions.find.return_value = [
        {"validation_passed": True, "validation_score": "9/10"},
        {"validation_passed": False, "validation_score": "4/10"},
        {"validation_score": "8/10", "validation_threshold": "6"},
    ]
    mock_db.dataset_versions.find_one.return_value = {
        "metadata": {
            "pipeline_mode": "full",
            "gate_enabled": True,
            "ablation_testing": True,
            "question_types": ["one", "multi"],
            "queue_name": "q1",
            "status": "processing",
            "total_chunks": 3,
            "chunks_passed_gate": 2,
            "chunks_rejected": 1,
            "expected_questions": 4,
            "total_questions_generated": 3,
        },
    }
    mock_db.tasks.aggregate.return_value = _task_groups([
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "gate", "status": "completed"},
        {"task_type": "gate", "status": "completed"},
        {"task_type": "gate", "status": "skipped"},
        {"task_type": "generation", "status": "completed"},
        {"task_type": "generation", "status": "pending"},
        {"task_type": "generation", "status": "processing"},
        {"task_type": "generation", "status": "failed"},
    ])
    mock_db.chunks.find.return_value = [
        {
            "chunk_index": 0,
            "gate_passed": True,
            "gate_pending": False,
            "question_types_valid": ["one", "multi"],
            "fragment_data": {"block_type": "inset"},
            "gate_result": {
                "c1_chunk_informative": [1],
                "c2_chunk_reference_clarity": [1],
                "c3_chunk_multi_suitability": [1],
                "c1_confidence": 0.9,
                "c2_confidence": 0.8,
                "c3_confidence": 0.7,
            },
        },
        {
            "chunk_index": 1,
            "gate_passed": True,
            "gate_pending": False,
            "question_types_valid": ["one"],
            "gate_result": {
                "c1_chunk_informative": [1],
                "c2_chunk_reference_clarity": [1],
                "c3_chunk_multi_suitability": [0],
                "c1_confidence": 0.95,
                "c2_confidence": 0.85,
                "c3_confidence": 0.6,
            },
        },
        {
            "chunk_index": 2,
            "gate_passed": False,
            "gate_pending": False,
            "question_types_valid": [],
            "gate_result": {
                "c1_chunk_informative": [0],
                "c2_chunk_reference_clarity": [1],
                "c3_chunk_multi_suitability": [0],
                "c1_confidence": 0.5,
                "c2_confidence": 0.7,
                "c3_confidence": 0.4,
                "rejection_reason": "chunk_not_informative",
            },
        },
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_pipeline_stats(DATASET_ID, current_user=USER)

        assert result["meta"]["pipeline_mode"] == "full"
        assert result["meta"]["ablation_testing"] is True
        assert result["chunking"]["total_chunks"] == 3
        assert result["chunking"]["state"] == "completed"
        assert result["gate"]["state"] == "completed"
        assert result["gate"]["passed"] == 2
        assert result["gate"]["rejected"] == 1
        assert result["gate"]["rejection_reasons"] == {"chunk_not_informative": 1}
        # Per block type: the inset passed; legacy chunks without a type are body.
        assert result["gate"]["by_block_type"]["inset"] == {
            "total": 1, "passed": 1, "rejected": 0, "pending": 0,
        }
        assert result["gate"]["by_block_type"]["body"]["total"] == 2
        assert result["gate"]["by_block_type"]["body"]["rejected"] == 1
        assert result["meta"]["chunking_dropped_blocks"] == {}
        assert result["gate"]["criteria"]["c1_pass"] == 2
        assert result["gate"]["criteria"]["c2_pass"] == 3
        assert result["gate"]["criteria"]["c3_pass"] == 1
        assert result["gate"]["question_types_valid"]["one"] == 2
        assert result["gate"]["question_types_valid"]["multi"] == 1
        assert result["generation"]["total"] == 4
        assert result["generation"]["by_status"]["completed"] == 1
        assert result["generation"]["by_status"]["pending"] == 1
        assert result["generation"]["validation"]["passed"] == 2
        assert result["generation"]["validation"]["failed"] == 1
        assert result["generation"]["validation"]["avg_score"] == pytest.approx(7.0)

    run()


def test_get_pipeline_stats_separates_completed_from_terminal(mod):
    """`done` says the phase stopped moving; `completed` says how much came out of it.

    The progress page shows `completed` (a failed task produced no question) but
    needs `done` to know the run is over, so the two must not be conflated.
    """
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "ds",
        "current_version": 1,
    }
    mock_db.questions.find.return_value = []
    mock_db.dataset_versions.find_one.return_value = {
        "metadata": {
            "pipeline_mode": "full",
            "gate_enabled": True,
            "total_chunks": 2,
            "chunks_passed_gate": 2,
            # Deliberately stale: the live task count must win over metadata.
            "expected_questions": 3,
            "total_questions_generated": 7,
        },
    }
    mock_db.tasks.aggregate.return_value = _task_groups(
        [
            {"task_type": "chunk", "status": "completed"},
            {"task_type": "gate", "status": "completed"},
            {"task_type": "gate", "status": "completed"},
        ]
        + [{"task_type": "generation", "status": "completed"}] * 7
        + [{"task_type": "generation", "status": "failed"}] * 2
        + [{"task_type": "generation", "status": "pending"}] * 1,
        gen_models=["gen-a", "gen-a", "gen-b"],
        val_models=["val-a", None],
    )
    mock_db.chunks.find.return_value = [
        {"chunk_index": i, "gate_passed": True, "gate_pending": False, "gate_result": {}}
        for i in range(2)
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_pipeline_stats(DATASET_ID, current_user=USER)

        gen = result["generation"]
        assert gen["total"] == 10          # counted, not read from expected_questions
        assert gen["completed"] == 7       # what the UI shows as the numerator
        assert gen["done"] == 9            # completed + failed: work that is over
        assert gen["state"] == "active"    # one task still pending
        assert result["models"] == {
            "generation": ["gen-a", "gen-b"],
            "validation": ["val-a"],
        }

    run()


def test_get_pipeline_stats_gate_disabled_for_ablation_mode(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "ds__generator_validator",
        "source_document": "doc.docx",
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {
        "questions": [],
        "metadata": {
            "pipeline_mode": "generator_validator",
            "gate_enabled": False,
            "total_chunks": 5,
            "status": "processing",
        },
    }
    mock_db.tasks.aggregate.return_value = _task_groups([
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "generation", "status": "pending"},
    ])
    mock_db.chunks.find.return_value = [
        {"chunk_index": i, "gate_passed": True, "gate_pending": False, "gate_result": {}}
        for i in range(5)
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_pipeline_stats(DATASET_ID, current_user=USER)
        assert result["gate"]["state"] == "disabled"
        assert result["meta"]["gate_enabled"] is False
        assert result["generation"]["state"] == "active"

    run()


def test_get_pipeline_stats_skips_pending_gate_chunks(mod):
    """Chunks waiting for a gate verdict must not count as rejected."""
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "name": "ds_gate_pending",
        "source_document": "doc.docx",
        "current_version": 1,
    }
    mock_db.dataset_versions.find_one.return_value = {
        "questions": [],
        "metadata": {
            "pipeline_mode": "full",
            "gate_enabled": True,
            "total_chunks": 4,
            "status": "processing",
        },
    }
    mock_db.tasks.aggregate.return_value = _task_groups([
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "gate", "status": "completed"},
        {"task_type": "gate", "status": "completed"},
        {"task_type": "gate", "status": "pending"},
        {"task_type": "gate", "status": "pending"},
    ])
    mock_db.chunks.find.return_value = [
        {
            "chunk_index": 0,
            "gate_passed": True,
            "gate_pending": False,
            "question_types_valid": ["one"],
            "gate_result": {
                "c1_chunk_informative": [1],
                "c2_chunk_reference_clarity": [1],
                "c3_chunk_multi_suitability": [1],
            },
        },
        {
            "chunk_index": 1,
            "gate_passed": False,
            "gate_pending": False,
            "question_types_valid": [],
            "gate_result": {
                "c1_chunk_informative": [0],
                "rejection_reason": "chunk_not_informative",
            },
        },
        {
            "chunk_index": 2,
            "gate_passed": False,
            "gate_pending": True,
            "gate_result": None,
            "question_types_valid": [],
        },
        {
            "chunk_index": 3,
            "gate_passed": False,
            "gate_pending": True,
            "gate_result": None,
            "question_types_valid": [],
        },
    ]

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.get_pipeline_stats(DATASET_ID, current_user=USER)

        assert result["gate"]["state"] == "active"
        assert result["gate"]["total"] == 4
        assert result["gate"]["done"] == 2
        assert result["gate"]["passed"] == 1
        assert result["gate"]["rejected"] == 1
        assert result["gate"]["rejection_reasons"] == {"chunk_not_informative": 1}
        assert "unknown" not in result["gate"]["rejection_reasons"]

    run()


def test_delete_chunks(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    mock_db.chunks.delete_many.return_value = MagicMock(deleted_count=4)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.delete_chunks(DATASET_ID, current_user=EXPERT)
        assert result["chunks_deleted"] == 4

    run()


def test_update_chunk_gate_success(mod):
    mock_db = MagicMock()
    mock_db.chunks.update_one.return_value = MagicMock(matched_count=1)
    update = mod.ChunkGateUpdate(
        gate_result={"score": 0.9},
        gate_passed=True,
        question_types_valid=["open"],
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.update_chunk_gate(DATASET_ID, 0, update)
        assert result["gate_passed"] is True
        set_doc = mock_db.chunks.update_one.call_args[0][1]["$set"]
        assert set_doc["gate_pending"] is False
        assert set_doc["question_types_valid"] == ["open"]

    run()


def test_update_chunk_gate_not_found_404(mod):
    mock_db = MagicMock()
    mock_db.chunks.update_one.return_value = MagicMock(matched_count=0)
    mock_db.chunks.find_one.return_value = None
    update = mod.ChunkGateUpdate(gate_result={}, gate_passed=False)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.update_chunk_gate(DATASET_ID, 99, update)
            assert exc.value.status_code == 404

    run()


def test_bump_gate_progress_increments_both_collections(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 2,
    }
    update = mod.GateProgressUpdate(passed=True, tasks_added=3)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.bump_gate_progress(DATASET_ID, update)
        assert result["dataset_id"] == DATASET_ID
        ds_inc = mock_db.datasets.update_one.call_args[0][1]["$inc"]
        ver_inc = mock_db.dataset_versions.update_one.call_args[0][1]["$inc"]
        assert ds_inc["metadata.chunks_passed_gate"] == 1
        assert ds_inc["metadata.chunks_rejected"] == 0
        assert ds_inc["metadata.expected_questions"] == 3
        assert ver_inc == ds_inc
        version_filter = mock_db.dataset_versions.update_one.call_args[0][0]
        assert version_filter == {"dataset_id": DATASET_ID, "version": 2}

    run()


def test_bump_gate_progress_rejected(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {
        "_id": ObjectId(DATASET_ID),
        "current_version": 1,
    }
    update = mod.GateProgressUpdate(passed=False, tasks_added=0)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.bump_gate_progress(DATASET_ID, update)
        ds_inc = mock_db.datasets.update_one.call_args[0][1]["$inc"]
        assert ds_inc["metadata.chunks_passed_gate"] == 0
        assert ds_inc["metadata.chunks_rejected"] == 1

    run()


def test_bump_gate_progress_dataset_not_found_404(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = None
    update = mod.GateProgressUpdate(passed=True)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.bump_gate_progress(DATASET_ID, update)
            assert exc.value.status_code == 404

    run()


def test_save_chunks_preserves_existing_gate_verdict(mod):
    mock_db = MagicMock()
    mock_db.datasets.find_one.return_value = {"_id": ObjectId(DATASET_ID)}
    mock_db.chunks.find_one.return_value = {
        "gate_result": {"passed": True},
        "gate_pending": False,
        "gate_passed": True,
        "question_types_valid": ["one"],
    }
    chunk = mod.ChunkCreate(
        chunk_index=1,
        chunk_text="hello",
        gate_pending=True,
        gate_passed=False,
    )

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.save_chunks(DATASET_ID, [chunk], current_user=EXPERT)
        saved_doc = mock_db.chunks.update_one.call_args[0][1]["$set"]
        assert saved_doc["gate_pending"] is False
        assert saved_doc["gate_passed"] is True
        assert saved_doc["question_types_valid"] == ["one"]

    run()


def test_add_tasks_skips_existing_duplicates(mod):
    mock_db = MagicMock()
    mock_db.queues.find_one.return_value = {"_id": ObjectId(QUEUE_ID), "name": "q1"}
    mock_db.tasks.find_one.return_value = {"_id": ObjectId(TASK_ID)}
    task = _sample_task_data(mod, dataset_id=DATASET_ID)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.add_tasks_to_queue("q1", [task], current_user=USER)
        assert result["tasks_added"] == 0
        mock_db.tasks.insert_one.assert_not_called()

    run()


def test_update_task_status_includes_worker_id_in_cas(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {
        "_id": ObjectId(TASK_ID),
        "status": "processing",
        "worker_id": "w1",
    }
    mock_db.tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            mod.update_task_status(
                TASK_ID,
                mod.TaskStatusUpdate(
                    status="completed",
                    expected_status="processing",
                    worker_id="w1",
                ),
            )
        filt = mock_db.tasks.update_one.call_args[0][0]
        assert filt["worker_id"] == "w1"
        assert filt["status"] == "processing"

    run()


def test_update_task_status_terminal_identity_is_noop(mod):
    mock_db = MagicMock()
    mock_db.tasks.find_one.return_value = {
        "_id": ObjectId(TASK_ID),
        "status": "completed",
        "result": {"kept": True},
    }

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.update_task_status(
                TASK_ID,
                mod.TaskStatusUpdate(status="completed", result={"overwrite": True}),
            )
        assert result["status"] == "completed"
        mock_db.tasks.update_one.assert_not_called()

    run()
