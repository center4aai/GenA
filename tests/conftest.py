"""
Pytest: put `gena` package on the path for `import gena` (add `gena_web/` to sys.path).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# Repository root: .../gena/ ; package lives in gena_web/
_REPO_ROOT = Path(__file__).resolve().parent.parent
_GENA_WEB = _REPO_ROOT / "gena_web"
if str(_GENA_WEB) not in sys.path:
    sys.path.insert(0, str(_GENA_WEB))


def ok_json_response(payload):
    m = MagicMock()
    m.status_code = 200
    m.json = MagicMock(return_value=payload)
    m.text = str(payload)
    return m


@contextmanager
def patch_view_http(module: str, payload=()):
    """Patch `get/post/put/delete` in a view module (where `from gena.http import get` is used)."""
    m = ok_json_response(list(payload))
    with (
        patch(f"{module}.get", return_value=m),
        patch(f"{module}.post", return_value=m),
        patch(f"{module}.put", return_value=m),
        patch(f"{module}.delete", return_value=m),
    ):
        yield m
