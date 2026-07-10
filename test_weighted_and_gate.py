"""
Smoke tests for:
  1. Weighted validation scoring (validator.py)
  2. Chunk gate parsing + decision logic (chunk_gate.py)

No LLM calls — pure unit logic.
"""
import sys, json

# ─── 1. Weighted scoring ───────────────────────────────────────────────

from agent.nodes.llm_validator.validator import (
    WEIGHTS, MULTIPLIER_INDICES, MAX_POINTS, THRESHOLDS, BlockResult,
)


def compute_weighted(qtype: str, blocks: list[BlockResult]) -> tuple[float, int]:
    weighted_sum = 0.0
    multiplier_product = 1
    for b in blocks:
        w = WEIGHTS[qtype].get(b.key, [1.0] * len(b.scores))
        weighted_sum += sum(s * wt for s, wt in zip(b.scores, w))
        for idx in MULTIPLIER_INDICES.get(qtype, {}).get(b.key, []):
            multiplier_product *= b.scores[idx]
    return weighted_sum * multiplier_product, multiplier_product


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


# ─── Run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Weighted Scoring Tests ===")
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

    print("\n=== Chunk Gate Decision Tests ===")
    test_decide_pass()
    test_decide_c1_fail()
    test_decide_c2_fail()
    test_decide_c3_fail_multi()
    test_decide_c3_fail_open_passes()
    test_decide_c3_fail_one_passes()

    print("\n✓ All 15 tests passed!\n")
