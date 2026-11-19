"""Quality revalidation in task_worker: claim → /validate_question/ → report."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from task_worker_loader import load_task_worker_module  # noqa: E402


def _worker():
    mod = load_task_worker_module()
    return mod, mod.TaskWorker()


ITEM = {
    "item_id": "it-1",
    "run_id": "run-1",
    "dataset_id": "ds-1",
    "version": 2,
    "question_id": "q1",
    "validation_model_id": "qwen",
    "document_kind": "textbook",
    "question": {"question_type": "one", "task": "Т?", "options": {"option_1": "a", "option_2": "b"}, "correct_answer": "1", "chunk_id": 3},
    "source_text": "Текст чанка.",
}

VALIDATION = {
    "passed": False,
    "total": 12.5,
    "max_total": 20.5,
    "threshold": 18.0,
    "by_block": {"c1_question": [1, 0]},
    "block_weighted": {"c1_question": 3.0},
    "block_max": {"c1_question": 4.0},
    "justifications": {"c1_question": ["ok", "нет в тексте"]},
}


def _resp(status, payload=None):
    r = MagicMock(status_code=status)
    r.json = MagicMock(return_value=payload or {})
    r.text = json.dumps(payload or {})
    r.reason = "x"
    return r


def test_validation_fields_from_result_matches_generation_format():
    mod, _w = _worker()
    fields = mod.TaskWorker.validation_fields_from_result(VALIDATION)
    assert fields["validation_passed"] == "False"
    assert fields["validation_score"] == "12.5/20.5"
    assert fields["validation_threshold"] == "18.0"
    details = json.loads(fields["validation_details"])
    assert details["c1_question"] == [1, 0] and details["__block_weighted__"] == {"c1_question": 3.0}
    assert json.loads(fields["validation_justifications"]) == {"c1_question": ["ok", "нет в тексте"]}


def test_process_revalidation_item_reports_result():
    mod, w = _worker()
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw.get("json")))
        if url.endswith("/validate_question/"):
            return _resp(200, {"status": "success", "result": VALIDATION})
        if "/revalidation/items/it-1" in url:
            return _resp(200, {"status": "done"})
        return _resp(404)

    with patch.object(w, "_request", side_effect=fake_request), patch.object(w, "_revalidation_heartbeat", return_value=MagicMock()):
        assert w.process_revalidation_item(dict(ITEM)) == "done"
    validate = next(c for c in calls if c[1].endswith("/validate_question/"))
    assert validate[2] == {
        "question": ITEM["question"],
        "source_text": "Текст чанка.",
        "document_kind": "textbook",
        "validation_model_id": "qwen",
    }
    put = next(c for c in calls if c[0] == "PUT")
    assert put[1].endswith("/revalidation/items/it-1")
    assert put[2]["result"]["validation_passed"] == "False"
    assert "error" not in put[2]


def test_process_revalidation_item_technical_error_is_not_a_verdict():
    mod, w = _worker()
    sent = {}

    def fake_request(method, url, **kw):
        if url.endswith("/validate_question/"):
            return _resp(503, {"detail": "llm busy"})
        if method == "PUT":
            sent.update(kw.get("json") or {})
            return _resp(200)
        return _resp(404)

    with patch.object(w, "_request", side_effect=fake_request), patch.object(w, "_revalidation_heartbeat", return_value=MagicMock()):
        assert w.process_revalidation_item(dict(ITEM)) == "error"
    assert "result" not in sent
    assert "llm busy" in sent["error"]


def test_process_revalidation_item_connection_error_is_reported():
    mod, w = _worker()
    sent = {}

    def fake_request(method, url, **kw):
        if url.endswith("/validate_question/"):
            raise requests.exceptions.ConnectionError("down")
        sent.update(kw.get("json") or {})
        return _resp(200)

    with patch.object(w, "_request", side_effect=fake_request), patch.object(w, "_revalidation_heartbeat", return_value=MagicMock()):
        assert w.process_revalidation_item(dict(ITEM)) == "error"
    assert sent["error"].startswith("Agent request failed")


def test_process_revalidation_items_stops_when_queue_is_empty():
    mod, w = _worker()
    items = [dict(ITEM, item_id="a"), dict(ITEM, item_id="b"), None]
    with (
        patch.object(w, "claim_revalidation_item", side_effect=items),
        patch.object(w, "process_revalidation_item", return_value="done") as proc,
    ):
        assert w.process_revalidation_items(5) == 2
    assert [c.args[0]["item_id"] for c in proc.call_args_list] == ["a", "b"]


def test_claim_revalidation_item_handles_empty_and_errors():
    mod, w = _worker()
    with patch.object(w, "_request", return_value=_resp(200, {"item": None})):
        assert w.claim_revalidation_item() is None
    with patch.object(w, "_request", return_value=_resp(200, {"item": ITEM})):
        assert w.claim_revalidation_item()["item_id"] == "it-1"
    with patch.object(w, "_request", side_effect=requests.exceptions.Timeout("t")):
        assert w.claim_revalidation_item() is None


def test_process_revalidation_items_runs_items_concurrently(monkeypatch):
    import threading

    mod, w = _worker()
    monkeypatch.setattr(mod, "WORKER_REVALIDATION_CONCURRENCY", 2)
    items = [dict(ITEM, item_id=i) for i in ("a", "b", "c")] + [None]
    barrier = threading.Barrier(2, timeout=5)
    seen = []

    def proc(item):
        seen.append(item["item_id"])
        if item["item_id"] in ("a", "b"):
            barrier.wait()  # deadlocks unless a and b run at the same time
        if item["item_id"] == "a":
            raise RuntimeError("boom")  # one failure must not stop the others
        return "done"

    with (
        patch.object(w, "claim_revalidation_item", side_effect=items),
        patch.object(w, "process_revalidation_item", side_effect=proc),
    ):
        assert w.process_revalidation_items(10) == 3
    assert sorted(seen) == ["a", "b", "c"]


def test_process_revalidation_items_respects_limit_with_concurrency(monkeypatch):
    mod, w = _worker()
    monkeypatch.setattr(mod, "WORKER_REVALIDATION_CONCURRENCY", 4)
    claim = MagicMock(side_effect=[dict(ITEM, item_id=str(i)) for i in range(10)])
    with (
        patch.object(w, "claim_revalidation_item", claim),
        patch.object(w, "process_revalidation_item", return_value="done"),
    ):
        assert w.process_revalidation_items(1) == 1
    assert claim.call_count == 1


def test_generated_question_records_its_validation_model():
    mod, w = _worker()
    task = {
        "_id": "t1", "chunk_id": 7, "chunk_text": "текст " * 10, "question_type": "one",
        "dataset_id": "ds1", "dataset_name": "D", "validation_model_id": "gemma",
    }
    result = {"result": {"output": {
        "generated_question": {"task": "Q?", "outputs": "A"},
        "validation_result": {"passed": True, "total": 5, "max_total": 5},
    }}}
    resp = _resp(200, {"duplicate": False, "total_questions": 1, "question_added": True})
    with (
        patch.object(w, "_request", return_value=resp) as req,
        patch.object(w, "update_dataset_progress", MagicMock()),
    ):
        assert w.save_question_to_dataset(task, result) is True
    sent = req.call_args.kwargs["json"]
    assert sent["validation_model_id"] == "gemma"
