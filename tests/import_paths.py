"""Keep agent_api and dataset_api imports from shadowing each other's ``config`` module."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_AGENT_API = _REPO / "agent_api"
_DATASET_API = _REPO / "dataset_api"

_AGENT_CONFIG_DEPS = (
    "config",
    "llm_factory",
    "agent",
    "agent.assistant_graph",
    "agent.nodes.chunk_gate.chunk_gate",
    "agent.nodes.llm_validator.validator",
)


def prefer_agent_api() -> None:
    if str(_DATASET_API) in sys.path:
        sys.path.remove(str(_DATASET_API))
    if str(_AGENT_API) in sys.path:
        sys.path.remove(str(_AGENT_API))
    sys.path.insert(0, str(_AGENT_API))
    for name in _AGENT_CONFIG_DEPS:
        sys.modules.pop(name, None)
    for name in list(sys.modules):
        if name.startswith("agent."):
            sys.modules.pop(name, None)


def prefer_dataset_api() -> None:
    if str(_AGENT_API) in sys.path:
        sys.path.remove(str(_AGENT_API))
    if str(_DATASET_API) in sys.path:
        sys.path.remove(str(_DATASET_API))
    sys.path.insert(0, str(_DATASET_API))
    for name in ("config", "auth_utils", "auth_router", "request_context"):
        sys.modules.pop(name, None)
