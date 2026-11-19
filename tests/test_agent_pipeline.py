"""Unit tests for Phase 4 agent pipeline helpers."""

from __future__ import annotations

import sys
import types
from importlib.util import find_spec
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AGENT_API = _REPO / "agent_api"
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()


def _install_import_stubs():
    broken_langgraph = False
    try:
        # Import for real: the previous check treated "not imported yet" as
        # "broken" (sys.modules.get returns None), so the stub replaced a
        # perfectly good langgraph on every run — which is why no test ever
        # exercised the compiled graph.
        import langgraph.graph  # noqa: F401

        broken_langgraph = False
    except Exception:
        broken_langgraph = True
    if broken_langgraph:
        for name in list(sys.modules):
            if name == "langgraph" or name.startswith("langgraph."):
                del sys.modules[name]
        langgraph = types.ModuleType("langgraph")
        langgraph.__spec__ = types.SimpleNamespace(name="langgraph")
        graph = types.ModuleType("langgraph.graph")
        graph.StateGraph = object
        graph.END = "__end__"
        graph.START = "__start__"
        checkpoint = types.ModuleType("langgraph.checkpoint")
        checkpoint_base = types.ModuleType("langgraph.checkpoint.base")
        checkpoint_base.BaseCheckpointSaver = object
        sys.modules["langgraph"] = langgraph
        sys.modules["langgraph.graph"] = graph
        sys.modules["langgraph.checkpoint"] = checkpoint
        sys.modules["langgraph.checkpoint.base"] = checkpoint_base

    if find_spec("langchain_core") is None:
        langchain_core = types.ModuleType("langchain_core")
        runnables = types.ModuleType("langchain_core.runnables")

        class _Runnable:
            def __class_getitem__(cls, _item):
                return cls

        runnables.Runnable = _Runnable
        sys.modules.setdefault("langchain_core", langchain_core)
        sys.modules.setdefault("langchain_core.runnables", runnables)


_install_import_stubs()

from agent.assistant_graph import GENAAssistant, shuffle_answer_options
from agent.nodes.chunk_gate.chunk_gate import synthetic_gate_pass
from agent.llm_parsing import parse_assessment_model, strip_markdown_fences
from agent.nodes.assess_difficulty.estimation import DifficultyOutput
from agent.nodes.assess_sensitivity.estimation import ProvocativenessOutput
from agent.nodes.normalize_output import normalize_question_output
from agent.schemas.question import format_question_context


def test_strip_markdown_fences():
    raw = '```json\n{"task": "Q"}\n```'
    assert strip_markdown_fences(raw) == '{"task": "Q"}'


def test_normalize_open_clears_options():
    q = normalize_question_output(
        {"task": "T", "option_1": "A", "outputs": "answer", "text": "None"},
        "open",
    )
    assert q["option_1"] is None
    assert q["outputs"] == "answer"
    assert q["text"] is None


def test_normalize_one_does_not_split_digits():
    q = normalize_question_output(
        {
            "task": "T",
            "outputs": "13",
            "option_1": "A",
            "option_2": "B",
            "option_3": "C",
            "option_4": "D",
        },
        "one",
    )
    assert q["outputs"] == ""


def test_normalize_one_keeps_a_correct_option_past_option_4():
    """The answer moves into the kept range instead of being discarded.

    Truncating first left `outputs` empty, which also made the option shuffler
    a no-op — so an unanswerable question with the model's original ordering
    reached the database.
    """
    q = normalize_question_output(
        {
            "task": "T",
            "outputs": 5,
            "option_1": "A",
            "option_2": "B",
            "option_3": "C",
            "option_4": "D",
            "option_5": "E",
        },
        "one",
    )
    assert q["option_5"] is None
    assert q["outputs"] != ""
    # the correct text survived, at whichever slot it was moved to
    assert q[f"option_{q['outputs']}"] == "E"


def test_normalize_one_drops_index_with_no_room_to_move():
    """Nothing to swap with: the index is still cleared rather than left dangling."""
    q = normalize_question_output(
        {"task": "T", "outputs": 5, "option_5": "E"},
        "one",
    )
    assert q["option_5"] is None
    assert q["outputs"] == ""


