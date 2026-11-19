"""Unit tests for llm_factory error classification and timeout/retry defaults."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api

prefer_agent_api()

import llm_factory  # noqa: E402
from llm_factory import (  # noqa: E402
    TRANSIENT_LLM_ERRORS,
    create_chat_llm,
    transient_llm_error_kind,
)
from openai import (  # noqa: E402
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://llm.local/v1/chat/completions")


def _status_error(cls, status: int):
    return cls("msg", response=httpx.Response(status, request=_request()), body=None)


# ─── transient_llm_error_kind ─────────────────────────────────────────


def test_kind_timeout_openai():
    assert transient_llm_error_kind(APITimeoutError(request=_request())) == "timeout"


def test_kind_timeout_httpx_and_builtin():
    assert transient_llm_error_kind(httpx.ReadTimeout("t", request=_request())) == "timeout"
    assert transient_llm_error_kind(TimeoutError()) == "timeout"


def test_kind_unreachable():
    assert transient_llm_error_kind(APIConnectionError(request=_request())) == "unreachable"
    assert transient_llm_error_kind(httpx.ConnectError("c", request=_request())) == "unreachable"


def test_kind_busy_rate_limit():
    assert transient_llm_error_kind(_status_error(RateLimitError, 429)) == "busy"


def test_kind_busy_internal_server_error_503():
    assert transient_llm_error_kind(_status_error(InternalServerError, 503)) == "busy"


def test_kind_internal_server_error_500_is_not_classified():
    # 500 от LLM-хоста — не "busy": для API это нетранзиентная ошибка LLM.
    assert transient_llm_error_kind(_status_error(InternalServerError, 500)) is None


def test_kind_auth():
    assert transient_llm_error_kind(_status_error(AuthenticationError, 401)) == "auth"
    assert transient_llm_error_kind(_status_error(PermissionDeniedError, 403)) == "auth"


def test_kind_none_for_non_infra_errors():
    assert transient_llm_error_kind(_status_error(BadRequestError, 400)) is None
    assert transient_llm_error_kind(_status_error(NotFoundError, 404)) is None
    assert transient_llm_error_kind(ValueError("bad json")) is None
    assert transient_llm_error_kind(RuntimeError("boom")) is None


def test_transient_tuple_contents():
    for exc in (
        APITimeoutError(request=_request()),
        APIConnectionError(request=_request()),
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 503),
        _status_error(AuthenticationError, 401),
        httpx.ReadTimeout("t", request=_request()),
        TimeoutError(),
    ):
        assert isinstance(exc, TRANSIENT_LLM_ERRORS), type(exc).__name__
    assert not isinstance(_status_error(BadRequestError, 400), TRANSIENT_LLM_ERRORS)
    assert not isinstance(ValueError("x"), TRANSIENT_LLM_ERRORS)


# ─── create_chat_llm defaults ─────────────────────────────────────────


def _capture_openai_compatible(monkeypatch):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_factory, "_create_openai_compatible", fake)
    return captured


def test_create_chat_llm_defaults_from_config(monkeypatch):
    captured = _capture_openai_compatible(monkeypatch)
    monkeypatch.setattr(llm_factory, "LLM_TIMEOUT", 123)
    monkeypatch.setattr(llm_factory, "LLM_MAX_RETRIES", 4)

    create_chat_llm(provider="openai", model_name="m", base_url="http://x", api_key="k")

    assert captured["timeout"] == 123
    assert captured["max_retries"] == 4


def test_create_chat_llm_explicit_overrides_config(monkeypatch):
    captured = _capture_openai_compatible(monkeypatch)
    monkeypatch.setattr(llm_factory, "LLM_TIMEOUT", 123)
    monkeypatch.setattr(llm_factory, "LLM_MAX_RETRIES", 4)

    create_chat_llm(
        provider="openai", model_name="m", base_url="http://x", api_key="k",
        timeout=7, max_retries=0,
    )

    assert captured["timeout"] == 7
    assert captured["max_retries"] == 0
