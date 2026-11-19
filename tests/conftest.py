"""
Shared pytest helpers for the GenA test suite.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

# Реестр моделей создаётся при импорте agent_api и сам запускает probe: пробные
# chat-запросы к настоящим моделям из тестов не нужны (тесты smoke задают интервал явно).
os.environ.setdefault("MODEL_SMOKE_INTERVAL_SEC", "0")
from unittest.mock import MagicMock, patch


def ok_json_response(payload):
    m = MagicMock()
    m.status_code = 200
    m.json = MagicMock(return_value=payload)
    m.text = str(payload)
    return m


@contextmanager
def patch_module_http(module: str, payload=()):
    """Patch `get/post/put/delete` on a module that imported them from an HTTP client."""
    m = ok_json_response(list(payload))
    with (
        patch(f"{module}.get", return_value=m),
        patch(f"{module}.post", return_value=m),
        patch(f"{module}.put", return_value=m),
        patch(f"{module}.delete", return_value=m),
    ):
        yield m
