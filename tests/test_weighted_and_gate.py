"""
Smoke tests for:
  1. Weighted validation scoring (validator.py)
  2. Chunk gate parsing + decision logic (chunk_gate.py)

No LLM calls — pure unit logic.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()

import json

# ─── 1. Weighted scoring ───────────────────────────────────────────────

from agent.nodes.llm_validator.validator import (
    WEIGHTS, MULTIPLIER_INDICES, MAX_POINTS, THRESHOLDS, BlockResult,
    _extract_scores_and_justifications,
)


def compute_weighted(qtype: str, blocks: list[BlockResult]) -> tuple[float, int]:
    from agent.nodes.llm_validator.validator import EXPECTED_COUNTS

    parse_complete = all(
        len(b.scores) >= EXPECTED_COUNTS[qtype][b.key] for b in blocks
    )
    weighted_sum = 0.0
    multiplier_product = 1
    for b in blocks:
        expected = EXPECTED_COUNTS[qtype][b.key]
        w = WEIGHTS[qtype].get(b.key, [1.0] * expected)
        if len(b.scores) >= expected:
            weighted_sum += sum(s * wt for s, wt in zip(b.scores, w))
        for idx in MULTIPLIER_INDICES.get(qtype, {}).get(b.key, []):
            if idx >= len(b.scores):
                parse_complete = False
                multiplier_product = 0
                break
            multiplier_product *= b.scores[idx]
        if not parse_complete and multiplier_product == 0:
            break
    total = weighted_sum * multiplier_product if parse_complete else 0.0
    return total, multiplier_product


def test_extract_scores_no_zero_fill():
    """Short LLM output must not be padded with zeros."""
    scores, justs = _extract_scores_and_justifications("Краткость 1 — ok\n", expected_count=5)
    assert scores == [1]
    assert len(justs) == 1


def test_extract_scores_no_greedy_fallback():
    """Standalone digits in prose must not be harvested."""
    text = "Вариант 1 подходит, но критерий 0 не выполнен.\nКраткость 1 — ok"
    scores, _ = _extract_scores_and_justifications(text, expected_count=2)
    assert scores == [1]


def test_extract_scores_hyphen_separator():
    """LLM may use ASCII hyphen instead of em dash."""
    scores, justs = _extract_scores_and_justifications(
        "Краткость 1 - ok\nОснованность 0 - missing\n", expected_count=2
    )
    assert scores == [1, 0]
    assert justs == ["ok", "missing"]


def test_extract_scores_slash_format():
    """Few-shot examples use 0/1 and 1/1 score notation."""
    scores, _ = _extract_scores_and_justifications(
        "Краткость 1/1\nОснованность 0/1 — bad\n", expected_count=2
    )
    assert scores == [1, 0]


def test_extract_scores_braced_real_llm_output():
    """Live models echo the prompt format: ``Критерий {1/1} — обоснование``."""
    text = (
        "Краткость {1/1} — ок\n"
        "Основанность {1/1} — ок\n"
        "Понятность {0/1} — плохо\n"
        "Не бинарность {1/1} — ок\n"
        "Контекст {1/1} — ок\n"
    )
    scores, justs = _extract_scores_and_justifications(text, expected_count=5)
    assert scores == [1, 1, 0, 1, 1]
    assert justs[2] == "плохо"


def test_extract_scores_skips_format_spec_placeholder():
    """The format-spec line with ``{обоснование}`` must not be scored."""
    text = (
        "{question_bravity} {0/1} — {обоснование}\n"
        "{question_basis} {0/1} — {обоснование}\n"
        "Краткость {1/1} — ок\n"
        "Основанность {0/1} — missing basis\n"
    )
    scores, justs = _extract_scores_and_justifications(text, expected_count=2)
    assert scores == [1, 0]
    assert justs == ["ок", "missing basis"]


def test_extract_scores_braced_few_shot_format():
    """Prompt-echo scores ``{1/1}`` are real; only ``{обоснование}`` is a template."""
    scores, justs = _extract_scores_and_justifications(
        "Краткость {1/1}\nОснованность {0/1} — missing basis\n",
        expected_count=2,
    )
    assert scores == [1, 0]
    assert justs == ["", "missing basis"]


def test_extract_scores_ignores_preamble_ending_in_digit():
    """A prose line ending in a digit used to eat a criterion slot.

    The real criteria then fell outside ``expected_count`` and a question with a
    zeroed critical criterion passed validation.
    """
    text = (
        "Оценка по блоку 1\n"
        "question_bravity 1\n"
        "question_basis 1\n"
        "question_clarity 1\n"
        "question_not_binary 1\n"
        "question_context 0 — нет якорей\n"
    )
    scores, justs = _extract_scores_and_justifications(text, expected_count=5)
    assert scores == [1, 1, 1, 1, 0]
    assert justs[4] == "нет якорей"


def test_extract_scores_ignores_heading_that_shifts_the_vector():
    """A «Критерий 1» heading used to shift every score by one position."""
    text = (
        "Критерий 1\n"
        "question_bravity 0 — слишком длинный\n"
        "question_basis 1\n"
        "question_clarity 1\n"
        "question_not_binary 1\n"
        "question_context 1\n"
    )
    scores, _ = _extract_scores_and_justifications(text, expected_count=5)
    assert scores == [0, 1, 1, 1, 1]


def test_extract_scores_ignores_trailing_summary_line():
    text = (
        "question_bravity 1\n"
        "question_basis 1\n"
        "question_clarity 1\n"
        "question_not_binary 1\n"
        "question_context 0\n"
        "Итого 1\n"
    )
    scores, _ = _extract_scores_and_justifications(text, expected_count=5)
    assert scores == [1, 1, 1, 1, 0]


def test_extract_scores_named_lines_with_markdown_bullets():
    text = (
        "- question_bravity 1 — ок\n"
        "- question_basis 1\n"
        "- question_clarity 0 — плохо\n"
        "- question_not_binary 1\n"
        "- question_context 1\n"
    )
    scores, justs = _extract_scores_and_justifications(text, expected_count=5)
    assert scores == [1, 1, 0, 1, 1]
    assert justs[2] == "плохо"


def test_extract_scores_falls_back_for_unnamed_output():
    """Models that answer with bare scores must keep working."""
    scores, _ = _extract_scores_and_justifications("1\n1\n0\n", expected_count=3)
    assert scores == [1, 1, 0]


def test_incomplete_parse_zeros_total():
    """Partial LLM output must not receive partial weighted credit."""
    blocks = [
        BlockResult("c1_question", [1], [""], ""),
        BlockResult("c2_outputs", [], [], ""),
        BlockResult("c4_logic", [], [], ""),
        BlockResult("c5_phrase", [], [], ""),
    ]
    total, mult = compute_weighted("open", blocks)
    assert total == 0.0
    assert mult == 0


def test_weighted_all_ones_open():
    blocks = [
        BlockResult("c1_question", [1,1,1,1,1], [""]* 5, ""),
        BlockResult("c2_outputs",  [1,1,1,1,1,1], [""]*6, ""),
        BlockResult("c4_logic",    [1,1,1,1], [""]*4, ""),
        BlockResult("c5_phrase",   [1,1], [""]*2, ""),
    ]
    total, mult = compute_weighted("open", blocks)
    assert mult == 1, f"multiplier should be 1 when all scores=1, got {mult}"
    assert total == MAX_POINTS["open"], f"open all-ones total should be {MAX_POINTS['open']}, got {total}"
    assert total >= THRESHOLDS["open"], "all-ones should pass threshold"
    print(f"  open  all-ones: total={total}, max={MAX_POINTS['open']}, passed=True  ✓")


def test_weighted_all_ones_multi():
    blocks = [
        BlockResult("c1_question", [1,1,1,1,1], [""]*5, ""),
        BlockResult("c2_options",  [1,1,1,1,1,1,1,1,1], [""]*9, ""),
        BlockResult("c3_outputs",  [1,1], [""]*2, ""),
        BlockResult("c4_logic",    [1,1,1,1], [""]*4, ""),
        BlockResult("c5_phrase",   [1,1], [""]*2, ""),
    ]
    total, mult = compute_weighted("multi", blocks)
    assert mult == 1
    assert total == MAX_POINTS["multi"], f"multi all-ones total should be {MAX_POINTS['multi']}, got {total}"
    print(f"  multi all-ones: total={total}, max={MAX_POINTS['multi']}, passed=True  ✓")


def test_weighted_multiplier_zero_open():
    """If question_basis (c1[1]) is 0, multiplier kills the score."""
    blocks = [
        BlockResult("c1_question", [1,0,1,1,1], [""]*5, ""),   # c1[1]=0 → multiplier=0
        BlockResult("c2_outputs",  [1,1,1,1,1,1], [""]*6, ""),
        BlockResult("c4_logic",    [1,1,1,1], [""]*4, ""),
        BlockResult("c5_phrase",   [1,1], [""]*2, ""),
    ]
    total, mult = compute_weighted("open", blocks)
    assert mult == 0, f"multiplier should be 0, got {mult}"
    assert total == 0.0, f"total should be 0 when critical criterion fails, got {total}"
    print(f"  open  c1_basis=0: total={total}, multiplier={mult}  ✓")


def test_weighted_half_weight():
    """c1_bravity weight is 0.5 for open."""
    blocks = [
        BlockResult("c1_question", [1,1,1,1,1], [""]*5, ""),
        BlockResult("c2_outputs",  [0,0,0,0,0,0], [""]*6, ""),  # all zeros
        BlockResult("c4_logic",    [0,0,0,0], [""]*4, ""),
        BlockResult("c5_phrase",   [0,0], [""]*2, ""),
    ]
    total, mult = compute_weighted("open", blocks)
    # c1: 1*0.5 + 1*1 + 1*1 + 1*1 + 1*1 = 4.5; c2/c4/c5: 0
    # multipliers: c1[1]=1,c1[4]=1,c2[1]=0,c2[2]=0 → mult=0
    assert total == 0.0, f"multiplier from c2 zeros should kill score, got {total}"
    print(f"  open  only-c1-ones: total={total}, multiplier={mult}  ✓")


def test_weighted_multi_one_critical_zero():
    """multi: c3_outputs[0]=0 (outputs_include) kills the score."""
    blocks = [
        BlockResult("c1_question", [1,1,1,1,1], [""]*5, ""),
        BlockResult("c2_options",  [1,1,1,1,1,1,1,1,1], [""]*9, ""),
        BlockResult("c3_outputs",  [0,1], [""]*2, ""),  # c3[0]=0
        BlockResult("c4_logic",    [1,1,1,1], [""]*4, ""),
        BlockResult("c5_phrase",   [1,1], [""]*2, ""),
    ]
    total, mult = compute_weighted("multi", blocks)
    assert mult == 0, f"c3[0]=0 should zero multiplier, got {mult}"
    assert total == 0.0
    print(f"  multi c3_include=0: total={total}, multiplier={mult}  ✓")


# ─── 2. Chunk gate parsing + decision ─────────────────────────────────

from agent.nodes.chunk_gate.chunk_gate import _parse_gate_response, _decide


def test_parse_valid_json():
    raw = json.dumps({
        "c1_chunk_informative": [1],
        "c1_reasoning": "Содержит полезную информацию",
        "c1_confidence": 0.95,
        "c2_chunk_reference_clarity": [1],
        "c2_reasoning": "Самодостаточен",
        "c2_confidence": 0.9,
        "c3_chunk_multi_suitability": [0],
        "c3_reasoning": "Только один факт",
        "c3_confidence": 0.85,
    })
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]
    assert parsed["c2_chunk_reference_clarity"] == [1]
    assert parsed["c3_chunk_multi_suitability"] == [0]
    assert parsed["c1_confidence"] == 0.95
    print(f"  parse valid JSON: all fields correct  ✓")


def test_parse_single_quoted_json():
    raw = """{
'c1_chunk_informative': [1],
'c1_reasoning': 'Good chunk',
'c1_confidence': 0.88,
'c2_chunk_reference_clarity': [0],
'c2_reasoning': 'Needs external context',
'c2_confidence': 1.0,
'c3_chunk_multi_suitability': [1],
'c3_reasoning': 'Multiple facts',
'c3_confidence': 0.7
}"""
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]
    assert parsed["c2_chunk_reference_clarity"] == [0]
    assert parsed["c3_chunk_multi_suitability"] == [1]
    print(f"  parse single-quoted JSON: correct  ✓")


def test_parse_markdown_fenced():
    raw = """```json
{"c1_chunk_informative": [1], "c1_reasoning": "ok", "c1_confidence": 0.9,
 "c2_chunk_reference_clarity": [1], "c2_reasoning": "ok", "c2_confidence": 0.8,
 "c3_chunk_multi_suitability": [1], "c3_reasoning": "ok", "c3_confidence": 0.7}
```"""
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]
    print(f"  parse markdown-fenced JSON: correct  ✓")


def test_parse_bare_int():
    raw = json.dumps({
        "c1_chunk_informative": 1,
        "c1_reasoning": "ok",
        "c1_confidence": 0.9,
        "c2_chunk_reference_clarity": 0,
        "c2_reasoning": "bad",
        "c2_confidence": 0.5,
        "c3_chunk_multi_suitability": 1,
        "c3_reasoning": "ok",
        "c3_confidence": 0.7,
    })
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1], f"bare int 1 should become [1], got {parsed['c1_chunk_informative']}"
    assert parsed["c2_chunk_reference_clarity"] == [0]
    print(f"  parse bare-int values: normalized to lists  ✓")


def test_parse_bare_float():
    raw = json.dumps({
        "c1_chunk_informative": 1.0,
        "c1_reasoning": "ok",
        "c1_confidence": 0.9,
        "c2_chunk_reference_clarity": 0.0,
        "c2_reasoning": "bad",
        "c2_confidence": 0.5,
        "c3_chunk_multi_suitability": 1.0,
        "c3_reasoning": "ok",
        "c3_confidence": 0.7,
    })
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1], f"bare float 1.0 should become [1], got {parsed['c1_chunk_informative']}"
    assert parsed["c2_chunk_reference_clarity"] == [0]
    print(f"  parse bare-float values: normalized to lists  ✓")


def test_decide_pass():
    parsed = {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [1]}
    ok, reason = _decide(parsed, "multi")
    assert ok is True and reason is None
    print(f"  decide all-pass multi: passed=True  ✓")


def test_decide_c1_fail():
    parsed = {"c1_chunk_informative": [0], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [1]}
    ok, reason = _decide(parsed, "open")
    assert ok is False and reason == "chunk_not_informative"
    print(f"  decide c1=0: rejected=chunk_not_informative  ✓")


def test_decide_c2_fail():
    parsed = {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [0], "c3_chunk_multi_suitability": [1]}
    ok, reason = _decide(parsed, "one")
    assert ok is False and reason == "chunk_not_self_contained"
    print(f"  decide c2=0: rejected=chunk_not_self_contained  ✓")


def test_decide_c3_fail_multi():
    parsed = {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [0]}
    ok, reason = _decide(parsed, "multi")
    assert ok is False and reason == "chunk_not_suitable_for_multi"
    print(f"  decide c3=0 multi: rejected=chunk_not_suitable_for_multi  ✓")


def test_decide_c3_fail_open_passes():
    """c3=0 should NOT block open-type questions."""
    parsed = {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [0]}
    ok, reason = _decide(parsed, "open")
    assert ok is True and reason is None
    print(f"  decide c3=0 open: passed=True (c3 ignored for open)  ✓")


def test_decide_c3_fail_one_passes():
    """c3=0 should NOT block one-type questions."""
    parsed = {"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [0]}
    ok, reason = _decide(parsed, "one")
    assert ok is True and reason is None
    print(f"  decide c3=0 one: passed=True (c3 ignored for one)  ✓")


# ─── 3. Chunk gate runnable: no verdict != rejected ───────────────────

import httpx
import pytest
from types import SimpleNamespace
from openai import BadRequestError

from agent.nodes.chunk_gate import chunk_gate as gate_mod
from agent.nodes.chunk_gate.chunk_gate import GateParseError, create_chunk_gate_chain

_FULL_GATE_JSON = json.dumps({
    "c1_chunk_informative": [1], "c1_reasoning": "ok", "c1_confidence": 0.9,
    "c2_chunk_reference_clarity": [1], "c2_reasoning": "ok", "c2_confidence": 0.8,
    "c3_chunk_multi_suitability": [0], "c3_reasoning": "one fact", "c3_confidence": 0.7,
})


def _gate_chain_with_llm(monkeypatch, llm):
    """create_chunk_gate_chain с подменённым LLM (callable → RunnableLambda)."""
    monkeypatch.setattr(gate_mod, "create_chat_llm", lambda **_: llm)
    return create_chunk_gate_chain(model_name="m", base_url="http://x", api_key="k")


def test_parse_repairs_missing_comma_between_members():
    """Qwen (temperature 0) drops the comma after a reasoning string — the exact
    shape behind the 'gate parse error' failures of 2026-09-15."""
    raw = (
        '{\n"c1_chunk_informative": [1],\n'
        '"c1_reasoning": "Чанк содержит важные правовые нормы.",\n'
        '"c1_confidence": 0.95,\n\n'
        '"c2_chunk_reference_clarity": [1],\n'
        '"c2_reasoning": "Смысл норм понятен сам по себе"\n'
        '"c2_confidence": 0.9,\n\n'
        '"c3_chunk_multi_suitability": [0],\n'
        '"c3_reasoning": "Один факт",\n'
        '"c3_confidence": 0.85\n}'
    )
    parsed = _parse_gate_response(raw)
    assert parsed["c2_chunk_reference_clarity"] == [1]
    assert parsed["c2_reasoning"] == "Смысл норм понятен сам по себе"
    assert parsed["c2_confidence"] == 0.9
    assert parsed["c3_chunk_multi_suitability"] == [0]


def test_parse_repairs_missing_comma_after_number_and_trailing_comma():
    raw = (
        '{"c1_chunk_informative": [1], "c1_reasoning": "ok", "c1_confidence": 0.9\n'
        '"c2_chunk_reference_clarity": [1], "c2_reasoning": "ok", "c2_confidence": 0.8\n'
        '"c3_chunk_multi_suitability": [1], "c3_reasoning": "ok", "c3_confidence": 0.7,\n}'
    )
    parsed = _parse_gate_response(raw)
    assert parsed["c1_confidence"] == 0.9
    assert parsed["c2_confidence"] == 0.8
    assert parsed["c3_chunk_multi_suitability"] == [1]


def test_parse_repair_leaves_valid_json_alone():
    """The repair only runs after strict parsing failed, so escaped newlines and
    quotes inside reasoning strings survive untouched."""
    raw = json.dumps({
        "c1_chunk_informative": [1],
        "c1_reasoning": 'Строка с "кавычками"\nи переносом',
        "c1_confidence": 0.95,
        "c2_chunk_reference_clarity": [1],
        "c2_reasoning": "ok",
        "c2_confidence": 0.9,
        "c3_chunk_multi_suitability": [0],
        "c3_reasoning": "ok",
        "c3_confidence": 0.85,
    }, ensure_ascii=False)
    parsed = _parse_gate_response(raw)
    assert parsed["c1_reasoning"] == 'Строка с "кавычками"\nи переносом'


def test_parse_missing_c2_raises_key_error():
    raw = json.dumps({"c1_chunk_informative": [1], "c3_chunk_multi_suitability": [1]})
    with pytest.raises(KeyError):
        _parse_gate_response(raw)


def test_invoke_returns_verdict_for_full_json(monkeypatch):
    chain = _gate_chain_with_llm(monkeypatch, lambda _msgs: SimpleNamespace(content=_FULL_GATE_JSON))
    out = chain.invoke({"chunk": "text", "question_type": "multi"})
    assert out.passed is False
    assert out.rejection_reason == "chunk_not_suitable_for_multi"


def test_invoke_truncated_json_raises_gate_parse_error(monkeypatch):
    truncated = _FULL_GATE_JSON[: len(_FULL_GATE_JSON) // 2]
    chain = _gate_chain_with_llm(monkeypatch, lambda _msgs: SimpleNamespace(content=truncated))
    with pytest.raises(GateParseError) as exc_info:
        chain.invoke({"chunk": "text", "question_type": "open"})
    assert exc_info.value.raw_snippet == truncated[:300]
    assert isinstance(exc_info.value, ValueError)


def test_invoke_missing_key_raises_gate_parse_error(monkeypatch):
    partial = json.dumps({"c1_chunk_informative": [0], "c1_confidence": 0.9})
    chain = _gate_chain_with_llm(monkeypatch, lambda _msgs: SimpleNamespace(content=partial))
    with pytest.raises(GateParseError):
        chain.invoke({"chunk": "text", "question_type": "open"})


def test_invoke_llm_bad_request_propagates(monkeypatch):
    """400 от LLM (context length и т.п.) — не вердикт по чанку, а ошибка."""
    request = httpx.Request("POST", "http://x/v1/chat/completions")
    err = BadRequestError("ctx", response=httpx.Response(400, request=request), body=None)

    def failing_llm(_msgs):
        raise err

    chain = _gate_chain_with_llm(monkeypatch, failing_llm)
    with pytest.raises(BadRequestError):
        chain.invoke({"chunk": "text", "question_type": "open"})


def test_conservative_parse_failure_removed():
    assert not hasattr(gate_mod, "_conservative_parse_failure")


# ─── Run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Score Extraction Tests ===")
    test_extract_scores_no_zero_fill()
    test_extract_scores_no_greedy_fallback()
    test_extract_scores_hyphen_separator()
    test_extract_scores_slash_format()
    test_extract_scores_braced_real_llm_output()
    test_extract_scores_skips_format_spec_placeholder()
    test_extract_scores_braced_few_shot_format()
    print("  score extraction edge cases  ✓")

    print("\n=== Weighted Scoring Tests ===")
    test_incomplete_parse_zeros_total()
    print("  incomplete parse: total=0  ✓")
    test_weighted_all_ones_open()
    test_weighted_all_ones_multi()
    test_weighted_multiplier_zero_open()
    test_weighted_half_weight()
    test_weighted_multi_one_critical_zero()

    print("\n=== Chunk Gate Parsing Tests ===")
    test_parse_valid_json()
    test_parse_single_quoted_json()
    test_parse_markdown_fenced()
    test_parse_bare_int()
    test_parse_bare_float()

    print("\n=== Chunk Gate Decision Tests ===")
    test_decide_pass()
    test_decide_c1_fail()
    test_decide_c2_fail()
    test_decide_c3_fail_multi()
    test_decide_c3_fail_open_passes()
    test_decide_c3_fail_one_passes()

    print("\n✓ All 20 tests passed!\n")


# ─── 3. Kind addenda, block-type header, c1_category ──────────────────

from agent.nodes.chunk_gate.chunk_gate import (  # noqa: E402
    _read_gate_prompt,
    _read_kind_addenda,
    build_gate_messages,
)


def test_gate_messages_for_normative_are_unchanged():
    base = _read_gate_prompt()
    addenda = _read_kind_addenda()
    system, human = build_gate_messages(base, addenda, "Статья 1. Текст.", "normative", "body")
    assert system == base
    assert human == "Текстовый чанк для оценки:\n\nСтатья 1. Текст."
    # Unknown / missing kind falls back to normative too.
    assert build_gate_messages(base, addenda, "x", None, None) == build_gate_messages(base, addenda, "x", "weird", "inset")


def test_gate_messages_for_textbook_add_addendum_and_header():
    base = _read_gate_prompt()
    addenda = _read_kind_addenda()
    assert "textbook" in addenda and "encyclopedia" in addenda
    system, human = build_gate_messages(base, addenda, "Глава 1 > § 2\n\nТекст.", "textbook", "inset")
    assert system.startswith(base.rstrip())
    assert system.rstrip().endswith(addenda["textbook"])
    assert "c1_category" in system
    assert "Выясните" in system
    assert human.startswith("Тип документа: учебник\nТип блока: врезка\n\nТекстовый чанк для оценки:\n\n")
    assert human.endswith("Глава 1 > § 2\n\nТекст.")
    # Unknown block type: header names only the document kind.
    _, human_body = build_gate_messages(base, addenda, "Текст.", "textbook", None)
    assert human_body == "Тип документа: учебник\n\nТекстовый чанк для оценки:\n\nТекст."
    _, human_enc = build_gate_messages(base, addenda, "Текст.", "encyclopedia", "glossary_entry")
    assert human_enc.startswith("Тип документа: энциклопедия\nТип блока: словарная статья\n")


def _gate_json(c1=1, c2=1, c3=1, **extra):
    data = {
        "c1_chunk_informative": [c1], "c1_reasoning": "r1", "c1_confidence": 0.9,
        "c2_chunk_reference_clarity": [c2], "c2_reasoning": "r2", "c2_confidence": 0.9,
        "c3_chunk_multi_suitability": [c3], "c3_reasoning": "r3", "c3_confidence": 0.9,
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def test_parse_gate_response_normalizes_c1_category():
    assert _parse_gate_response(_gate_json())["c1_category"] is None
    assert _parse_gate_response(_gate_json(c1=0, c1_category="exercise"))["c1_category"] == "exercise"
    assert _parse_gate_response(_gate_json(c1=0, c1_category=" Front-Matter "))["c1_category"] == "front_matter"
    assert _parse_gate_response(_gate_json(c1=0, c1_category="whatever"))["c1_category"] is None
    assert _parse_gate_response(_gate_json(c1=0, c1_category=42))["c1_category"] is None


def test_decide_maps_c1_category_to_reason():
    assert _decide(_parse_gate_response(_gate_json(c1=0, c1_category="exercise")), "open") == (False, "chunk_exercise_block")
    assert _decide(_parse_gate_response(_gate_json(c1=0, c1_category="front_matter")), "open") == (False, "chunk_front_matter")
    assert _decide(_parse_gate_response(_gate_json(c1=0, c1_category="caption_only")), "open") == (False, "chunk_not_informative")
    assert _decide(_parse_gate_response(_gate_json(c1=0)), "open") == (False, "chunk_not_informative")
    # A category never overrides a passing c1.
    assert _decide(_parse_gate_response(_gate_json(c1=1, c1_category="exercise")), "open") == (True, None)


def test_gate_header_marks_truncated_boundaries_only_for_prose_kinds():
    base, addenda = "BASE", {"textbook": "TB", "encyclopedia": "ENC"}
    cut = "Глава 1 > § 2\n\nначалось с обрывка. Основной текст обрывается на середине"
    _, human = build_gate_messages(base, addenda, cut, "textbook", "body")
    assert human.startswith(
        "Тип документа: учебник\nТип блока: основной текст\n"
        "Автоматическая проверка границ: начало — обрыв предложения; конец — обрыв предложения\n\n"
    )
    _, human_end = build_gate_messages(base, addenda, "Глава 1 > § 2\n\nЦелое начало. Оборванный конец", "textbook", None)
    assert "Автоматическая проверка границ: конец — обрыв предложения\n\n" in human_end
    # Целый чанк — строки проверки нет; контекстная строка без точки не считается обрывом.
    _, human_ok = build_gate_messages(base, addenda, "Глава 1 > § 2\n\nЦелый абзац с точкой.", "textbook", "inset")
    assert "Автоматическая проверка" not in human_ok
    assert human_ok.startswith("Тип документа: учебник\nТип блока: врезка\n\nТекстовый чанк для оценки:")
    # Перечень после двоеточия и маркер списка — не обрыв.
    _, human_list = build_gate_messages(base, addenda, "§ 3\n\nК признакам относятся:", "textbook", None)
    assert "Автоматическая проверка" not in human_list
    _, human_item = build_gate_messages(base, addenda, "§ 3\n\n2) население и территория.", "encyclopedia", None)
    assert "Автоматическая проверка" not in human_item
    # Нормативка — байт в байт как раньше, даже с обрывом.
    system, human_norm = build_gate_messages(base, addenda, "обрывок без точки", "normative", "body")
    assert system == base and human_norm == "Текстовый чанк для оценки:\n\nобрывок без точки"


def test_gate_boundary_flags_match_chunker_text_utils():
    """Копия логики в agent_api не должна разойтись с chunker/text_utils.boundary_flags."""
    import importlib.util
    from pathlib import Path as _P

    spec = importlib.util.spec_from_file_location("chunker_text_utils", _P(__file__).resolve().parent.parent / "chunker" / "text_utils.py")
    tu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tu)
    from agent.nodes.chunk_gate.chunk_gate import _boundary_flags

    cases = [
        "Целое предложение.", "обрывок в начале. Конец целый.", "Начало целое, конец оборван",
        "и то и другое оборвано", "«Цитата в начале со строчной» — нет.", "1. пункт перечня.",
        "• маркер списка без точки", "К признакам относятся:", "Вопрос?", "Восклицание!",
        "Многоточие…", "Конец со скобкой.)", "", "   ", "a latin start.",
    ]
    for text in cases:
        assert _boundary_flags(text) == tu.boundary_flags(text), text
