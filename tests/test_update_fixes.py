"""
Regression tests for `tests/update_fix.docx` (update_fix) — see plan.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from streamlit.testing.v1 import AppTest

import streamlit

from tests.conftest import ok_json_response

_REPO = Path(__file__).resolve().parent.parent
_GENA = _REPO / "gena_web" / "gena"
_TASK_WORKER = _REPO / "task_worker"

if str(_TASK_WORKER) not in sys.path:
    sys.path.insert(0, str(_TASK_WORKER))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _noop_page_link(*_a, **_k):
    return None


# ---------- Source / grep guards ----------


def test_bot_chunks_use_metadata_total_chunks():
    t = _read(_GENA / "views" / "bot.py")
    assert "_total_ch" in t
    assert "total_chunks" in t
    assert "/{_total_ch}" in t


def test_queue_manager_no_truncated_task_id():
    t = _read(_GENA / "views" / "queue_manager.py")
    assert "task['_id'][:8]" not in t
    assert '"Chunks (valid/total)":' in t
    assert '"Total Chunks":' not in t


def test_dataset_editor_source_uses_st_code():
    t = _read(_GENA / "views" / "dataset_editor.py")
    assert 'st.markdown(f"```\\n{question' not in t
    assert t.count("st.code(question['source_chunk'], language=None)") == 2


def test_worker_uses_config_timeout():
    t = _read(_TASK_WORKER / "worker.py")
    assert "timeout = 300" not in t
    assert "WORKER_AGENT_TIMEOUT" in t
    wcfg = _read(_TASK_WORKER / "config.py")
    assert "WORKER_AGENT_TIMEOUT" in wcfg
    assert 'os.getenv("WORKER_AGENT_TIMEOUT", "600")' in wcfg


# ---------- Unit: bot._any_active_generation ----------


def test_any_active_generation_true_when_pending():
    from gena.views import bot as bot_mod

    ds = [
        {
            "_id": "d1",
            "metadata": {"status": "processing", "queue_name": "Q42"},
        }
    ]
    m = ok_json_response([{"status": "pending", "task_id": "t1"}])
    with patch.object(bot_mod, "get", return_value=m):
        active, qname = bot_mod._any_active_generation(ds)
    assert active is True
    assert qname == "Q42"


def test_any_active_generation_false_when_no_processing():
    from gena.views import bot as bot_mod

    ds = [{"_id": "d1", "metadata": {"status": "completed"}}]
    with patch.object(bot_mod, "get", return_value=ok_json_response([])):
        active, qname = bot_mod._any_active_generation(ds)
    assert active is False
    assert qname == ""


# ---------- AppTest: bot ----------


def _collect_markdown(app: AppTest) -> str:
    seen: set[int] = set()
    parts: list[str] = []

    def walk(node) -> None:
        oid = id(node)
        if oid in seen:
            return
        seen.add(oid)
        for c in node:
            if type(c).__name__ == "Markdown" and getattr(c, "value", None):
                parts.append(c.value)
            if hasattr(c, "children"):
                walk(c)

    walk(app)
    return " ".join(parts)


def _bot_get_routing_for_chunks_test():
    """Return dataset list with valid/total 3/10; empty tasks for active-gen check path."""

    def _get(path, **kw):
        m = MagicMock()
        m.status_code = 200
        if path == "/datasets/" or path.rstrip("/") == "/datasets":
            m.json = MagicMock(
                return_value=[
                    {
                        "_id": "ds1",
                        "name": "N",
                        "source_document": "S",
                        "chunks_valid": 3,
                        "chunks_count": 3,
                        "metadata": {
                            "total_chunks": 10,
                            "status": "completed",
                        },
                        "questions": [],
                        "created_at": "2020-01-01T00:00:00",
                    }
                ]
            )
        else:
            m.json = MagicMock(return_value=[])
        return m

    return _get


def test_apptest_existing_datasets_chunks_column_3_of_10():
    """AppTest runs the script as a file; mock ``gena.http.get`` (not gena.views.bot)."""
    fake_r = MagicMock()
    fake_r.status_code = 200
    fake_r.json = MagicMock(return_value=[])
    with (
        patch("gena.http.get", side_effect=_bot_get_routing_for_chunks_test()),
        patch("gena.http.post", return_value=ok_json_response([])),
        patch("gena.http.put", return_value=ok_json_response([])),
        patch("gena.http.delete", return_value=ok_json_response([])),
        patch("gena.views.bot.requests.get", return_value=fake_r),
        patch.object(streamlit, "page_link", _noop_page_link),
    ):
        os.chdir(_REPO / "gena_web")
        at = AppTest.from_file(str(_GENA / "views" / "bot.py"), default_timeout=90)
        at.run()
    dfs = at.dataframe
    assert len(dfs) >= 1, "expected st.dataframe for Existing Datasets table"
    df0 = dfs[0].value
    col = "Chunks (valid/total)" if "Chunks (valid/total)" in df0.columns else None
    assert col, list(df0.columns)
    val = str(df0[col].iloc[0])
    assert "3" in val and "10" in val, val
    assert val.replace(" ", "") in ("3/10",) or re.match(r"3.*/\s*10", val), val


def test_bot_shows_concurrent_warning_in_source():
    t = _read(_GENA / "views" / "bot.py")
    assert "Another generation is in progress" in t
    assert "disabled=not question_types or _active_gen" in t


def test_bot_leave_page_warning_after_generate_in_source():
    """Warning text is still rendered, but now via a placeholder so it can be
    cleared once the Gate finishes."""
    t = _read(_GENA / "views" / "bot.py")
    assert (
        '_leave_warn.warning("Please note: Your progress will be lost if you leave this page.")'
        in t
    )


# ---------- AppTest: queue_manager ----------


def _qm_get_mock_full_task(queues, datasets, task_id, queue_name: str = "Q1"):

    def _get(path, **kw):
        m = MagicMock()
        p = path if isinstance(path, str) else str(path)
        m.status_code = 200
        if "/queues/" in p and "/tasks" not in p and p.rstrip("/").endswith("queues"):
            m.json = MagicMock(return_value=queues)
        elif p.endswith("/datasets/") or ("/datasets" in p and p.rstrip("/").endswith("datasets")):
            m.json = MagicMock(return_value=datasets)
        elif f"/queues/{queue_name}/tasks" in p:
            m.json = MagicMock(
                return_value=[
                    {
                        "_id": task_id,
                        "chunk_id": 1,
                        "question_type": "one",
                        "status": "failed",
                        "error": "e" * 120,
                        "created_at": "2020-01-01T00:00:00",
                        "updated_at": "2020-01-01T00:00:00",
                    }
                ]
            )
        elif re.search(r"/datasets/[^/]+/tasks", p):
            m.json = MagicMock(return_value=[])
        elif re.match(r"/datasets/[^/]+$", p) or re.match(r".*/datasets/[^/]+$", p):
            if "/tasks" in p or "/chunks" in p:
                m.json = MagicMock(return_value=[])
            else:
                did = p.rstrip("/").split("/datasets/")[-1]
                m.json = MagicMock(
                    return_value={
                        "_id": did,
                        "name": "D",
                        "metadata": {
                            "status": "completed",
                            "total_questions_generated": 0,
                            "total_chunks": 12,
                            "question_types": ["one"],
                        },
                        "questions": [],
                    }
                )
        else:
            m.json = MagicMock(return_value=[])
        return m

    return _get


def test_apptest_queue_manager_full_task_id_in_table_and_expander():
    full_id = "507f1f77bcf86cd79943901aa"
    queues = [
        {
            "name": "Q1",
            "task_count": 1,
            "pending_count": 0,
            "processing_count": 0,
            "completed_count": 0,
            "failed_count": 1,
        }
    ]
    datasets = [
        {
            "_id": "d1",
            "name": "D1",
            "created_at": "2020-01-01T00:00:00",
            "chunks_valid": 5,
            "chunks_count": 5,
            "metadata": {
                "total_chunks": 12,
                "status": "completed",
                "last_updated": "2020-01-01T00:00:00",
            },
        }
    ]
    with (
        patch("gena.http.get", side_effect=_qm_get_mock_full_task(queues, datasets, full_id)),
        patch("gena.http.post", return_value=ok_json_response({})),
        patch("gena.http.put", return_value=ok_json_response({})),
        patch("gena.http.delete", return_value=ok_json_response({})),
        patch(
            "gena.views.queue_manager.requests.get",
            return_value=MagicMock(status_code=200, json=MagicMock(return_value=[])),
        ),
    ):
        os.chdir(_REPO / "gena_web")
        at = AppTest.from_file(
            str(_GENA / "views" / "queue_manager.py"), default_timeout=120
        )
        at.run()
    dfp = None
    for d in at.dataframe:
        v = d.value
        if hasattr(v, "columns") and "Chunks (valid/total)" in list(v.columns):
            dfp = v
            break
    assert dfp is not None, "expected Dataset Progress table"
    r0 = dfp[dfp["Dataset Name"] == "D1"].iloc[0]
    assert "5" in str(r0["Chunks (valid/total)"]) and "12" in str(r0["Chunks (valid/total)"])

    task_df = None
    for d in at.dataframe:
        v = d.value
        if hasattr(v, "columns") and "Task ID" in list(v.columns):
            task_df = v
            break
    assert task_df is not None
    assert task_df["Task ID"].iloc[0] == full_id
    ex_titles = [e.label for e in at.expander]
    assert any(full_id in t for t in ex_titles)


# ---------- task_worker unit tests ----------
# Config defaults (600 / 2) are asserted in `test_worker_uses_config_timeout`.


def test_worker_retries_on_timeout_then_success():
    from worker import TaskWorker

    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout()
        m = MagicMock()
        m.status_code = 200
        m.json = MagicMock(
            return_value={"result": {"output": {"generated_question": {"task": "Q", "outputs": "A"}}}}
        )
        m.text = "{}"
        return m

    task = {
        "_id": "507f1f77bcf86cd79943901bb",
        "question_type": "one",
        "chunk_text": "x" * 50,
        "chunk_id": 1,
        "dataset_id": None,
    }
    w = TaskWorker()
    with (
        patch.object(w, "update_task_status", MagicMock()),
        patch.object(w, "save_question_to_dataset", MagicMock()),
        patch("worker.requests.post", side_effect=fake_post),
    ):
        r = w.process_task(task)
    assert r is not None
    assert calls["n"] == 2
