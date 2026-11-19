"""Parity checks between Python pipeline_constants and frontend copies."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DATASET_API = _REPO / "dataset_api"
if str(_DATASET_API) not in sys.path:
    sys.path.insert(0, str(_DATASET_API))

from pipeline_constants import GATE_REJECTION_REASONS, MESSAGE_CODES, TASK_STATUSES

_FRONTEND = _REPO / "gena_frontend" / "src" / "shared"
_TASK_MESSAGE = _FRONTEND / "lib" / "taskMessage.ts"
_LOCALES = _FRONTEND / "i18n" / "locales"

# Reasons the gate reports on a chunk it let through (mode disabled, already
# validated). They never reach ``gate_rejected_<reason>``.
_PASS_THROUGH_REASONS = frozenset({"gate_disabled_by_pipeline_mode", "chunk_pre_validated"})


def _frontend_message_code_keys() -> dict:
    """The MESSAGE_CODE_KEYS object literal from taskMessage.ts as {code: i18n key}."""
    text = _TASK_MESSAGE.read_text(encoding="utf-8")
    m = re.search(r"MESSAGE_CODE_KEYS[^=]*=\s*\{(.*?)\n\};", text, flags=re.S)
    assert m, "MESSAGE_CODE_KEYS not found in taskMessage.ts"
    return dict(re.findall(r"^\s+([a-z0-9_]+):\s*'([^']+)'", m.group(1), flags=re.M))


def _locale_msg_keys(locale: str) -> set:
    data = json.loads((_LOCALES / locale / "progress.json").read_text(encoding="utf-8"))
    return {f"msg.{k}" for k in (data.get("msg") or {})}


def test_frontend_message_codes_match_python_set():
    keys = set(_frontend_message_code_keys())
    assert keys == MESSAGE_CODES, (
        f"frontend missing {MESSAGE_CODES - keys}, python missing {keys - MESSAGE_CODES}"
    )


def test_every_gate_rejection_reason_has_a_message_code():
    reasons = GATE_REJECTION_REASONS - _PASS_THROUGH_REASONS
    # The worker falls back to "unknown" when the gate gives no reason.
    expected = {f"gate_rejected_{r}" for r in reasons | {"unknown"}}
    missing = expected - MESSAGE_CODES
    assert not missing, f"MESSAGE_CODES missing {missing}"


def test_every_message_code_key_is_translated():
    keys = _frontend_message_code_keys()
    for locale in ("ru", "en"):
        translated = _locale_msg_keys(locale)
        missing = {
            code: key for code, key in keys.items()
            if not key.startswith("progress:") or key[len("progress:"):] not in translated
        }
        assert not missing, f"{locale}/progress.json missing {missing}"


def test_task_statuses_documented():
    assert "processing" in TASK_STATUSES
    assert "skipped" not in TASK_STATUSES
