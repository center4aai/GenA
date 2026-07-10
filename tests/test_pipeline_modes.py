from __future__ import annotations

import sys
import types
from importlib.util import find_spec
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_AGENT_API = _REPO / "agent_api"

if str(_AGENT_API) not in sys.path:
    sys.path.insert(0, str(_AGENT_API))


def _install_import_stubs():
    if find_spec("langgraph") is None:
        langgraph = types.ModuleType("langgraph")
        graph = types.ModuleType("langgraph.graph")
        graph.StateGraph = object
        graph.END = "__end__"
        graph.START = "__start__"
        checkpoint = types.ModuleType("langgraph.checkpoint")
        checkpoint_base = types.ModuleType("langgraph.checkpoint.base")
        checkpoint_base.BaseCheckpointSaver = object
        sys.modules.setdefault("langgraph", langgraph)
        sys.modules.setdefault("langgraph.graph", graph)
        sys.modules.setdefault("langgraph.checkpoint", checkpoint)
        sys.modules.setdefault("langgraph.checkpoint.base", checkpoint_base)

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


class _Chain:
    def __init__(self, result):
        self.calls = 0
        self.result = result

    def invoke(self, _input):
        self.calls += 1
        return self.result


def _assistant():
    from agent.assistant_graph import GENAAssistant

    gate = _Chain({"passed": True, "rejection_reason": None})
    assistant = GENAAssistant.__new__(GENAAssistant)
    assistant.chunk_gate_chain = gate
    return assistant, gate


def _input(pipeline_mode: str):
    return {
        "chunk": "A useful legal source chunk.",
        "question_type": "open",
        "source": "test",
        "source_text": "A useful legal source chunk.",
        "pipeline_mode": pipeline_mode,
    }


def test_generator_validator_mode_skips_gate_and_refine():
    from agent.assistant_graph import GENAAssistant

    assistant, gate = _assistant()
    output = assistant.chunk_gate_node(_input("generator_validator"))

    assert gate.calls == 0
    assert output["chunk_rejected"] is False
    assert output["chunk_gate_result"]["rejection_reason"] == "gate_disabled_by_pipeline_mode"
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "generator_validator",
            "validation_result": {"passed": False},
            "retry_count": 0,
        }
    ) == "end"


def test_generator_validator_gate_mode_uses_gate_but_skips_refine():
    from agent.assistant_graph import GENAAssistant

    assistant, gate = _assistant()
    output = assistant.chunk_gate_node(_input("generator_validator_gate"))

    assert gate.calls == 1
    assert output["chunk_rejected"] is False
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "generator_validator_gate",
            "validation_result": {"passed": False},
            "retry_count": 0,
        }
    ) == "end"


def test_full_mode_refines_failed_validation():
    from agent.assistant_graph import GENAAssistant

    assistant, gate = _assistant()
    output = assistant.chunk_gate_node(_input("full"))

    assert gate.calls == 1
    assert output["chunk_rejected"] is False
    assert GENAAssistant._should_retry(
        {
            "pipeline_mode": "full",
            "validation_result": {"passed": False},
            "retry_count": 0,
        }
    ) == "refine"
