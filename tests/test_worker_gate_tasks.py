"""Tests for async chunk-gate support in task_worker/worker.py."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from task_worker_loader import load_task_worker_module


def _worker():
    mod = load_task_worker_module()
    return mod, mod.TaskWorker


def _gate_task(**overrides):
    base = {
        "_id": "gate-1",
        "task_type": "gate",
        "chunk_text": "x" * 50,
        "chunk_id": 3,
        "dataset_id": "ds-abc",
        "question_type": "one",
        "question_types": ["one", "multi", "open"],
        "queue_name": "q-test",
        "source_document": "doc.pdf",
        "dataset_name": "Test DS",
    }
    base.update(overrides)
    return base


def _heartbeat_patches(w):
    stop = threading.Event()
    lost = threading.Event()
    return patch.object(w, "_start_heartbeat", return_value=(stop, lost))


# ---------- _gate_score ----------


def test_gate_score_list_one():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score([1]) == 1


def test_gate_score_list_zero():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score([0]) == 0


def test_gate_score_bool_true():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score(True) == 1


def test_gate_score_bool_false():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score(False) == 0


def test_gate_score_int_one():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score(1) == 1


def test_gate_score_int_zero():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score(0) == 0


def test_gate_score_string_one():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score("1") == 1


def test_gate_score_string_true():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score("true") == 1


def test_gate_score_string_zero():
    _, TaskWorker = _worker()
    assert TaskWorker._gate_score("0") == 0


def test_gate_score_garbage_strings():
    _, TaskWorker = _worker()
    for value in ("yes", "no", "garbage", "", "  "):
        assert TaskWorker._gate_score(value) == 0


# ---------- _filename_from_disposition ----------


def test_filename_from_disposition_prefers_rfc5987_cyrillic():
    _, TaskWorker = _worker()
    header = (
        'attachment; filename="1.pdf"; '
        "filename*=UTF-8''%D1%83%D1%87%D0%B5%D0%B1%D0%BD%D0%B8%D0%BA.pdf"
    )
    assert TaskWorker._filename_from_disposition(header) == "учебник.pdf"


def test_filename_from_disposition_plain_fallback():
    _, TaskWorker = _worker()
    header = 'attachment; filename="report.docx"'
    assert TaskWorker._filename_from_disposition(header) == "report.docx"


def test_filename_from_disposition_empty():
    _, TaskWorker = _worker()
    assert TaskWorker._filename_from_disposition("") is None


# ---------- evaluate_gate ----------


def test_evaluate_gate_all_pass_all_types():
    _, TaskWorker = _worker()
    gate = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    types = ["one", "multi", "open"]
    passed, types_ok = TaskWorker.evaluate_gate(gate, types)
    assert passed is True
    assert types_ok == types


def test_evaluate_gate_c1_fail():
    _, TaskWorker = _worker()
    gate = {
        "c1_chunk_informative": [0],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    passed, types_ok = TaskWorker.evaluate_gate(gate, ["one", "multi"])
    assert passed is False
    assert types_ok == []


def test_evaluate_gate_c2_fail():
    _, TaskWorker = _worker()
    gate = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [0],
        "c3_chunk_multi_suitability": [1],
    }
    passed, types_ok = TaskWorker.evaluate_gate(gate, ["one", "multi"])
    assert passed is False
    assert types_ok == []


def test_evaluate_gate_c3_zero_drops_multi_keeps_one():
    _, TaskWorker = _worker()
    gate = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [0],
    }
    passed, types_ok = TaskWorker.evaluate_gate(gate, ["one", "multi"])
    assert passed is True
    assert types_ok == ["one"]


def test_evaluate_gate_c3_zero_multi_only_fails():
    _, TaskWorker = _worker()
    gate = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [0],
    }
    passed, types_ok = TaskWorker.evaluate_gate(gate, ["multi"])
    assert passed is False
    assert types_ok == []


def test_evaluate_gate_disabled_by_pipeline_mode():
    _, TaskWorker = _worker()
    gate = {"rejection_reason": "gate_disabled_by_pipeline_mode"}
    types = ["one", "multi", "open"]
    passed, types_ok = TaskWorker.evaluate_gate(gate, types)
    assert passed is True
    assert types_ok == types


def test_evaluate_gate_synthetic_pass_without_criteria():
    _, TaskWorker = _worker()
    gate = {"passed": True}
    types = ["one", "multi", "open"]
    passed, types_ok = TaskWorker.evaluate_gate(gate, types)
    assert passed is True
    assert types_ok == types


# ---------- process_gate_task ----------


def test_process_gate_task_pass_enqueues_generation_tasks():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    gate_result = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            resp = MagicMock(status_code=200)
            return resp
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"tasks_added": 3})
            return resp
        if method == "POST" and url.endswith("/gate-progress"):
            resp = MagicMock(status_code=200)
            return resp
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request) as req,
        patch.object(w, "_complete_task", MagicMock()) as complete,
    ):
        summary, status = w.process_gate_task(task)

    assert status == "gate_done"
    assert summary["types_ok"] == ["one", "multi", "open"]
    assert summary["tasks_added"] == 3
    complete.assert_called_once()
    assert complete.call_args.args[1] == "completed"
    assert complete.call_args.kwargs.get("gate_passed") is True

    put_calls = [c for c in req.call_args_list if c.args[0] == "PUT"]
    assert len(put_calls) == 1
    assert put_calls[0].kwargs["json"]["gate_passed"] is True
    assert put_calls[0].kwargs["json"]["question_types_valid"] == ["one", "multi", "open"]

    enqueue_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and "/queues/q-test/tasks/" in c.args[1]
    ]
    assert len(enqueue_calls) == 1
    gen_tasks = enqueue_calls[0].kwargs["json"]
    assert len(gen_tasks) == 3
    for gt in gen_tasks:
        assert gt["task_type"] == "generation"
        assert gt["chunk_pre_validated"] is True
        assert gt["document_kind"] == "normative"
        # Гейт должен опережать генерацию, иначе этап отбора тянется до конца прогона.
        assert gt["priority"] == mod.TASK_PRIORITY_GENERATION
        assert gt["priority"] < mod.TASK_PRIORITY_GATE < mod.TASK_PRIORITY_CHUNK
    assert {gt["question_type"] for gt in gen_tasks} == {"one", "multi", "open"}

    progress_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and c.args[1].endswith("/gate-progress")
    ]
    assert len(progress_calls) == 1
    assert progress_calls[0].kwargs["json"] == {"passed": True, "tasks_added": 3}


def test_process_gate_task_reject_skips_without_enqueue():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    gate_result = {
        "c1_chunk_informative": [0],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
        "rejection_reason": "not_informative",
    }

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            return MagicMock(status_code=200)
        if method == "POST" and url.endswith("/gate-progress"):
            return MagicMock(status_code=200)
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request) as req,
        patch.object(w, "_complete_task", MagicMock()) as complete,
    ):
        summary, status = w.process_gate_task(task)

    assert status == "gate_rejected"
    assert summary["types_ok"] == []

    enqueue_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and "/queues/" in c.args[1]
    ]
    assert enqueue_calls == []

    put_calls = [c for c in req.call_args_list if c.args[0] == "PUT"]
    assert put_calls[0].kwargs["json"]["gate_passed"] is False

    progress_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and c.args[1].endswith("/gate-progress")
    ]
    assert progress_calls[0].kwargs["json"] == {"passed": False, "tasks_added": 0}

    complete.assert_called_once()
    args, kwargs = complete.call_args
    assert args[0] == "gate-1"
    assert args[1] == "completed"
    assert kwargs.get("gate_passed") is False
    assert kwargs.get("message_code") == "gate_rejected_not_informative"


def test_process_gate_task_agent_500_fails():
    _, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    fail_resp = MagicMock(status_code=500, text='{"detail":"boom"}')
    fail_resp.json = MagicMock(return_value={"detail": "boom"})

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", return_value=fail_resp) as req,
        patch.object(w, "_complete_task", MagicMock()) as complete,
        patch.object(w, "_save_chunk_gate", MagicMock()) as save,
        patch.object(w, "_bump_gate_progress", MagicMock()) as bump,
    ):
        result, status = w.process_gate_task(task)

    assert result is None
    assert status == "failed"
    complete.assert_called_once()
    assert complete.call_args.args[1] == "failed"
    # Чанк остаётся gate_pending: вердикт не сохраняем, прогресс не двигаем,
    # генерацию не ставим. Единственный HTTP-вызов — сам /chunk_gate/.
    save.assert_not_called()
    bump.assert_not_called()
    assert req.call_count == 1
    assert req.call_args.args[1].endswith("/chunk_gate/")
    assert req.call_args.kwargs.get("retries") == 1


def _run_gate_with_http_error(status_code, detail):
    """Gate-задача при не-2xx от /chunk_gate/: возвращает
    (terminal_status, update_task_status kwargs, _request mock)."""
    _, TaskWorker = _worker()
    w = TaskWorker()
    fail_resp = MagicMock(status_code=status_code, text=f'{{"detail":"{detail}"}}')
    fail_resp.json = MagicMock(return_value={"detail": detail})

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", return_value=fail_resp) as req,
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
    ):
        result, status = w.process_gate_task(_gate_task())

    assert result is None
    assert upd.call_count == 1
    assert upd.call_args.args == ("gate-1", "failed")
    return status, upd.call_args.kwargs, req


@pytest.mark.parametrize(
    "status_code, detail, expected_code",
    [
        (401, "gate llm auth failed", "model_auth_failed"),
        (504, "gate llm timeout", "gate_timeout"),
        (503, "gate llm busy", "gate_overloaded"),
        (502, "gate llm unreachable", "gate_unavailable"),
        (500, "gate llm error", "model_error"),
        (500, "gate parse error", "model_output_invalid"),
        (500, "Internal server error", "gate_unavailable"),
        (404, "Not Found", "gate_unavailable"),
    ],
)
def test_process_gate_task_http_error_fails_with_message_code(status_code, detail, expected_code):
    status, kwargs, req = _run_gate_with_http_error(status_code, detail)
    assert status == "failed"
    assert kwargs["message_code"] == expected_code
    assert kwargs["error"] == f"Gate API error: {status_code}: {detail}"
    assert kwargs["expected_status"] == "processing"
    # Ни PUT .../gate, ни POST .../gate-progress, ни enqueue — только сам gate-вызов.
    assert [c.args[1].rsplit("/", 2)[-2:] for c in req.call_args_list] == [["chunk_gate", ""]]


def test_process_gate_task_timeout_exception_fails_without_save():
    _, TaskWorker = _worker()
    w = TaskWorker()
    with (
        _heartbeat_patches(w),
        patch.object(
            w, "_request", side_effect=requests.exceptions.Timeout("Read timed out")
        ) as req,
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
        patch.object(w, "_save_chunk_gate", MagicMock()) as save,
    ):
        result, status = w.process_gate_task(_gate_task())

    assert (result, status) == (None, "failed")
    assert req.call_count == 1
    save.assert_not_called()
    assert upd.call_args.args == ("gate-1", "failed")
    assert upd.call_args.kwargs["message_code"] == "gate_timeout"


def test_process_gate_task_enqueue_failure_fails():
    _, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    gate_result = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            return MagicMock(status_code=200)
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp = MagicMock(status_code=500, text='{"detail":"queue down"}')
            resp.json = MagicMock(return_value={"detail": "queue down"})
            return resp
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "_complete_task", MagicMock()) as complete,
    ):
        result, status = w.process_gate_task(task)

    assert result is None
    assert status == "failed"
    complete.assert_called_once()
    assert complete.call_args.args[1] == "failed"
    assert "Enqueue generation failed" in complete.call_args.kwargs["error"]


def test_process_gate_task_defaults_question_types_from_question_type():
    _, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    del task["question_types"]
    task["question_type"] = "open"
    gate_result = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    captured_gate_payload = {}

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            captured_gate_payload.update(kwargs.get("json", {}))
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            return MagicMock(status_code=200)
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"tasks_added": 1})
            return resp
        if method == "POST" and url.endswith("/gate-progress"):
            return MagicMock(status_code=200)
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request) as req,
        patch.object(w, "_complete_task", MagicMock()),
    ):
        summary, status = w.process_gate_task(task)

    assert status == "gate_done"
    assert captured_gate_payload["question_type"] == "open"
    assert captured_gate_payload["document_kind"] == "normative"
    assert "block_type" not in captured_gate_payload
    assert summary["types_ok"] == ["open"]

    enqueue_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and "/queues/q-test/tasks/" in c.args[1]
    ]
    gen_tasks = enqueue_calls[0].kwargs["json"]
    assert len(gen_tasks) == 1
    assert gen_tasks[0]["question_type"] == "open"


def test_process_gate_task_propagates_document_kind():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task(document_kind="textbook")
    gate_result = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            return MagicMock(status_code=200)
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"tasks_added": 3})
            return resp
        if method == "POST" and url.endswith("/gate-progress"):
            return MagicMock(status_code=200)
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request) as req,
        patch.object(w, "_complete_task", MagicMock()),
    ):
        summary, status = w.process_gate_task(task)

    assert status == "gate_done"
    enqueue_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and "/queues/q-test/tasks/" in c.args[1]
    ]
    gen_tasks = enqueue_calls[0].kwargs["json"]
    assert all(gt["document_kind"] == "textbook" for gt in gen_tasks)


# ---------- process_task dispatch ----------


def test_process_task_dispatches_gate_tasks():
    _, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task()
    expected = ({"gate_result": {}}, "gate_done")

    with patch.object(w, "process_gate_task", return_value=expected) as gate_fn:
        result, status = w.process_task(task)

    gate_fn.assert_called_once_with(task)
    assert result == expected[0]
    assert status == "gate_done"


# ---------- no_valid_chunks finalization ----------


def test_is_no_valid_chunks_when_all_gates_rejected():
    _, TaskWorker = _worker()
    tasks = [
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "gate", "status": "completed", "gate_passed": False},
        {"task_type": "gate", "status": "completed", "gate_passed": False},
    ]
    assert TaskWorker._is_no_valid_chunks(tasks) is True


def test_is_no_valid_chunks_legacy_skipped():
    _, TaskWorker = _worker()
    tasks = [
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "gate", "status": "skipped"},
        {"task_type": "gate", "status": "skipped"},
    ]
    assert TaskWorker._is_no_valid_chunks(tasks) is True


def test_is_no_valid_chunks_false_when_generation_exists():
    _, TaskWorker = _worker()
    tasks = [
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "gate", "status": "completed", "gate_passed": False},
        {"task_type": "generation", "status": "pending"},
    ]
    assert TaskWorker._is_no_valid_chunks(tasks) is False


def test_is_no_valid_chunks_false_without_gate_tasks():
    _, TaskWorker = _worker()
    tasks = [
        {"task_type": "chunk", "status": "completed"},
        {"task_type": "generation", "status": "completed"},
    ]
    assert TaskWorker._is_no_valid_chunks(tasks) is False


def test_finalize_dataset_no_valid_chunks_status():
    _, TaskWorker = _worker()
    w = TaskWorker()
    dataset_tasks = [
        {"_id": "c1", "task_type": "chunk", "status": "completed"},
        {"_id": "g1", "task_type": "gate", "status": "completed", "gate_passed": False},
        {"_id": "g2", "task_type": "gate", "status": "completed", "gate_passed": False},
    ]
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={})
        return resp

    with patch.object(w, "_request", side_effect=fake_request):
        w.finalize_dataset(
            "ds-1",
            "Test DS",
            completed_count=3,
            failed_count=0,
            skipped_count=2,
            total_tasks=3,
            dataset_tasks=dataset_tasks,
        )

    assert captured["json"]["status"] == "no_valid_chunks"
    assert captured["json"]["error"] == "no_valid_chunks"
    assert captured["json"]["completed_tasks"] == 1  # chunk only; rejections subtracted
    assert "/datasets/ds-1/metadata" in captured["url"]


def _finalize_status(completed_count, failed_count, total_tasks, dataset_tasks=None):
    """Run finalize_dataset and return the status it patched onto the dataset."""
    _, TaskWorker = _worker()
    w = TaskWorker()
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={})
        return resp

    with patch.object(w, "_request", side_effect=fake_request):
        w.finalize_dataset(
            "ds-1",
            "Test DS",
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=0,
            total_tasks=total_tasks,
            dataset_tasks=dataset_tasks if dataset_tasks is not None else [],
        )
    return captured["json"]["status"]


def test_finalize_marks_partial_success_as_completed_with_failures():
    """A run that lost questions must not be reported as a clean "completed"."""
    assert _finalize_status(completed_count=99, failed_count=1, total_tasks=100) == (
        "completed_with_failures"
    )
    assert _finalize_status(completed_count=1, failed_count=99, total_tasks=100) == (
        "completed_with_failures"
    )


def test_finalize_marks_total_loss_as_failed():
    assert _finalize_status(completed_count=0, failed_count=10, total_tasks=10) == "failed"


def test_finalize_clean_run_stays_completed():
    assert _finalize_status(completed_count=10, failed_count=0, total_tasks=10) == "completed"


def test_check_dataset_completion_finalizes_no_valid_chunks():
    _, TaskWorker = _worker()
    w = TaskWorker()
    dataset_tasks = [
        {"_id": "c1", "task_type": "chunk", "status": "completed"},
        {"_id": "g1", "task_type": "gate", "status": "completed", "gate_passed": False},
        {"_id": "g2", "task_type": "gate", "status": "completed", "gate_passed": False},
    ]

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/tasks"):
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value=dataset_tasks)
            return resp
        return MagicMock(status_code=200)

    with (
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "finalize_dataset", MagicMock()) as finalize,
    ):
        w.check_dataset_completion("ds-1", "Test DS")

    finalize.assert_called_once()
    kwargs = finalize.call_args.kwargs
    assert kwargs["dataset_tasks"] == dataset_tasks
    assert finalize.call_args.args[0] == "ds-1"
    assert kwargs.get("skipped_count") == 2 or finalize.call_args.args[4] == 2


def test_classify_error_auth_and_parse():
    _, TaskWorker = _worker()
    assert TaskWorker._classify_error(
        'Agent API error: 500: {"detail":"401 Unauthorized"}'
    ) == "model_auth_failed"
    # Константные detail agent_api: auth модели — и для генерации, и для gate.
    assert TaskWorker._classify_error("Agent API error: 401: llm auth failed") == "model_auth_failed"
    assert TaskWorker._classify_error("Gate API error: 401: gate llm auth failed") == "model_auth_failed"
    assert TaskWorker._classify_error("Gate API error: 500: gate parse error") == "model_output_invalid"
    assert TaskWorker._classify_error("Gate API error: 500: gate llm error") == "model_error"
    # «401» как подстрока другого числа — не статус.
    assert TaskWorker._classify_error("Chunk 401 is empty or too short (length: 3)") == "chunk_too_short"
    assert TaskWorker._classify_error(
        'Agent API error: 500: Failed to parse StructuredQuestionOutput from completion {...}'
    ) == "model_output_invalid"
    assert TaskWorker._classify_error("Chunker error: 500: boom") == "chunker_failed"


def test_classify_error_gate_service_kinds():
    _, TaskWorker = _worker()
    # Distinct causes surfaced by agent_api's /chunk_gate/ constant details.
    assert TaskWorker._classify_error("Gate API error: 504: gate llm timeout") == "gate_timeout"
    assert TaskWorker._classify_error("Gate request failed: timeout") == "gate_timeout"
    # requests.Timeout text says "timed out", not "timeout".
    assert TaskWorker._classify_error(
        "Gate request failed: HTTPConnectionPool(host='agent_api', port=8790): Read timed out."
    ) == "gate_timeout"
    assert TaskWorker._classify_error("Gate API error: 503: gate llm busy") == "gate_overloaded"
    assert TaskWorker._classify_error("Gate API error: 502: gate llm unreachable") == "gate_unavailable"
    # Sanitized legacy form still lands on the timeout code.
    assert TaskWorker._classify_error(
        "Gate API error: 500: Internal server error (timeout)"
    ) == "gate_timeout"
    assert TaskWorker._classify_error("Gate API error: 500: Internal server error") == "gate_unavailable"
    assert TaskWorker._classify_error("Gate processing error: boom") == "gate_unavailable"


def test_format_http_error_strips_completion_dump():
    _, TaskWorker = _worker()
    resp = MagicMock(status_code=500)
    resp.json = MagicMock(
        return_value={
            "detail": (
                'Failed to parse StructuredQuestionOutput from completion '
                '{"task": "long text that should be truncated"}'
            )
        }
    )
    resp.text = ""
    msg = TaskWorker._format_http_error(resp, prefix="Agent API error: ")
    assert "from completion" in msg
    assert "long text" not in msg
    assert len(msg) <= 500


def test_gate_message_code_passed_multi_dropped():
    _, TaskWorker = _worker()
    code = TaskWorker._gate_message_code(
        True, None, question_types=["one", "multi"], types_ok=["one"]
    )
    assert code == "gate_passed_chunk_not_suitable_for_multi"
    assert TaskWorker._gate_message_code(False, "chunk_not_informative") == (
        "gate_rejected_chunk_not_informative"
    )


def test_complete_task_does_not_bypass_cas():
    src = Path(__file__).resolve().parent.parent.joinpath("task_worker/worker.py").read_text(
        encoding="utf-8"
    )
    assert 'expected_status="processing"' in src
    assert "CAS failed completing task" in src


def test_save_chunk_gate_404_is_failure():
    _, TaskWorker = _worker()
    w = TaskWorker()
    w._request = MagicMock(return_value=MagicMock(status_code=404))
    assert w._save_chunk_gate("ds", 1, {}, True, ["one"]) is False


def test_process_chunk_task_saves_and_enqueues_gate():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = {
        "_id": "chunk-1",
        "task_type": "chunk",
        "dataset_id": "ds-abc",
        "queue_name": "q-test",
        "question_types": ["one", "open"],
        "pipeline_mode": "full",
        "source_document": "doc.docx",
        "dataset_name": "DS",
        "document_kind": "textbook",
    }
    stop = threading.Event()
    lost = threading.Event()
    chunker_kwargs = {}

    def fake_request(method, url, **kwargs):
        resp = MagicMock(status_code=200)
        if method == "GET" and url.endswith("/document"):
            resp.content = b"docx-bytes"
            resp.headers = {
                "Content-Disposition": 'attachment; filename="doc.docx"',
                "Content-Type": "application/octet-stream",
            }
            return resp
        if method == "POST" and "/chunk/" in url:
            chunker_kwargs.update(kwargs)
            resp.json = MagicMock(return_value={
                "chunks": ["x" * 50, "y" * 50],
                "chunks_detailed": [],
                "document_type": {"document_name": "Doc"},
            })
            return resp
        if method == "POST" and url.endswith("/chunks"):
            return resp
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp.json = MagicMock(return_value={"tasks_added": 2})
            return resp
        return MagicMock(status_code=404)

    with (
        patch.object(w, "_start_heartbeat", return_value=(stop, lost)),
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "_complete_task", MagicMock()) as complete,
        patch.object(w, "_patch_dataset", MagicMock()),
    ):
        summary, status = w.process_chunk_task(task)

    assert status == "completed"
    assert summary["chunks"] == 2
    assert summary["gate_enabled"] is True
    complete.assert_called()
    assert complete.call_args.args[1] == "completed"
    # Документ в чанкер уходит один раз: повтор выгружал его заново, пока
    # чанкер ещё обрабатывал первую копию.
    assert chunker_kwargs.get("retries") == 1
    assert chunker_kwargs.get("timeout") == mod.WORKER_CHUNK_TIMEOUT
    assert chunker_kwargs.get("params") == {"document_kind": "textbook", "chunking_method": "rules"}


def test_process_gate_task_propagates_block_type_to_gate_and_generation():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = _gate_task(document_kind="textbook", block_type="inset")
    gate_result = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    captured = {}

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/chunk_gate/"):
            captured.update(kwargs.get("json", {}))
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"result": gate_result})
            return resp
        if method == "PUT" and "/chunks/3/gate" in url:
            return MagicMock(status_code=200)
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp = MagicMock(status_code=200)
            resp.json = MagicMock(return_value={"tasks_added": 3})
            return resp
        if method == "POST" and url.endswith("/gate-progress"):
            return MagicMock(status_code=200)
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request) as req,
        patch.object(w, "_complete_task", MagicMock()),
    ):
        _summary, status = w.process_gate_task(task)

    assert status == "gate_done"
    assert captured["document_kind"] == "textbook"
    assert captured["block_type"] == "inset"
    enqueue_calls = [
        c for c in req.call_args_list
        if c.args[0] == "POST" and "/queues/q-test/tasks/" in c.args[1]
    ]
    gen_tasks = enqueue_calls[0].kwargs["json"]
    assert all(gt["block_type"] == "inset" for gt in gen_tasks)


def test_block_type_field_reads_fragment_data():
    _, TaskWorker = _worker()
    assert TaskWorker._block_type_field({"fragment_data": {"block_type": "caption"}}) == {"block_type": "caption"}
    assert TaskWorker._block_type_field({"fragment_data": {"block_type": ""}}) == {}
    assert TaskWorker._block_type_field({"fragment_data": None}) == {}
    assert TaskWorker._block_type_field({}) == {}


def test_process_chunk_task_llm_method_uses_long_timeout_and_records_engine():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    task = {
        "_id": "chunk-2",
        "task_type": "chunk",
        "dataset_id": "ds-abc",
        "queue_name": "q-test",
        "question_types": ["one"],
        "pipeline_mode": "full",
        "source_document": "book.pdf",
        "dataset_name": "DS",
        "document_kind": "textbook",
        "chunking_method": "llm",
    }
    chunker_kwargs = {}
    patches = []

    def fake_request(method, url, **kwargs):
        resp = MagicMock(status_code=200)
        if method == "GET" and url.endswith("/document"):
            resp.content = b"pdf-bytes"
            resp.headers = {"Content-Disposition": 'attachment; filename="book.pdf"', "Content-Type": "application/pdf"}
            return resp
        if method == "POST" and "/chunk/" in url:
            chunker_kwargs.update(kwargs)
            resp.json = MagicMock(return_value={
                "chunks": ["x" * 50],
                "chunks_detailed": [],
                "document_type": {"document_name": "Book"},
                "chunking_method": "llm",
                "chunking_engine": "llm_segmentation",
                "chunking_fallback": None,
                "dropped_blocks": {"exercise": {"count": 3, "chars": 300}},
                "chunking_stats": {
                    "paragraph_source": "layout",
                    "llm_segmentation": {"windows": 5, "llm_ok": 5, "fallback": 0, "suspicious_windows": []},
                },
            })
            return resp
        if method == "POST" and url.endswith("/chunks"):
            return resp
        if method == "POST" and "/queues/q-test/tasks/" in url:
            resp.json = MagicMock(return_value={"tasks_added": 1})
            return resp
        return MagicMock(status_code=404)

    with (
        _heartbeat_patches(w),
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "_complete_task", MagicMock()),
        patch.object(w, "_patch_dataset", side_effect=lambda _id, p: patches.append(p)),
    ):
        _summary, status = w.process_chunk_task(task)

    assert status == "completed"
    assert chunker_kwargs["params"]["chunking_method"] == "llm"
    assert chunker_kwargs["timeout"] == mod.WORKER_CHUNK_TIMEOUT_LLM
    final = patches[-1]
    assert final["chunking_method"] == "llm"
    assert final["chunking_engine"] == "llm_segmentation"
    assert "chunking_fallback" not in final
    assert final["chunking_dropped_blocks"] == {"exercise": {"count": 3, "chars": 300}}
    assert final["chunking_stats"]["paragraph_source"] == "layout"
    assert final["chunking_stats"]["llm_segmentation"]["windows"] == 5


def test_compact_chunking_stats_respects_size_cap():
    _, TaskWorker = _worker()
    assert TaskWorker._compact_chunking_stats(None) is None
    assert TaskWorker._compact_chunking_stats({}) is None
    small = {"paragraph_source": "text", "llm_segmentation": {"windows": 2}}
    assert TaskWorker._compact_chunking_stats(small) == small

    big = {
        "paragraph_source": "layout",
        "llm_segmentation": {"windows": 400, "suspicious_windows": list(range(5000)), "errors_sample": ["x" * 200] * 5},
        "layout": {"pages": 448, "blocks": {"body": 1193}},
    }
    compact = TaskWorker._compact_chunking_stats(big)
    assert compact is not None
    assert "suspicious_windows" not in compact["llm_segmentation"]
    assert "errors_sample" not in compact["llm_segmentation"]
    assert compact["llm_segmentation"]["windows"] == 400
    assert compact["layout"] == {"pages": 448, "blocks": {"body": 1193}}
    # Исходный словарь не меняется.
    assert len(big["llm_segmentation"]["suspicious_windows"]) == 5000

    huge = {"layout": {"profile": "y" * 20000}, "llm_segmentation": {"windows": 1}}
    compact = TaskWorker._compact_chunking_stats(huge)
    assert compact == {"llm_segmentation": {"windows": 1}}


def test_classify_error_chunking_method_unavailable():
    _, TaskWorker = _worker()
    assert TaskWorker._classify_error(
        "Chunker error: 400: chunking method 'semantic' is not configured: CHUNKER_EMBEDDING_URL is not set"
    ) == "chunker_method_unavailable"
    assert TaskWorker._classify_error(
        "Chunker error: 400: chunking method 'llm' is not supported for document_kind 'normative'"
    ) == "chunker_method_unavailable"
    assert TaskWorker._classify_error("Chunker error: 500: Internal server error") == "chunker_failed"