def test_normalize_multi_outputs_list():
    q = normalize_question_output(
        {"task": "T", "outputs": [1, 3], "option_1": "A", "option_3": "C"},
        "multi",
    )
    assert q["outputs"] == "1,3"


def test_shuffle_remaps_by_position_not_value():
    question = {
        "task": "Pick",
        "option_1": "A",
        "option_2": "B",
        "option_3": "A",
        "option_4": "C",
        "outputs": 1,
    }
    shuffled = shuffle_answer_options(dict(question), "one")
    outputs = int(shuffled["outputs"])
    correct_value = shuffled[f"option_{outputs}"]
    assert correct_value == "A"
    positions = [
        shuffled[f"option_{i}"]
        for i in range(1, 5)
        if shuffled.get(f"option_{i}") not in (None, "None")
    ]
    assert positions.count("A") == 2


def test_shuffle_multi_indices_follow_permutation():
    question = {
        "task": "Pick many",
        "option_1": "A",
        "option_2": "B",
        "option_3": "C",
        "option_4": "D",
        "option_5": "E",
        "option_6": "F",
        "outputs": "1,3",
    }
    shuffled = shuffle_answer_options(dict(question), "multi")
    indices = [int(x) for x in str(shuffled["outputs"]).split(",")]
    values = {shuffled[f"option_{i}"] for i in indices}
    assert values == {"A", "C"}


def test_format_question_context_treats_none_string_as_empty():
    assert format_question_context("None") == "Нет дополнительного контекста"
    assert format_question_context("  context  ") == "context"


def test_parse_assessment_uses_score_alias_when_primary_invalid():
    fb = DifficultyOutput(difficulty=1, explanation="fb")
    result = parse_assessment_model(
        '{"difficulty": 0, "score": 2, "explanation": "ok"}',
        DifficultyOutput,
        "difficulty",
        fb,
        score_aliases=("score", "difficulty_level", "level"),
    )
    assert result.difficulty == 2
    assert result.explanation == "ok"


def test_parse_assessment_accepts_explanation_aliases():
    fb = ProvocativenessOutput(provocativeness_score=1, explanation="fb")
    result = parse_assessment_model(
        '{"provocativeness_score": 2, "reason": "sensitive topic"}',
        ProvocativenessOutput,
        "provocativeness_score",
        fb,
        score_aliases=("provocativeness", "sensitivity", "sensitivity_level"),
    )
    assert result.provocativeness_score == 2
    assert result.explanation == "sensitive topic"


def test_parse_assessment_missing_score_is_not_replaced_by_a_number():
    """No score in the response means "no assessment", not the fallback's value."""
    fb = DifficultyOutput(difficulty=None, explanation=None, assessment_failed=True)
    result = parse_assessment_model(
        '{"explanation": "the model forgot the score"}',
        DifficultyOutput,
        "difficulty",
        fb,
        score_aliases=("score", "difficulty_level", "level"),
    )
    assert result.difficulty is None


def test_synthetic_gate_pass_includes_criteria():
    gate = synthetic_gate_pass("chunk_pre_validated")
    assert gate["passed"] is True
    assert gate["rejection_reason"] == "chunk_pre_validated"
    assert gate["c1_chunk_informative"] == [1]
    assert gate["c3_chunk_multi_suitability"] == [1]


def test_chunk_pre_validated_skips_gate_chain():
    class _Gate:
        calls = 0

        def invoke(self, _input):
            _Gate.calls += 1
            return {"passed": True, "rejection_reason": None}

    assistant = GENAAssistant.__new__(GENAAssistant)
    assistant.chunk_gate_chain = _Gate()
    output = assistant.chunk_gate_node(
        {
            "chunk": "A useful legal source chunk.",
            "question_type": "multi",
            "source": "test",
            "source_text": "A useful legal source chunk.",
            "pipeline_mode": "full",
            "chunk_pre_validated": True,
        }
    )
    assert _Gate.calls == 0
    assert output["chunk_rejected"] is False
    assert output["chunk_gate_result"]["rejection_reason"] == "chunk_pre_validated"
    assert output["chunk_gate_result"]["c3_chunk_multi_suitability"] == [1]


def test_should_retry_requires_refine_available():
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "full",
            "validation_result": {"passed": False},
            "retry_count": 0,
            "refine_available": True,
        }
    ) == "refine"
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "full",
            "validation_result": {"passed": False},
            "retry_count": 0,
            "refine_available": False,
        }
    ) == "end"


