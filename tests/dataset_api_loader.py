"""Load dataset_api.py as a module (avoids empty dataset_api package __init__.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DATASET_API = _REPO / "dataset_api"
_AGENT_API = _REPO / "agent_api"

_MODULE_NAME = "gena_dataset_api_module"

_DATASET_DEPS = (
    "config",
    "auth_utils",
    "auth_router",
    "request_context",
)


def _ensure_dataset_api_path() -> None:
    if str(_AGENT_API) in sys.path:
        sys.path.remove(str(_AGENT_API))
    if str(_DATASET_API) in sys.path:
        sys.path.remove(str(_DATASET_API))
    sys.path.insert(0, str(_DATASET_API))


def _clear_dataset_import_cache() -> None:
    sys.modules.pop(_MODULE_NAME, None)
    for name in _DATASET_DEPS:
        sys.modules.pop(name, None)


def load_dataset_api_module():
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None and hasattr(cached, "_merge_dataset_metadata"):
        return cached

    _clear_dataset_import_cache()
    _ensure_dataset_api_path()

    path = _DATASET_API / "dataset_api.py"
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
