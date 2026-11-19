"""Load task_worker/worker.py as a module (avoids config shadowing from dataset_api)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TASK_WORKER = _REPO / "task_worker"

_MODULE_NAME = "gena_task_worker_module"


def load_task_worker_module():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]

    if str(_TASK_WORKER) in sys.path:
        sys.path.remove(str(_TASK_WORKER))
    sys.path.insert(0, str(_TASK_WORKER))

    saved_config = sys.modules.get("config")
    sys.modules.pop("config", None)
    sys.modules.pop("worker", None)

    path = _TASK_WORKER / "worker.py"
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)

    # Worker bound its config at import time; restore prior `config` for other tests.
    if saved_config is not None:
        sys.modules["config"] = saved_config

    return module