# --- compiled graph: assessment/refine failure markers ----------------------


def _real_langgraph() -> bool:
    try:
        from langgraph.graph import StateGraph
    except Exception:
        return False
    return hasattr(StateGraph, "add_node")


class _Stub:
    def __init__(self, results):
        self.calls = 0
        self.inputs = []
        self._results = list(results)

    def invoke(self, input_data):
        self.calls += 1
        self.inputs.append(input_data)
        idx = min(self.calls - 1, len(self._results) - 1)
        return self._results[idx]


_QUESTION = {
    "task": "Кто принимает закон?",
    "option_1": "Парламент",
    "option_2": "Суд",
    "option_3": "Мэрия",
    "option_4": "Банк",
    "outputs": 1,
}
_FAILED_VALIDATION = {"passed": False, "total": 3.0, "max_total": 10.0, "threshold": 7.0}
_PASSED_VALIDATION = {"passed": True, "total": 9.0, "max_total": 10.0, "threshold": 7.0}


def _graph_input():
    return {
        "chunk": "Закон принимает парламент.",
        "question_type": "one",
        "source": "test",
        "source_text": "Закон принимает парламент.",
        "pipeline_mode": "full",
        "document_kind": "normative",
        "chunk_pre_validated": True,
        "refine_available": True,
        "retry_count": 0,
    }


def _build(provocativeness, difficulty, validation, refine):
    return GENAAssistant(
        generate_question_chain=_Stub([dict(_QUESTION)]),
        provocativeness_chain=provocativeness,
        validation_chain=validation,
        difficulty_chain=difficulty,
        chunk_gate_chain=None,
        refine_question_chain=refine,
    )


@pytest.mark.skipif(not _real_langgraph(), reason="langgraph is stubbed in this environment")
def test_refine_failed_ends_graph_without_reassessment():
    provocativeness = _Stub([ProvocativenessOutput(provocativeness_score=1, explanation="ok")])
    difficulty = _Stub([DifficultyOutput(difficulty=2, explanation="ok")])
    validation = _Stub([_FAILED_VALIDATION])
    refine = _Stub([dict(_QUESTION, refine_failed=True, refine_error="parse failed: boom")])

    output = _build(provocativeness, difficulty, validation, refine).graph.invoke(_graph_input())

    assert refine.calls == 1
    # only the initial round: no second pass over the same question
    assert provocativeness.calls == 1
    assert difficulty.calls == 1
    assert validation.calls == 1
    assert output["refine_failed"] is True
    assert output["generated_question"]["task"] == _QUESTION["task"]
    assert "refine_failed" not in output["generated_question"]
    assert output["assessment_failed"] is False
    assert output["assessment_errors"] == []


@pytest.mark.skipif(not _real_langgraph(), reason="langgraph is stubbed in this environment")
def test_refine_success_reassesses_and_marks_nothing():
    provocativeness = _Stub([ProvocativenessOutput(provocativeness_score=1, explanation="ok")])
    difficulty = _Stub([DifficultyOutput(difficulty=2, explanation="ok")])
    validation = _Stub([_FAILED_VALIDATION, _PASSED_VALIDATION])
    refine = _Stub([dict(_QUESTION, task="Какой орган принимает закон?")])

    output = _build(provocativeness, difficulty, validation, refine).graph.invoke(_graph_input())

    assert refine.calls == 1
    assert provocativeness.calls == 2
    assert difficulty.calls == 2
    assert validation.calls == 2
    assert output.get("refine_failed", False) is False
    assert output["generated_question"]["task"] == "Какой орган принимает закон?"
    assert output["assessment_failed"] is False


@pytest.mark.skipif(not _real_langgraph(), reason="langgraph is stubbed in this environment")
def test_failed_assessment_is_reported_not_stored_as_a_score():
    provocativeness = _Stub([
        ProvocativenessOutput(
            provocativeness_score=None,
            explanation=None,
            assessment_failed=True,
            assessment_error="llm call failed: 400 bad request",
        )
    ])
    difficulty = _Stub([DifficultyOutput(difficulty=2, explanation="fine")])
    validation = _Stub([_PASSED_VALIDATION])

    output = _build(provocativeness, difficulty, validation, None).graph.invoke(_graph_input())

    assert output["assessment_failed"] is True
    assert output["assessment_errors"] == ["provocativeness: llm call failed: 400 bad request"]
    assert output["sensitivity_score"]["provocativeness_score"] is None
    assert output["sensitivity_score"]["explanation"] is None
    assert output["sensitivity_score"]["assessment_failed"] is True
    assert output["difficulty_score"] == {
        "difficulty": 2,
        "explanation": "fine",
        "assessment_failed": False,
        "assessment_error": None,
    }
    assert output["validation_result"]["passed"] is True


