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

_REPO = Path(__file__).resolve().parent.parent
_TASK_WORKER = _REPO / "task_worker"
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from task_worker_loader import load_task_worker_module


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
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    stuck = [
        {"_id": "t1", "attempts": 0},
        {"_id": "t2", "attempts": 1},
    ]
    w = TaskWorker()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(return_value=stuck)

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/tasks/stuck"):
            return fake_resp
        return MagicMock(status_code=404)

    with (
        patch.object(w, "_request", side_effect=fake_request) as req_mock,
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        w.recover_stuck_tasks()

    # Threshold passed via query string, not buried in default.
    stuck_call = next(c for c in req_mock.call_args_list if c.args[0] == "GET")
    assert stuck_call.kwargs["params"]["threshold_minutes"]
    # Both tasks were requeued (default cap is 3); each gets attempts bumped.
    calls = upd.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("t1", "pending")
    assert calls[0].kwargs == {"attempts": 1, "expected_status": "processing", "check_owner": False}
    assert calls[1].args == ("t2", "pending")
    assert calls[1].kwargs == {"attempts": 2, "expected_status": "processing", "check_owner": False}


def test_recover_stuck_tasks_force_fails_when_attempts_exceed_cap():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    # Pretend the task already hit the cap on this round.
    stuck = [{"_id": "poison", "attempts": mod.WORKER_MAX_TASK_ATTEMPTS - 1}]
    w = TaskWorker()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(return_value=stuck)

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/tasks/stuck"):
            return fake_resp
        return MagicMock(status_code=404)

    with (
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        w.recover_stuck_tasks()

    assert upd.call_count == 1
    args, kwargs = upd.call_args
    assert args[0] == "poison"
    assert args[1] == "failed"
    assert kwargs["attempts"] == mod.WORKER_MAX_TASK_ATTEMPTS
    assert kwargs["expected_status"] == "processing"
    assert kwargs["check_owner"] is False
    assert "Force-failed" in kwargs["error"]


def _stuck_request(stuck, put_status=200):
    """Fake ``_request``: GET /tasks/stuck -> ``stuck``; PUT .../status -> ``put_status``."""
    stuck_resp = MagicMock(status_code=200)
    stuck_resp.json = MagicMock(return_value=stuck)
    put_calls = []

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/tasks/stuck"):
            return stuck_resp
        if method == "PUT" and url.endswith("/status"):
            put_calls.append((url, kwargs.get("json")))
            return MagicMock(status_code=put_status)
        return MagicMock(status_code=404)

    return fake_request, put_calls


def test_recover_stuck_tasks_does_not_send_worker_id():
    """Recovery must not put ``worker_id`` into the CAS payload: the stuck task
    belongs to a dead/foreign worker, so an owner check would always 409."""
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    stuck = [
        {"_id": "orphan", "attempts": 0},
        {"_id": "poison", "attempts": mod.WORKER_MAX_TASK_ATTEMPTS - 1},
    ]
    fake_request, put_calls = _stuck_request(stuck)
    with patch.object(w, "_request", side_effect=fake_request):
        w.recover_stuck_tasks()

    assert len(put_calls) == 2
    for url, payload in put_calls:
        assert "worker_id" not in payload, (url, payload)
        assert payload["expected_status"] == "processing"
    assert put_calls[0][1]["status"] == "pending"
    assert put_calls[1][1]["status"] == "failed"


def test_update_task_status_sends_worker_id_by_default():
    """Heartbeat / complete / fail of our own task keep the owner check."""
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    with patch.object(w, "_request", return_value=MagicMock(status_code=200)) as req:
        assert w.update_task_status("abc", "processing", expected_status="processing") is True
        assert w.update_task_status("abc", "completed", result={"x": 1}, check_owner=True) is True
        assert w.update_task_status("abc", "pending", check_owner=False) is True

    payloads = [c.kwargs["json"] for c in req.call_args_list]
    assert payloads[0]["worker_id"] == w.worker_id
    assert payloads[1]["worker_id"] == w.worker_id
    assert "worker_id" not in payloads[2]


def test_complete_task_keeps_owner_check():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    with patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd:
        assert w._complete_task("abc", "completed", result={"x": 1}) is True

    assert upd.call_count == 1
    assert upd.call_args.kwargs.get("check_owner", True) is True


def test_recover_stuck_tasks_does_not_count_cas_conflicts():
    """A 409 on requeue/force-fail must not be reported as a recovery."""
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    stuck = [
        {"_id": "orphan", "attempts": 0},
        {"_id": "poison", "attempts": mod.WORKER_MAX_TASK_ATTEMPTS - 1},
    ]
    fake_request, put_calls = _stuck_request(stuck, put_status=409)
    with (
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(mod, "logger") as log,
    ):
        w.recover_stuck_tasks()

    assert len(put_calls) == 2
    info_msgs = [str(c.args[0]) for c in log.info.call_args_list]
    warn_msgs = [str(c.args[0]) for c in log.warning.call_args_list]
    assert any("requeued=0, force_failed=0" in m for m in info_msgs), info_msgs
    assert not any("Reset stuck task" in m for m in info_msgs)
    assert not any("Force-failed stuck task" in m for m in warn_msgs)
    assert any("orphan" in m for m in warn_msgs), warn_msgs
    assert any("poison" in m for m in warn_msgs), warn_msgs


def test_recover_stuck_tasks_counts_only_successful_updates():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    stuck = [{"_id": "a", "attempts": 0}, {"_id": "b", "attempts": 0}]
    stuck_resp = MagicMock(status_code=200)
    stuck_resp.json = MagicMock(return_value=stuck)
    with (
        patch.object(w, "_request", return_value=stuck_resp),
        patch.object(w, "update_task_status", MagicMock(side_effect=[True, False])),
        patch.object(mod, "logger") as log,
    ):
        w.recover_stuck_tasks()

    info_msgs = [str(c.args[0]) for c in log.info.call_args_list]
    assert any("requeued=1, force_failed=0" in m for m in info_msgs), info_msgs


# ---------- Graceful stop in process_batch ----------


def _batch_patches(w, mod, process_task):
    return (
        patch.object(w, "process_task", side_effect=process_task),
        patch.object(w, "_complete_task", MagicMock(return_value=True)),
        patch.object(w, "recover_stuck_tasks", MagicMock()),
        patch.object(w, "check_dataset_completion", MagicMock()),
        patch.object(w, "reconcile_dataset_status", MagicMock()),
        patch.object(mod.time, "sleep", MagicMock()),
    )


def test_process_batch_requeues_unstarted_tasks_on_stop():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    tasks = [{"_id": "t1", "dataset_id": "ds"}, {"_id": "t2", "dataset_id": "ds"}, {"_id": "t3"}]

    def process_task(task):
        # SIGTERM arrives while the first task is running.
        w.stop.set()
        return None, "failed"

    p1, p2, p3, p4, p5, p6 = _batch_patches(w, mod, process_task)
    with (
        p1 as pt, p2, p3 as rec, p4 as done, p5, p6,
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
    ):
        w.process_batch(tasks)

    assert pt.call_count == 1
    assert pt.call_args.args[0]["_id"] == "t1"
    # Remaining tasks go back to pending with our owner check (we claimed them).
    assert [c.args for c in upd.call_args_list] == [("t2", "pending"), ("t3", "pending")]
    for c in upd.call_args_list:
        assert c.kwargs.get("expected_status") == "processing"
        assert c.kwargs.get("check_owner", True) is True
    # No recovery between tasks once we are stopping.
    rec.assert_not_called()
    # Dataset of the task we did finish is still checked for completion.
    done.assert_called_once()


def test_process_batch_processes_all_tasks_without_stop():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    tasks = [{"_id": "t1"}, {"_id": "t2"}]
    p1, p2, p3, p4, p5, p6 = _batch_patches(w, mod, lambda task: (None, "failed"))
    with (
        p1 as pt, p2, p3, p4, p5, p6,
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
    ):
        w.process_batch(tasks)

    assert pt.call_count == 2
    upd.assert_not_called()


def test_process_batch_stop_set_before_start_requeues_everything():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    w.stop.set()
    tasks = [{"_id": "t1"}, {"_id": "t2"}]
    p1, p2, p3, p4, p5, p6 = _batch_patches(w, mod, lambda task: (None, "failed"))
    with (
        p1 as pt, p2, p3, p4, p5, p6,
        patch.object(w, "update_task_status", MagicMock(side_effect=[True, False])) as upd,
        patch.object(mod, "logger") as log,
    ):
        w.process_batch(tasks)

    pt.assert_not_called()
    assert [c.args for c in upd.call_args_list] == [("t1", "pending"), ("t2", "pending")]
    info_msgs = [str(c.args[0]) for c in log.info.call_args_list]
    assert any("requeued 1/2" in m for m in info_msgs), info_msgs


def test_heartbeat_thread_refreshes_processing_status():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    with (
        patch.object(mod, "WORKER_HEARTBEAT_SECONDS", 0.05),
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
    ):
        stop, _lost = w._start_heartbeat("abc123")
        time.sleep(0.18)  # ~3 ticks at 50ms
        stop.set()
        time.sleep(0.1)  # let the thread observe the stop event

    assert upd.call_count >= 2, f"heartbeat fired only {upd.call_count} times"
    for call in upd.call_args_list:
        assert call.args == ("abc123", "processing")
        assert call.kwargs.get("expected_status") == "processing"


def test_process_task_starts_and_stops_heartbeat():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    fake_lost = MagicMock()
    fake_lost.is_set.return_value = False
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
        patch.object(w, "_start_heartbeat", return_value=(fake_event, fake_lost)) as hb,
        patch.object(w, "_request", return_value=response),
    ):
        w.process_task(task)

    hb.assert_called_once_with("abc")
    fake_event.set.assert_called_once()


def test_process_task_stops_heartbeat_even_on_agent_error():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    fake_lost = MagicMock()
    fake_lost.is_set.return_value = False
    response = MagicMock(status_code=500, text='{"detail":"boom"}')
    response.json = MagicMock(return_value={"detail": "boom"})
    task = {
        "_id": "abc",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 1,
        "dataset_id": None,
    }
    with (
        patch.object(w, "update_task_status", MagicMock()),
        patch.object(w, "_start_heartbeat", return_value=(fake_event, fake_lost)),
        patch.object(w, "_request", return_value=response),
    ):
        result, status = w.process_task(task)

    assert result is None
    assert status == "failed"
    fake_event.set.assert_called_once()


def test_process_task_stops_heartbeat_on_unexpected_exception():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    fake_event = MagicMock()
    fake_lost = MagicMock()
    fake_lost.is_set.return_value = False
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
        patch.object(w, "_start_heartbeat", return_value=(fake_event, fake_lost)),
        patch.object(w, "_request", side_effect=boom),
    ):
        result, status = w.process_task(task)

    assert result is None
    assert status == "failed"
    fake_event.set.assert_called_once()


