"""Политика ретраев воркера к agent_api (task_worker/worker.py).

Таймаут /process_prompt/ — клиентский (requests.Timeout) или серверный (504
"llm timeout") — НЕ повторяется: пайплайн на стороне agent_api продолжает
работать, и повтор запускал параллельный пайплайн на тот же чанк (инцидент
«503 busy» на общем LLM-хосте). 502/503/500 и ConnectionError повторяются с
экспоненциальным backoff 1, 4, 16 с (Retry-After сервера имеет приоритет),
4xx — без повторов. Реальных пауз в тестах нет: time.sleep замокан.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from task_worker_loader import load_task_worker_module


def _load_message_codes() -> frozenset:
    """MESSAGE_CODES из dataset_api/pipeline_constants.py без правки sys.path
    (иначе dataset_api/config.py затеняет config воркера)."""
    import importlib.util

    path = _REPO / "dataset_api" / "pipeline_constants.py"
    spec = importlib.util.spec_from_file_location("gena_pipeline_constants_for_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MESSAGE_CODES


MESSAGE_CODES = _load_message_codes()


def _worker():
    mod = load_task_worker_module()
    return mod, mod.TaskWorker


def _gen_task(**overrides):
    base = {
        "_id": "gen-1",
        "task_type": "generation",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 7,
        "dataset_id": None,
        "source_document": "doc.pdf",
    }
    base.update(overrides)
    return base


def _resp(status: int, detail: str = "boom", headers: dict | None = None):
    resp = MagicMock(status_code=status)
    resp.headers = headers if headers is not None else {}
    if status == 200:
        resp.json = MagicMock(
            return_value={"result": {"output": {"generated_question": {"task": "Q", "outputs": "A"}}}}
        )
        resp.text = "{}"
    else:
        resp.json = MagicMock(return_value={"detail": detail})
        resp.text = f'{{"detail":"{detail}"}}'
    return resp


def _run_process_task(mod, w, side_effects, task=None):
    """process_task с замоканным транспортом: возвращает
    (result, status, session_mock, sleep_mock, update_task_status_mock)."""
    stop, lost = threading.Event(), threading.Event()
    with (
        patch.object(mod, "WORKER_AGENT_MAX_RETRIES", 2),
        patch.object(w, "_start_heartbeat", return_value=(stop, lost)),
        patch.object(w.session, "request", MagicMock(side_effect=side_effects)) as req,
        patch.object(mod.time, "sleep", MagicMock()) as sleep,
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
    ):
        result, status = w.process_task(task or _gen_task())
    return result, status, req, sleep, upd


def _failed_code(upd):
    assert upd.call_count == 1
    assert upd.call_args.args[1] == "failed"
    return upd.call_args.kwargs.get("message_code")


# ---------- _backoff_seconds ----------


def test_backoff_seconds_exponential_and_capped():
    mod, TaskWorker = _worker()
    assert TaskWorker._backoff_seconds(0) == 1
    assert TaskWorker._backoff_seconds(1) == 4
    assert TaskWorker._backoff_seconds(2) == 16
    assert TaskWorker._backoff_seconds(3) == mod.WORKER_RETRY_BACKOFF_MAX
    assert TaskWorker._backoff_seconds(10) == mod.WORKER_RETRY_BACKOFF_MAX
    assert TaskWorker._backoff_seconds(-1) == 1


def test_backoff_seconds_honours_numeric_retry_after():
    mod, TaskWorker = _worker()
    assert TaskWorker._backoff_seconds(0, retry_after="7") == 7
    assert TaskWorker._backoff_seconds(2, retry_after="2") == 2
    assert TaskWorker._backoff_seconds(0, retry_after=3) == 3
    # Retry-After больше потолка — режем потолком.
    assert TaskWorker._backoff_seconds(0, retry_after="600") == mod.WORKER_RETRY_BACKOFF_MAX


def test_backoff_seconds_ignores_unparseable_retry_after():
    _, TaskWorker = _worker()
    # HTTP-дату не разбираем — экспонента.
    assert TaskWorker._backoff_seconds(1, retry_after="Wed, 21 Oct 2026 07:28:00 GMT") == 4
    assert TaskWorker._backoff_seconds(1, retry_after=None) == 4
    assert TaskWorker._backoff_seconds(1, retry_after="-5") == 4
    assert TaskWorker._backoff_seconds(1, retry_after=MagicMock()) == 4


# ---------- process_task: /process_prompt/ ----------


def test_process_task_timeout_fails_fast_without_retry():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    result, status, req, sleep, upd = _run_process_task(
        mod, w, [requests.exceptions.Timeout("Read timed out")]
    )
    assert result is None
    assert status == "failed"
    assert req.call_count == 1
    sleep.assert_not_called()
    assert _failed_code(upd) == "model_unavailable"
    assert "not retrying" in upd.call_args.kwargs["error"]


def test_process_task_504_fails_fast_without_retry():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    result, status, req, sleep, upd = _run_process_task(
        mod, w, [_resp(504, "llm timeout"), _resp(200)]
    )
    assert result is None
    assert status == "failed"
    assert req.call_count == 1
    sleep.assert_not_called()
    assert _failed_code(upd) == "model_unavailable"
    assert upd.call_args.kwargs["error"] == "Agent API error: 504: llm timeout"


def test_process_task_503_twice_then_200_succeeds_with_backoff():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    result, status, req, sleep, upd = _run_process_task(
        mod, w, [_resp(503, "llm busy"), _resp(503, "llm busy"), _resp(200)]
    )
    assert status == "completed"
    assert result is not None
    assert req.call_count == 3
    assert sleep.call_args_list == [call(1), call(4)]
    upd.assert_not_called()  # completed ставит process_batch, не process_task


def test_process_task_503_exhausted_fails_model_unavailable():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    result, status, req, sleep, upd = _run_process_task(
        mod, w, [_resp(503, "llm busy")] * 3
    )
    assert status == "failed"
    assert req.call_count == 3  # 1 + WORKER_AGENT_MAX_RETRIES
    assert sleep.call_args_list == [call(1), call(4)]
    assert _failed_code(upd) == "model_unavailable"


def test_process_task_502_retries_with_backoff():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, _ = _run_process_task(
        mod, w, [_resp(502, "llm unreachable"), _resp(200)]
    )
    assert status == "completed"
    assert req.call_count == 2
    assert sleep.call_args_list == [call(1)]


def test_process_task_503_respects_retry_after_header():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, _ = _run_process_task(
        mod, w, [_resp(503, "llm busy", headers={"Retry-After": "7"}), _resp(200)]
    )
    assert status == "completed"
    assert req.call_count == 2
    assert sleep.call_args_list == [call(7)]


def test_process_task_500_retries_with_backoff_then_model_error():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, upd = _run_process_task(mod, w, [_resp(500, "boom")] * 3)
    assert status == "failed"
    assert req.call_count == 3
    assert sleep.call_args_list == [call(1), call(4)]
    assert _failed_code(upd) == "model_error"


def test_process_task_4xx_no_retry():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, upd = _run_process_task(
        mod, w, [_resp(400, "bad prompt"), _resp(200)]
    )
    assert status == "failed"
    assert req.call_count == 1
    sleep.assert_not_called()
    assert upd.call_args.kwargs["error"] == "Agent API error: 400: bad prompt"


def test_process_task_401_llm_auth_failed_no_retry():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, upd = _run_process_task(mod, w, [_resp(401, "llm auth failed")])
    assert status == "failed"
    assert req.call_count == 1
    sleep.assert_not_called()
    assert _failed_code(upd) == "model_auth_failed"


def test_process_task_connection_error_retries_with_backoff():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    errors = [requests.exceptions.ConnectionError("refused")] * 3
    _, status, req, sleep, upd = _run_process_task(mod, w, errors)
    assert status == "failed"
    assert req.call_count == 3
    assert sleep.call_args_list == [call(1), call(4)]
    assert _failed_code(upd) == "model_unavailable"
    assert "Agent request failed after 3 attempts" in upd.call_args.kwargs["error"]


def test_process_task_connection_error_then_200():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    _, status, req, sleep, _ = _run_process_task(
        mod, w, [requests.exceptions.ConnectionError("refused"), _resp(200)]
    )
    assert status == "completed"
    assert req.call_count == 2
    assert sleep.call_args_list == [call(1)]


# ---------- _request ----------


def test_request_5xx_uses_exponential_backoff():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    with (
        patch.object(w.session, "request", MagicMock(side_effect=[_resp(500)] * 3)) as req,
        patch.object(mod.time, "sleep", MagicMock()) as sleep,
    ):
        resp = w._request("GET", "http://x/y", retries=3)
    assert resp.status_code == 500
    assert req.call_count == 3
    assert sleep.call_args_list == [call(1), call(4)]


def test_request_5xx_honours_retry_after():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    responses = [_resp(503, headers={"Retry-After": "2"}), _resp(200)]
    with (
        patch.object(w.session, "request", MagicMock(side_effect=responses)),
        patch.object(mod.time, "sleep", MagicMock()) as sleep,
    ):
        resp = w._request("GET", "http://x/y", retries=3)
    assert resp.status_code == 200
    assert sleep.call_args_list == [call(2)]


def test_request_timeout_uses_exponential_backoff_then_raises():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    errors = [requests.exceptions.Timeout("t")] * 3
    with (
        patch.object(w.session, "request", MagicMock(side_effect=errors)) as req,
        patch.object(mod.time, "sleep", MagicMock()) as sleep,
        pytest.raises(requests.exceptions.Timeout),
    ):
        w._request("GET", "http://x/y", retries=3)
    assert req.call_count == 3
    assert sleep.call_args_list == [call(1), call(4)]


def test_request_retries_one_makes_single_attempt():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    with (
        patch.object(w.session, "request", MagicMock(side_effect=[_resp(503), _resp(200)])) as req,
        patch.object(mod.time, "sleep", MagicMock()) as sleep,
    ):
        resp = w._request("POST", "http://x/y", retries=1)
    assert resp.status_code == 503
    assert req.call_count == 1
    sleep.assert_not_called()


def test_claim_tasks_is_not_retried():
    """Claim не идемпотентен: потерянный ответ на успешный claim при повторе
    забирал бы лишние задачи в processing."""
    _, TaskWorker = _worker()
    w = TaskWorker()
    ok = MagicMock(status_code=200)
    ok.json = MagicMock(return_value=[{"_id": "t1"}])
    with patch.object(w, "_request", return_value=ok) as req:
        assert w.claim_tasks() == [{"_id": "t1"}]
    assert req.call_count == 1
    assert req.call_args.args[1].endswith("/tasks/claim")
    assert req.call_args.kwargs.get("retries") == 1


# ---------- _classify_error ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Agent API error: 504: llm timeout", "model_unavailable"),
        ("Agent API error: 502: llm unreachable", "model_unavailable"),
        ("Agent API error: 503: llm busy", "model_unavailable"),
        ("Agent API error: 401: llm auth failed", "model_auth_failed"),
        ("Gate API error: 401: gate llm auth failed", "model_auth_failed"),
        ("Gate API error: 500: gate parse error", "model_output_invalid"),
        ("Gate API error: 500: gate llm error", "model_error"),
        ("Gate API error: 504: gate llm timeout", "gate_timeout"),
        ("Gate API error: 503: gate llm busy", "gate_overloaded"),
        ("Gate API error: 502: gate llm unreachable", "gate_unavailable"),
        ("Gate API error: 500: Internal server error", "gate_unavailable"),
        ("Gate request failed: HTTPConnectionPool(host='agent_api', port=8790): Read timed out.", "gate_timeout"),
        # Числа в тексте — не HTTP-статусы.
        ("Chunk 401 is empty or too short (length: 5)", "chunk_too_short"),
        ("Chunk 429 is empty or too short (length: 5)", "chunk_too_short"),
        (
            "Agent request failed after 3 attempts (chunk length: 14012 chars): Read timed out",
            "model_unavailable",
        ),
        (
            "Agent request timed out after 1 attempt(s), not retrying (chunk length: 4401 chars): "
            "HTTPConnectionPool(host='agent_api', port=8790): Read timed out.",
            "model_unavailable",
        ),
        ("Agent API error: 500: boom", "model_error"),
        ("Agent API error: 429: Too Many Requests", "model_unavailable"),
        ("Agent API error: 401: Unauthorized", "model_auth_failed"),
        # Голый 401 без detail модели с gate-префиксом — не auth модели.
        ("Gate API error: 401: Unauthorized", "gate_unavailable"),
        ("Save chunks failed: 401: Not authenticated", "chunks_save_failed"),
        ("Force-failed after 3 stuck recoveries (>= WORKER_MAX_TASK_ATTEMPTS=3)", "task_stuck"),
    ],
)
def test_classify_error_constant_details(raw, expected):
    _, TaskWorker = _worker()
    code = TaskWorker._classify_error(raw)
    assert code == expected
    assert code in MESSAGE_CODES


def test_classify_error_only_emits_known_message_codes():
    """Все литералы message_code в _classify_error объявлены в MESSAGE_CODES."""
    import inspect
    import re as _re

    _, TaskWorker = _worker()
    src = inspect.getsource(TaskWorker._classify_error)
    emitted = set(_re.findall(r'return "([a-z_]+)"', src))
    assert emitted, "no return codes found"
    assert emitted <= MESSAGE_CODES, emitted - MESSAGE_CODES


# ---------- assessment_failed (контракт B) ----------


def _save_with(result, task=None):
    _, TaskWorker = _worker()
    w = TaskWorker()
    task = task or {
        "_id": "t1",
        "chunk_id": 7,
        "chunk_text": "hello world " * 5,
        "question_type": "one",
        "dataset_id": "ds1",
        "dataset_name": "D",
    }
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["json"] = kwargs.get("json")
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"duplicate": False, "total_questions": 1})
        return resp

    mod = load_task_worker_module()
    with (
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "update_dataset_progress", MagicMock()),
        patch.object(mod, "logger") as log,
    ):
        ok = w.save_question_to_dataset(task, result)
    return ok, captured["json"], log


def test_save_question_assessment_failed_keeps_scores_empty():
    result = {
        "assessment_failed": True,
        "assessment_errors": ["sensitivity: llm timeout", "difficulty: llm timeout"],
        "result": {
            "output": {
                "generated_question": {"task": "Q?", "outputs": "A"},
                "sensitivity_score": None,
                "difficulty_score": None,
                "validation_result": {"passed": True, "total": 5, "max_total": 5},
            }
        },
    }
    ok, payload, log = _save_with(result)
    assert ok is True
    assert payload["difficulty"] is None
    # QuestionData.provocativeness — обязательная str в dataset_api: «нет оценки» = "".
    assert payload["provocativeness"] == ""
    for key in ("provocativeness", "difficulty"):
        assert payload[key] not in ("None", "3", "1", 3, 1), (key, payload[key])
    warn_msgs = [str(c.args[0]) for c in log.warning.call_args_list]
    assert any("assessment failed" in m and "sensitivity: llm timeout" in m for m in warn_msgs), warn_msgs


def test_save_question_assessment_failed_flag_inside_output():
    result = {
        "result": {
            "output": {
                "assessment_failed": True,
                "assessment_errors": ["difficulty: llm busy"],
                "generated_question": {"task": "Q?", "outputs": "A"},
                # Даже если оценки почему-то пришли — при флаге они не сохраняются.
                "sensitivity_score": {"provocativeness_score": 3},
                "difficulty_score": {"difficulty": 1},
            }
        }
    }
    ok, payload, _ = _save_with(result)
    assert ok is True
    assert payload["provocativeness"] == ""
    assert payload["difficulty"] is None


def test_save_question_assessment_failed_flag_per_score_object():
    """Флаг на самих объектах оценок (ProvocativenessOutput/DifficultyOutput):
    падает только та оценка, у которой assessment_failed."""
    result = {
        "result": {
            "output": {
                "generated_question": {"task": "Q?", "outputs": "A"},
                "sensitivity_score": {
                    "provocativeness_score": None,
                    "assessment_failed": True,
                    "assessment_error": "parse error",
                },
                "difficulty_score": {"difficulty": 2, "assessment_failed": False},
            }
        }
    }
    ok, payload, log = _save_with(result)
    assert ok is True
    assert payload["provocativeness"] == ""
    assert payload["difficulty"] == "2"
    warn_msgs = [str(c.args[0]) for c in log.warning.call_args_list]
    assert any("sensitivity: parse error" in m for m in warn_msgs), warn_msgs


def test_save_question_scores_none_without_flag_not_stringified():
    result = {
        "result": {
            "output": {
                "generated_question": {"task": "Q?", "outputs": "A"},
                "sensitivity_score": {"provocativeness_score": None},
                "difficulty_score": {"difficulty": None},
            }
        }
    }
    ok, payload, _ = _save_with(result)
    assert ok is True
    assert payload["provocativeness"] == ""
    assert payload["difficulty"] is None


def test_save_question_scores_preserved_when_assessment_ok():
    result = {
        "assessment_failed": False,
        "result": {
            "output": {
                "generated_question": {"task": "Q?", "outputs": "A"},
                "sensitivity_score": {"provocativeness_score": 2},
                "difficulty_score": {"difficulty": "3"},
            }
        },
    }
    ok, payload, log = _save_with(result)
    assert ok is True
    assert payload["provocativeness"] == "2"
    assert payload["difficulty"] == "3"
    log.warning.assert_not_called()


def test_process_task_assessment_failed_task_stays_completed():
    mod, TaskWorker = _worker()
    w = TaskWorker()
    resp = MagicMock(status_code=200, headers={})
    resp.json = MagicMock(
        return_value={
            "assessment_failed": True,
            "assessment_errors": ["sensitivity: llm timeout"],
            "result": {
                "output": {
                    "generated_question": {"task": "Q", "outputs": "A"},
                    "sensitivity_score": None,
                    "difficulty_score": None,
                }
            },
        }
    )
    resp.text = "{}"
    saved = {}

    def fake_request(method, url, **kwargs):
        if url.endswith("/process_prompt/"):
            return resp
        if url.endswith("/add-question"):
            saved["json"] = kwargs.get("json")
            ok = MagicMock(status_code=200)
            ok.json = MagicMock(return_value={"duplicate": False, "total_questions": 1})
            return ok
        return MagicMock(status_code=404)

    stop, lost = threading.Event(), threading.Event()
    with (
        patch.object(w, "_start_heartbeat", return_value=(stop, lost)),
        patch.object(w, "_request", side_effect=fake_request),
        patch.object(w, "update_task_status", MagicMock(return_value=True)) as upd,
        patch.object(w, "update_dataset_progress", MagicMock()),
    ):
        result, status = w.process_task(_gen_task(dataset_id="ds1", dataset_name="D"))

    assert status == "completed"
    assert result is not None
    upd.assert_not_called()  # не failed
    assert saved["json"]["difficulty"] is None
    assert saved["json"]["provocativeness"] == ""
