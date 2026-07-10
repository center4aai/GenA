"""Regression tests for periodic stuck-task recovery, heartbeat and the
max-attempts cap added to ``task_worker/worker.py``.

The original code only ran ``recover_stuck_tasks`` once at startup and used
the default 10-minute threshold, so a worker that crashed and was restarted
within 10 minutes would leave its in-flight task wedged in ``processing``
forever (observed on main: queue ``queue_2. Уголовный кодекс РФ part_…``).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_REPO = Path(__file__).resolve().parent.parent
_TASK_WORKER = _REPO / "task_worker"
if str(_TASK_WORKER) not in sys.path:
    sys.path.insert(0, str(_TASK_WORKER))


# ---------- Source guards ----------


def test_worker_imports_new_config_knobs():
    src = (_TASK_WORKER / "worker.py").read_text(encoding="utf-8")
    for name in (
        "WORKER_STUCK_THRESHOLD_MINUTES",
        "WORKER_RECOVERY_INTERVAL_SECONDS",
        "WORKER_HEARTBEAT_SECONDS",
        "WORKER_MAX_TASK_ATTEMPTS",
    ):
        assert name in src, f"worker.py must import {name}"


def test_worker_run_loop_calls_recovery_periodically():
    src = (_TASK_WORKER / "worker.py").read_text(encoding="utf-8")
    assert "last_recovery_at" in src
    assert "WORKER_RECOVERY_INTERVAL_SECONDS" in src


def test_worker_process_task_starts_heartbeat():
    src = (_TASK_WORKER / "worker.py").read_text(encoding="utf-8")
    assert "_start_heartbeat" in src
    assert "heartbeat_stop.set()" in src


def test_dataset_api_task_status_update_accepts_attempts():
    src = (_REPO / "dataset_api" / "dataset_api.py").read_text(encoding="utf-8")
    assert "attempts: Optional[int]" in src
    assert 'update_data["attempts"] = status_update.attempts' in src


# ---------- Behavioural tests ----------


def test_recover_stuck_tasks_requeues_and_increments_attempts():
    from worker import TaskWorker

    stuck = [
        {"_id": "t1", "attempts": 0},
        {"_id": "t2", "attempts": 1},
    ]
    w = TaskWorker()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(return_value=stuck)
    with (
        patch.object(w.session, "get", return_value=fake_resp) as get_mock,
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        w.recover_stuck_tasks()

    # Threshold passed via query string, not buried in default.
    _, kwargs = get_mock.call_args
    assert kwargs["params"]["threshold_minutes"]
    # Both tasks were requeued (default cap is 3); each gets attempts bumped.
    calls = upd.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("t1", "pending")
    assert calls[0].kwargs == {"attempts": 1}
    assert calls[1].args == ("t2", "pending")
    assert calls[1].kwargs == {"attempts": 2}


def test_recover_stuck_tasks_force_fails_when_attempts_exceed_cap():
    from worker import TaskWorker
    import worker as worker_mod

    # Pretend the task already hit the cap on this round.
    stuck = [{"_id": "poison", "attempts": worker_mod.WORKER_MAX_TASK_ATTEMPTS - 1}]
    w = TaskWorker()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(return_value=stuck)
    with (
        patch.object(w.session, "get", return_value=fake_resp),
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        w.recover_stuck_tasks()

    assert upd.call_count == 1
    args, kwargs = upd.call_args
    assert args[0] == "poison"
    assert args[1] == "failed"
    assert kwargs["attempts"] == worker_mod.WORKER_MAX_TASK_ATTEMPTS
    assert "Force-failed" in kwargs["error"]


def test_heartbeat_thread_refreshes_processing_status():
    from worker import TaskWorker
    import worker as worker_mod

    w = TaskWorker()
    with (
        patch.object(worker_mod, "WORKER_HEARTBEAT_SECONDS", 0.05),
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        stop = w._start_heartbeat("abc123")
        time.sleep(0.18)  # ~3 ticks at 50ms
        stop.set()
        time.sleep(0.1)  # let the thread observe the stop event

    assert upd.call_count >= 2, f"heartbeat fired only {upd.call_count} times"
    for call in upd.call_args_list:
        assert call.args == ("abc123", "processing")


def test_process_task_starts_and_stops_heartbeat():
    from worker import TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    response = MagicMock(status_code=200)
    response.json = MagicMock(
        return_value={"result": {"output": {"generated_question": {"task": "Q", "outputs": "A"}}}}
    )
    response.text = "{}"
    task = {
        "_id": "abc",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 1,
        "dataset_id": None,
    }
    with (
        patch.object(w, "update_task_status", MagicMock()),
        patch.object(w, "save_question_to_dataset", MagicMock()),
        patch.object(w, "_start_heartbeat", return_value=fake_event) as hb,
        patch("worker.requests.post", return_value=response),
    ):
        w.process_task(task)

    hb.assert_called_once_with("abc")
    fake_event.set.assert_called_once()


def test_process_task_stops_heartbeat_even_on_agent_error():
    from worker import TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    response = MagicMock(status_code=500, text="boom")
    task = {
        "_id": "abc",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 1,
        "dataset_id": None,
    }
    with (
        patch.object(w, "update_task_status", MagicMock()),
        patch.object(w, "_start_heartbeat", return_value=fake_event),
        patch("worker.requests.post", return_value=response),
    ):
        result = w.process_task(task)

    assert result is None
    fake_event.set.assert_called_once()


def test_process_task_stops_heartbeat_on_unexpected_exception():
    from worker import TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    task = {
        "_id": "abc",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 1,
        "dataset_id": None,
    }

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    with (
        patch.object(w, "update_task_status", MagicMock()),
        patch.object(w, "_start_heartbeat", return_value=fake_event),
        patch("worker.requests.post", side_effect=boom),
    ):
        result = w.process_task(task)

    assert result is None
    fake_event.set.assert_called_once()
