"""Tests for _merge_dataset_metadata dual-write behavior."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from bson import ObjectId

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from dataset_api_loader import load_dataset_api_module

_api = load_dataset_api_module()
_merge_dataset_metadata = _api._merge_dataset_metadata


DATASET_ID = "507f1f77bcf86cd799439011"
VERSION = 2


def _make_db(
    version_meta=None,
    dataset_meta=None,
    version_exists=True,
    dataset_exists=True,
):
    version_doc = {"metadata": version_meta or {"status": "processing", "total_tasks": 5}}
    dataset_doc = {"metadata": dataset_meta or {"status": "pending"}}

    db = MagicMock()
    db.dataset_versions.find_one.return_value = version_doc if version_exists else None
    db.datasets.find_one.return_value = dataset_doc if dataset_exists else None
    return db


def test_merge_combines_existing_metadata_with_patch():
    db = _make_db(
        version_meta={"status": "processing", "queue_name": "Q1"},
        dataset_meta={"status": "pending", "extra": "keep"},
    )
    patch = {"status": "completed", "completed_at": "2026-01-01T00:00:00"}

    result = _merge_dataset_metadata(db, DATASET_ID, VERSION, patch)

    assert result == {
        "status": "completed",
        "queue_name": "Q1",
        "completed_at": "2026-01-01T00:00:00",
    }
    db.dataset_versions.update_one.assert_called_once()
    db.datasets.update_one.assert_called_once()

    # Dotted paths: replacing the whole metadata object rolled back the $inc
    # counters written concurrently by bump_gate_progress.
    version_set = db.dataset_versions.update_one.call_args[0][1]["$set"]
    dataset_set = db.datasets.update_one.call_args[0][1]["$set"]
    assert version_set == {
        "metadata.status": "completed",
        "metadata.completed_at": "2026-01-01T00:00:00",
    }
    assert dataset_set["metadata.status"] == "completed"
    assert dataset_set["metadata.completed_at"] == "2026-01-01T00:00:00"
    assert "metadata" not in dataset_set  # untouched keys are left alone
    assert "updated_at" in dataset_set


def test_merge_returns_patch_when_version_missing():
    db = _make_db(version_exists=False)
    patch = {"status": "processing"}
    result = _merge_dataset_metadata(db, DATASET_ID, VERSION, patch)
    assert result == patch
    db.dataset_versions.update_one.assert_not_called()
    db.datasets.update_one.assert_not_called()


def test_merge_returns_patch_when_dataset_missing():
    db = _make_db(dataset_exists=False)
    patch = {"status": "processing"}
    result = _merge_dataset_metadata(db, DATASET_ID, VERSION, patch)
    assert result == patch
    db.dataset_versions.update_one.assert_not_called()
    db.datasets.update_one.assert_not_called()


def test_merge_uses_objectid_for_dataset_lookup():
    db = _make_db()
    _merge_dataset_metadata(db, DATASET_ID, VERSION, {"status": "processing"})
    db.datasets.find_one.assert_called_once_with(
        {"_id": ObjectId(DATASET_ID)}, {"_id": 1}
    )


def test_merge_skips_writes_when_patch_empty():
    """An empty dotted $set is rejected by MongoDB, and there is nothing to write."""
    db = _make_db(
        version_meta={"status": "completed"},
        dataset_meta={"status": "completed"},
    )
    result = _merge_dataset_metadata(db, DATASET_ID, VERSION, {})
    assert result == {}
    db.dataset_versions.update_one.assert_not_called()
    db.datasets.update_one.assert_not_called()


def test_merge_overwrites_conflicting_keys_with_patch():
    db = _make_db(
        version_meta={"status": "processing", "total_tasks": 10},
        dataset_meta={"status": "processing", "total_tasks": 10},
    )
    result = _merge_dataset_metadata(db, DATASET_ID, VERSION, {"total_tasks": 12})
    assert result["total_tasks"] == 12
    assert result["status"] == "processing"
