"""Gate kind addenda (textbook / encyclopedia): files exist and examples parse."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_AGENT_API = _REPO / "agent_api"
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api  # noqa: E402

prefer_agent_api()  # agent_api's `config`, not dataset_api's, must be importable

from agent.nodes.chunk_gate.chunk_gate import _decide, _parse_gate_response  # noqa: E402

_GATE_DIR = _AGENT_API / "agent" / "nodes" / "chunk_gate"


def _examples(text: str):
    """(expectation line, parsed JSON) for every «ОЖИДАЕМЫЙ OUTPUT:» block."""
    out = []
    for m in re.finditer(r"ПРИМЕР ([A-Z]) \(ОЖИДАЕТСЯ ([^)]*)\):.*?ОЖИДАЕМЫЙ OUTPUT:\n(\{.*?\})\n", text, re.S):
        out.append((m.group(1), m.group(2), json.loads(m.group(3))))
    return out


def test_textbook_addendum_covers_apparatus_and_exercises():
    text = (_GATE_DIR / "GATE_KIND_TEXTBOOK.txt").read_text(encoding="utf-8")
    for needle in ("c1_category", "exercise", "front_matter", "caption_only", "Выясните", "ВОПРОСЫ И ЗАДАНИЯ", "ОГЛАВЛЕНИЕ", "СПИСОК СОКРАЩЕНИЙ"):
        assert needle in text, needle
    examples = _examples(text)
    assert len(examples) == 4
    expected = {"A": (False, "chunk_exercise_block"), "B": (True, None), "C": (False, "chunk_front_matter"), "D": (True, None)}
    for label, _note, raw in examples:
        parsed = _parse_gate_response(json.dumps(raw, ensure_ascii=False))
        assert _decide(parsed, "open") == expected[label], label


def test_encyclopedia_addendum_examples_parse():
    text = (_GATE_DIR / "GATE_KIND_ENCYCLOPEDIA.txt").read_text(encoding="utf-8")
    assert "c1_category" in text and "front_matter" in text
    examples = _examples(text)
    assert len(examples) == 2
    verdicts = {label: _decide(_parse_gate_response(json.dumps(raw, ensure_ascii=False)), "multi") for label, _n, raw in examples}
    assert verdicts["A"] == (False, "chunk_front_matter")
    assert verdicts["B"] == (True, None)