def test_heartbeat_stops_when_cas_fails():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    with (
        patch.object(mod, "WORKER_HEARTBEAT_SECONDS", 0.05),
        patch.object(w, "update_task_status", MagicMock(return_value=False)) as upd,
    ):
        stop, lost = w._start_heartbeat("abc123")
        time.sleep(0.15)
        stop.set()
        time.sleep(0.1)

    assert upd.call_count >= 1
    assert lost.is_set()


def test_recover_stuck_tasks_skips_unprocessed_batch_tasks():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    stuck = [
        {"_id": "in-batch", "attempts": 0},
        {"_id": "orphan", "attempts": 0},
    ]
    w = TaskWorker()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(return_value=stuck)

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/tasks/stuck"):
            return fake_resp
        return MagicMock(status_code=404)

    with (
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "update_task_status", MagicMock()) as upd,
    ):
        w.recover_stuck_tasks(exclude_task_ids={"in-batch"})

    assert upd.call_count == 1
    assert upd.call_args.args[0] == "orphan"


def test_format_http_error_parses_json_detail():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    resp = MagicMock(status_code=400)
    resp.json = MagicMock(return_value={"detail": "bad prompt"})
    resp.text = '{"detail":"bad prompt"}'
    msg = TaskWorker._format_http_error(resp, prefix="Agent API error: ")
    assert "400" in msg
    assert "bad prompt" in msg


