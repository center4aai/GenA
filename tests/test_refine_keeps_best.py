"""The refine loop must never return a question worse than an earlier attempt.

LangGraph drops any key a node returns that is not declared in the state
schema, which silently disabled the "keep the best attempt" logic: the
comparison in `validation_node` always saw `best_validation_total is None`,
so a refine that lowered the score replaced the better question for good.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()

# These tests are about real LangGraph behaviour; a stub would prove nothing.
# Sibling test modules install a stub when langgraph is unavailable, and that
# stub also satisfies importorskip — so check the API, not just the import.
pytest.importorskip("langgraph", reason="langgraph not installed")

from langgraph.graph import END, START, StateGraph  # noqa: E402

if not hasattr(StateGraph, "add_node"):  # stubbed as `object`
    pytest.skip("langgraph is stubbed in this environment", allow_module_level=True)

from agent.assistant_graph import AgentState  # noqa: E402

REFINE_KEYS = (
    "original_question",
    "best_question",
    "best_validation_total",
    "best_validation_result",
)


def test_refine_tracking_keys_are_declared_in_state():
    missing = [k for k in REFINE_KEYS if k not in AgentState.__annotations__]
    assert not missing, (
        f"{missing} are written by graph nodes but absent from AgentState; "
        "LangGraph would drop them and the best-attempt logic would die again"
    )


def test_declared_keys_survive_a_node_hop():
    """Guards the mechanism itself, not just today's key list."""
    seen = {}

    def write(state):
        return {**state, "best_question": {"task": "best"}, "best_validation_total": 17.5}

    def read(state):
        seen["best_question"] = state.get("best_question")
        seen["best_validation_total"] = state.get("best_validation_total")
        return state

    graph = StateGraph(AgentState)
    graph.add_node("write", write)
    graph.add_node("read", read)
    graph.add_edge(START, "write")
    graph.add_edge("write", "read")
    graph.add_edge("read", END)
    graph.compile().invoke({"chunk": "c", "question_type": "one", "retry_count": 0})

    assert seen["best_question"] == {"task": "best"}
    assert seen["best_validation_total"] == 17.5


def test_undeclared_keys_are_dropped():
    """Documents why the keys above must stay declared."""
    seen = {}

    def write(state):
        return {**state, "not_in_schema": "gone"}

    def read(state):
        seen["value"] = state.get("not_in_schema")
        return state

    graph = StateGraph(AgentState)
    graph.add_node("write", write)
    graph.add_node("read", read)
    graph.add_edge(START, "write")
    graph.add_edge("write", "read")
    graph.add_edge("read", END)
    graph.compile().invoke({"chunk": "c", "question_type": "one", "retry_count": 0})

    assert seen["value"] is None


def _best_of(attempts: list[tuple[dict, Optional[float]]]) -> dict:
    """Replay validation_node's comparison over a sequence of attempts."""
    best_question: Optional[dict] = None
    best_total: Optional[float] = None
    current = {}
    for question, total in attempts:
        current = question
        if best_total is None or (total is not None and total >= best_total):
            best_question, best_total = question, total
        else:
            current = best_question
    return current


def test_worse_refine_does_not_replace_a_better_attempt():
    first = {"task": "good question?"}
    worse = {"task": "bad"}
    assert _best_of([(first, 17.5), (worse, 6.0)]) == first
    assert _best_of([(first, 17.5), (worse, 18.0)]) == worse
