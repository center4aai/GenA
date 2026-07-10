"""
Regression tests for `tests/update_fix_2.docx` (update_fix_2).

Covers the nine items listed in the document: the question_types_select
StreamlitAPIException, the leave-page warning lifecycle, page tables of
contents, Back-to-top links, the Statistics Overall Status field that used
to be truncated, the restored "edit inline" subtitle, the enriched
"Another generation is in progress" message, the cleaned-up post-Gate
messages, and the HTTPS Dockerfile flags that re-enable Copy to clipboard.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_GENA = _REPO / "gena_web" / "gena"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------- 1. question_types_select bug ----------


def test_bot_no_session_state_assignment_after_widget():
    """The buggy `st.session_state["question_types_select"] = ...` after the
    widget instantiation must be gone (it caused StreamlitAPIException).
    The Generate button is already disabled when the list is empty, so we
    only show an info hint instead."""
    t = _read(_GENA / "views" / "bot.py")
    assert 'st.session_state["question_types_select"] =' not in t
    assert "Select at least one question type to enable generation." in t


# ---------- 2. Leave-page warning placeholder ----------


def test_bot_leave_page_warning_uses_placeholder():
    t = _read(_GENA / "views" / "bot.py")
    assert "_leave_warn = st.empty()" in t
    assert (
        '_leave_warn.warning("Please note: Your progress will be lost if you leave this page.")'
        in t
    )
    # cleared after Gate completes (Queue mode) and after Direct generation completes
    assert t.count("_leave_warn.empty()") >= 2


# ---------- 3. Page tables of contents in main content ----------


def test_queue_manager_main_toc_present():
    t = _read(_GENA / "views" / "queue_manager.py")
    # Sidebar TOC remains; an additional list lives in the main content
    # right after `page_subtitle("Monitor task queues, ...")`.
    main_section = t.split('page_subtitle("Monitor task queues')[1]
    for link in (
        "[Model Health](#model-health)",
        "[Queue Overview](#queue-overview)",
        "[Dataset Progress](#dataset-progress)",
        "[Manage Queues](#manage-queues)",
    ):
        assert link in main_section


def test_dataset_editor_main_toc_present():
    t = _read(_GENA / "views" / "dataset_editor.py")
    main_section = t.split("page_subtitle(")[1]
    for link in (
        "[Available Datasets](#available-datasets)",
        "[Questions](#questions)",
        "[Export](#export)",
    ):
        assert link in main_section


def test_statistics_anchors_and_toc():
    t = _read(_GENA / "views" / "statistics.py")
    for name in (
        "dataset-status-and-progress",
        "validation-threshold-analysis",
        "distributions",
        "detailed-dataset-statistics",
        "dataset-creation-timeline",
        "export-statistics",
    ):
        assert f'name="{name}"' in t
        assert f"(#{name})" in t


def test_docs_anchors_and_toc():
    t = _read(_GENA / "views" / "docs.py")
    for name in (
        "about-gen-a",
        "sensitivity-levels",
        "question-types",
        "how-to-use-gen-a",
        "chunk-gate",
        "chunks-storage",
        "validation",
    ):
        assert f'name="{name}"' in t
        assert f"(#{name})" in t


# ---------- 4. Back to top in Queue Manager and Statistics ----------


def test_back_to_top_in_qm_and_stats():
    for rel in ("views/queue_manager.py", "views/statistics.py"):
        t = _read(_GENA / rel)
        assert "<a name='top'></a>" in t, rel
        assert "[⬆️ Back to top](#top)" in t, rel


# ---------- 5. HTTPS in Dockerfile ----------


def test_dockerfile_https_flags():
    t = _read(_REPO / "gena_web" / "Dockerfile")
    assert "openssl" in t
    assert "--server.sslCertFile=" in t
    assert "--server.sslKeyFile=" in t
    # Certs must live OUTSIDE /app — docker-compose bind-mounts ./gena_web:/app
    # at runtime, which would mask anything generated at build time under /app.
    assert "/certs/cert.pem" in t
    assert "/certs/key.pem" in t
    assert "/app/.streamlit/cert.pem" not in t
    assert "/app/.streamlit/key.pem" not in t


# ---------- 6. Statistics Overall Status field is no longer truncated ----------


def test_statistics_status_metric_no_truncation():
    t = _read(_GENA / "views" / "statistics.py")
    # The truncating st.metric("Status", ...) call is replaced by markdown.
    assert 'st.metric("Status",' not in t
    assert "{completed} completed, {processing} processing" in t


# ---------- 7. "edit inline" restored ----------


def test_results_editor_subtitle_edit_inline():
    t = _read(_GENA / "views" / "dataset_editor.py")
    assert (
        'page_subtitle("Browse generated questions, edit inline, compare versions, and export XLSX.")'
        in t
    )
    assert "Save Changes as New Version" in t


def test_home_quick_start_edit_inline():
    t = _read(_GENA / "views" / "home.py")
    assert (
        "Browse generated questions, edit inline, compare versions, and export XLSX."
        in t
    )
    assert (
        "Browse generated questions, edit inline, compare dataset versions, "
        "export Generation Results and Full Pipeline Data to XLSX."
        in t
    )


# ---------- 8. Active-generation message includes queue and link ----------


def test_bot_active_gen_message_includes_queue_and_link():
    t = _read(_GENA / "views" / "bot.py")
    assert "Processing queue:" in t
    assert "Switch to Queue Manager to monitor and manage queues." in t
    # Queue Manager link rendered via st.page_link to make it work in multipage mode.
    assert 'st.page_link(\n            "views/queue_manager.py"' in t


# ---------- 9. Post-Gate messages cleaned up ----------


def test_bot_gate_message_format_and_no_duplicates():
    t = _read(_GENA / "views" / "bot.py")
    # Old duplicates removed.
    assert 'st.info(f"Queue: {queue_name} | Dataset:' not in t
    assert '"Go to **Queue Manager** to monitor progress."' not in t
    assert 'f"Gate complete: {len(valid_chunks)} chunks passed, {rejected} rejected."' not in t
    # New format and consolidated success message.
    assert 'chunks passed gate, "' in t
    assert "Added {tr['tasks_added']} tasks to queue" in t
    assert "Go to Queue Manager to monitor progress." in t


# ---------- 10. Average Progress = 100% for completed datasets ----------


def test_statistics_progress_uses_chunks_passed_gate_and_clamps_completed():
    """A finished dataset whose gate rejected some chunks must still report
    100% progress: ``total_chunks`` (denominator) includes gate-rejected
    chunks that never produce questions, so the old formula left completed
    datasets stuck below 100%."""
    t = _read(_GENA / "views" / "statistics.py")
    # New denominator is ``chunks_passed_gate`` (with fallback to total_chunks).
    assert 'metadata.get("chunks_passed_gate")' in t
    # Status == "completed" forces 100%.
    assert 'if ds_status == "completed":' in t
    assert "progress_percent = 100.0" in t
    # The naive formula must be gone.
    assert "expected_questions = total_chunks * questions_per_chunk" not in t


def test_queue_manager_progress_uses_chunks_passed_gate_and_clamps_completed():
    t = _read(_GENA / "views" / "queue_manager.py")
    assert 'metadata.get("chunks_passed_gate")' in t
    # In the "no live tasks but has questions" branch, completed clamps to 100%.
    assert 'if status == "completed":' in t
    # Old naive denominator must be gone.
    assert "expected = total_chunks_m * qpc" not in t