def test_save_question_treats_duplicate_as_success():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    task = {
        "_id": "t1",
        "chunk_id": 7,
        "chunk_text": "hello world " * 5,
        "question_type": "one",
        "dataset_id": "ds1",
        "dataset_name": "D",
    }
    result = {
        "result": {
            "output": {
                "generated_question": {"task": "Q?", "outputs": "A"},
                "sensitivity_score": {"provocativeness_score": "1"},
                "difficulty_score": {"difficulty": "2"},
                "validation_result": {"passed": True, "total": 5, "max_total": 5},
            }
        }
    }
    fake_resp = MagicMock(status_code=200)
    fake_resp.json = MagicMock(
        return_value={"duplicate": True, "total_questions": 3, "question_added": False}
    )

    with (
        patch.object(w, "_request", return_value=fake_resp),
        patch.object(w, "update_dataset_progress", MagicMock()) as prog,
    ):
        assert w.save_question_to_dataset(task, result) is True

    prog.assert_not_called()


def test_process_task_aborts_when_heartbeat_loses_ownership():
    mod = load_task_worker_module()
    TaskWorker = mod.TaskWorker

    w = TaskWorker()
    stop = MagicMock()
    lost = MagicMock()
    lost.is_set = MagicMock(side_effect=[False, True])
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
        "dataset_id": "ds1",
    }
    with (
        patch.object(w, "_start_heartbeat", return_value=(stop, lost)),
        patch.object(w, "_request", return_value=response),
        patch.object(w, "save_question_to_dataset", MagicMock()) as save,
        patch.object(w, "_complete_task", MagicMock()) as complete,
    ):
        result, status = w.process_task(task)

    assert result is None
    assert status == "aborted"
    save.assert_not_called()
    complete.assert_not_called()
