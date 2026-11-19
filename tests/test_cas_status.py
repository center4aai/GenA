"""CAS (compare-and-swap) status update tests."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from dataset_api_loader import load_dataset_api_module


@pytest.fixture
def api_module():
    return load_dataset_api_module()


def test_cas_conflict_returns_409(api_module):
    """When expected_status does not match, update returns 409."""
    mod = api_module
    mock_db = MagicMock()
    mock_tasks = MagicMock()
    mock_db.tasks = mock_tasks
    mock_tasks.find_one.return_value = {"_id": "abc", "status": "processing"}
    mock_tasks.update_one.return_value = MagicMock(matched_count=0)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            with pytest.raises(HTTPException) as exc:
                mod.update_task_status(
                    "507f1f77bcf86cd799439011",
                    mod.TaskStatusUpdate(status="completed", expected_status="pending"),
                )
            assert exc.value.status_code == 409

    run()


def test_cas_success_when_expected_matches(api_module):
    mod = api_module
    mock_db = MagicMock()
    mock_tasks = MagicMock()
    mock_db.tasks = mock_tasks
    mock_tasks.find_one.return_value = {"_id": "abc", "status": "processing"}
    mock_tasks.update_one.return_value = MagicMock(matched_count=1)

    def run():
        with patch.object(mod, "get_db", return_value=mock_db):
            result = mod.update_task_status(
                "507f1f77bcf86cd799439011",
                mod.TaskStatusUpdate(status="completed", expected_status="processing"),
            )
            assert result["status"] == "completed"
            filter_query = mock_tasks.update_one.call_args[0][0]
            assert filter_query["status"] == "processing"

    run()
