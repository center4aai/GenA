"""
UI regression tests for changes specified in `tests/ui_fix.docx`.
Uses Streamlit AppTest and simple source-guard checks.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

import streamlit

from tests.conftest import ok_json_response, patch_view_http

_REPO = Path(__file__).resolve().parent.parent
_GENA = _REPO / "gena_web" / "gena"


def _noop_page_link(*_a, **_k):
    """AppTest does not load multipage `app.py`; st.page_link needs a stub."""
    return None


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------- Source / grep guards ----------


def test_app_renames_data_preprocessing_page():
    t = _read(_GENA / "app.py")
    assert 'title="Data Preprocessing"' in t
    assert 'url_path="data_preprocessing"' in t
    assert "Question Generator" not in t


def test_home_quick_start_xlsx():
    t = _read(_GENA / "views" / "home.py")
    assert "Browse generated questions, edit inline, compare versions, and export XLSX" in t
    assert "edit them, compare versions, and export CSV" not in t
    assert "Data Preprocessing" in t
    assert (
        "Browse generated questions, edit inline, compare dataset versions, export Generation Results and Full Pipeline Data to XLSX"
        in t
    )


def test_queue_manager_anchors():
    t = _read(_GENA / "views" / "queue_manager.py")
    for name in ("model-health", "queue-overview", "dataset-progress", "manage-queues"):
        assert f'name="{name}"' in t
    assert "**On this page**" in t
    assert "[Model Health](#model-health)" in t


def test_dataset_editor_anchors():
    t = _read(_GENA / "views" / "dataset_editor.py")
    for name in ("available-datasets", "questions", "export"):
        assert f'name="{name}"' in t
    assert "**On this page**" in t
    assert "[Available Datasets](#available-datasets)" in t


def test_bot_multiselect_and_headings():
    t = _read(_GENA / "views" / "bot.py")
    assert "default=['one', 'multi', 'open']" in t
    assert 'key="question_types_select"' in t
    assert "### Question Types Selection" in t
    assert "### Chunking" in t


def test_subtitles_use_helper():
    for rel in (
        "views/bot.py",
        "views/dataset_editor.py",
        "views/queue_manager.py",
        "views/statistics.py",
        "views/dynamic_implementation.py",
        "views/docs.py",
    ):
        t = _read(_GENA / rel)
        assert "from gena.views import page_subtitle" in t
        assert "page_subtitle(" in t


def test_page_subtitle_helper_in_views_init():
    t = _read(_GENA / "views" / "__init__.py")
    assert "def page_subtitle" in t
    assert "1.15em" in t and "#555" in t


# ---------- AppTest (Streamlit) ----------


def _collect_markdown(app: AppTest) -> str:
    seen: set[int] = set()
    parts: list[str] = []

    def walk(node) -> None:
        oid = id(node)
        if oid in seen:
            return
        seen.add(oid)
        for c in node:
            cls = type(c).__name__
            if cls == "Markdown" and getattr(c, "value", None):
                parts.append(c.value)
            if hasattr(c, "children"):
                walk(c)

    walk(app)
    return " ".join(parts)


def test_home_apptest_no_csv_in_step4():
    with patch.object(streamlit, "page_link", _noop_page_link):
        os.chdir(_REPO / "gena_web")
        at = AppTest.from_file(str(_GENA / "views" / "home.py"), default_timeout=30)
        at.run()
        joined = _collect_markdown(at)
    assert "export XLSX" in joined
    assert "export CSV" not in joined
    assert "edit inline" in joined


def test_data_preprocessing_apptest():
    """Smoke run: `st.page_link` needs stub outside multipage `app.py`; dataset API mocked."""
    fake_r = MagicMock()
    fake_r.status_code = 200
    fake_r.json = MagicMock(return_value=[])
    _m = ok_json_response([])
    with (
        patch("gena.views.bot.get", return_value=_m),
        patch("gena.views.bot.post", return_value=_m),
        patch("gena.views.bot.put", return_value=_m),
        patch("gena.views.bot.delete", return_value=_m),
        patch("gena.views.bot.requests.get", return_value=fake_r),
        patch.object(streamlit, "page_link", _noop_page_link),
    ):
        os.chdir(_REPO / "gena_web")
        at = AppTest.from_file(str(_GENA / "views" / "bot.py"), default_timeout=60)
        at.run()
        assert "Data Preprocessing" in [t.value for t in at.title if t.value]
        joined = _collect_markdown(at)
        assert "Existing Datasets" in joined
        assert "Upload a new document" in joined


def test_results_editor_subtitle_text():
    with patch_view_http("gena.views.dataset_editor"):
        os.chdir(_REPO / "gena_web")
        at = AppTest.from_file(
            str(_GENA / "views" / "dataset_editor.py"), default_timeout=40
        )
        at.run()
        joined = " ".join(m.value for m in at.markdown if m.value)
        captions = " ".join(c.value for c in at.caption if c.value)
        assert (
            "Browse generated questions, edit inline, compare versions, and export XLSX"
            in joined
        )
        assert "Save Changes as New Version" in captions


def test_docs_open_data_preprocessing_link():
    t = _read(_GENA / "views" / "docs.py")
    assert 'label="Open Data Preprocessing"' in t
    assert "Open Question Generator" not in t