@pytest.mark.skipif(not _real_langgraph(), reason="langgraph is stubbed in this environment")
def test_assessment_markers_reflect_the_last_round():
    """A failure in round one must not stick once the refined question is assessed fine."""
    provocativeness = _Stub([
        ProvocativenessOutput(assessment_failed=True, assessment_error="garbage"),
        ProvocativenessOutput(provocativeness_score=1, explanation="ok"),
    ])
    difficulty = _Stub([
        DifficultyOutput(assessment_failed=True, assessment_error="garbage"),
        DifficultyOutput(difficulty=3, explanation="ok"),
    ])
    validation = _Stub([_FAILED_VALIDATION, _PASSED_VALIDATION])
    refine = _Stub([dict(_QUESTION, task="Какой орган принимает закон?")])

    output = _build(provocativeness, difficulty, validation, refine).graph.invoke(_graph_input())

    assert output["assessment_failed"] is False
    assert output["assessment_errors"] == []
    assert output["sensitivity_score"]["provocativeness_score"] == 1
    assert output["difficulty_score"]["difficulty"] == 3


def test_provocativeness_node_treats_missing_score_as_failure():
    assistant = GENAAssistant.__new__(GENAAssistant)
    assistant.provocativeness_chain = _Stub([{"explanation": "no score"}])
    output = assistant.provocativeness_node(
        {"generated_question": dict(_QUESTION), "source_text": "x"}
    )
    assert output["assessment_failed"] is True
    assert output["sensitivity_score"]["provocativeness_score"] is None
    assert output["assessment_errors"] == ["provocativeness: provocativeness_score missing"]


def test_difficulty_node_accumulates_errors():
    assistant = GENAAssistant.__new__(GENAAssistant)
    assistant.difficulty_chain = _Stub([{"difficulty": None, "assessment_failed": True, "assessment_error": "x"}])
    output = assistant.difficulty_node(
        {
            "generated_question": dict(_QUESTION),
            "source_text": "x",
            "assessment_failed": True,
            "assessment_errors": ["provocativeness: y"],
        }
    )
    assert output["assessment_failed"] is True
    assert output["assessment_errors"] == ["provocativeness: y", "difficulty: x"]
    assert output["difficulty_score"]["difficulty"] is None


def test_should_retry_stops_after_refine_failed():
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "full",
            "validation_result": {"passed": False},
            "retry_count": 0,
            "refine_available": True,
            "refine_failed": True,
        }
    ) == "end"
    assert GENAAssistant._after_refine({"refine_failed": True}) == "end"
    assert GENAAssistant._after_refine({}) == "assess"


def test_chunk_gate_node_passes_document_kind_and_block_type():
    class _Gate:
        seen = None

        def invoke(self, _input):
            _Gate.seen = dict(_input)
            return {"passed": True, "rejection_reason": None}

    assistant = GENAAssistant.__new__(GENAAssistant)
    assistant.chunk_gate_chain = _Gate()
    assistant.chunk_gate_node(
        {
            "chunk": "Врезка учебника.",
            "question_type": "open",
            "source": "test",
            "source_text": "Врезка учебника.",
            "pipeline_mode": "full",
            "document_kind": "textbook",
            "block_type": "inset",
        }
    )
    assert _Gate.seen == {
        "chunk": "Врезка учебника.",
        "question_type": "open",
        "document_kind": "textbook",
        "block_type": "inset",
    }
    # Legacy state without kind/block_type: defaults, not KeyError.
    assistant.chunk_gate_node(
        {
            "chunk": "x",
            "question_type": "one",
            "source": "test",
            "source_text": "x",
            "pipeline_mode": "full",
        }
    )
    assert _Gate.seen["document_kind"] == "normative"
    assert _Gate.seen["block_type"] is None
