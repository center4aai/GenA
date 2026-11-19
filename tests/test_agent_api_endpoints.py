"""Unit tests for agent_api.py endpoints and chunk_gate pure logic."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

_REPO = Path(__file__).resolve().parent.parent
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
        checkpoint_mongo = types.ModuleType("langgraph.checkpoint.mongodb")
        checkpoint_mongo.MongoDBSaver = object
        sys.modules["langgraph"] = langgraph
        sys.modules["langgraph.graph"] = graph
        sys.modules["langgraph.checkpoint"] = checkpoint
        sys.modules["langgraph.checkpoint.base"] = checkpoint_base
        sys.modules["langgraph.checkpoint.mongodb"] = checkpoint_mongo


_install_import_stubs()

import agent_api as mod  # noqa: E402
from agent.nodes.chunk_gate.chunk_gate import (  # noqa: E402
    GateParseError,
    _decide,
    _normalize_gate_score,
    _parse_gate_response,
    synthetic_gate_pass,
)
from request_context import (  # noqa: E402
    GATE_LLM_AUTH_DETAIL,
    GATE_LLM_BUSY_DETAIL,
    GATE_LLM_ERROR_DETAIL,
    GATE_LLM_TIMEOUT_DETAIL,
    GATE_LLM_UNREACHABLE_DETAIL,
    GATE_PARSE_ERROR_DETAIL,
    LLM_AUTH_DETAIL,
    LLM_BUSY_DETAIL,
    LLM_TIMEOUT_DETAIL,
    LLM_UNREACHABLE_DETAIL,
    _sanitize_500_detail,
)


def _run(coro):
    return asyncio.run(coro)


def _llm_request() -> httpx.Request:
    return httpx.Request("POST", "http://llm.local/v1/chat/completions")


def _llm_status_error(cls, status: int):
    return cls("llm said no", response=httpx.Response(status, request=_llm_request()), body=None)


# ─── GET /models/ ─────────────────────────────────────────────────────


def test_list_models():
    model = SimpleNamespace(
        id="m1",
        name="Model One",
        base_url="http://localhost:8000",
        model_name="test-model",
        provider="openai",
    )
    with patch.object(mod.registry, "list_models", return_value=[model]):
        result = _run(mod.list_models())
    assert len(result) == 1
    assert result[0].id == "m1"
    assert result[0].name == "Model One"
    assert result[0].model_name == "test-model"
    assert result[0].provider == "openai"


# ─── GET /models/health/ ──────────────────────────────────────────────


def test_models_health():
    health_rows = [
        {"id": "m1", "name": "Model One", "available": True},
        {"id": "m2", "name": "Model Two", "available": False},
    ]
    with patch.object(mod.registry, "get_health", return_value=health_rows):
        result = _run(mod.models_health())
    assert len(result) == 2
    assert result[0].available is True
    assert result[1].available is False


# ─── GET /models/discover/ ────────────────────────────────────────────


def test_discover_models():
    discovered = [
        {
            "id": "m1",
            "name": "Discovered",
            "base_url": "http://x",
            "model_name": "disc-model",
            "provider": "openai",
            "available": True,
        }
    ]
    with patch.object(
        mod.registry, "discover_models", new_callable=AsyncMock, return_value=discovered
    ):
        result = _run(mod.discover_models())
    assert len(result) == 1
    assert result[0].id == "m1"
    assert result[0].available is True


# ─── POST /process_prompt/ ──────────────────────────────────────────


def _prompt_request(**kwargs):
    defaults = dict(
        prompt="What is law?",
        question_type="open",
        source="doc.pdf",
        chat_id=42,
    )
    defaults.update(kwargs)
    return mod.PromptRequest(**defaults)


def test_process_prompt_success():
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.return_value = {"questions": ["Q1"]}
    mod.handler = mock_handler

    result = _run(mod.process_prompt(_prompt_request()))
    assert result.status == "success"
    assert result.result == {"questions": ["Q1"]}
    mock_handler.ahandle_prompt.assert_called_once()
    call_kwargs = mock_handler.ahandle_prompt.call_args.kwargs
    assert call_kwargs.get("document_kind") == "normative"


def test_process_prompt_passes_document_kind_textbook():
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.return_value = {"questions": ["Q1"]}
    mod.handler = mock_handler

    _run(mod.process_prompt(_prompt_request(document_kind="textbook")))
    call_kwargs = mock_handler.ahandle_prompt.call_args.kwargs
    assert call_kwargs.get("document_kind") == "textbook"


def test_process_prompt_value_error():
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.side_effect = ValueError("bad prompt")
    mod.handler = mock_handler

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.process_prompt(_prompt_request()))
    assert exc_info.value.status_code == 400
    assert "bad prompt" in str(exc_info.value.detail)


def test_process_prompt_gate_parse_error_is_500_constant():
    # GateParseError — подкласс ValueError, но это сбой вывода LLM внутри полного
    # пайплайна, а не ошибка клиента: 500 с константным detail, без текста ответа.
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.side_effect = GateParseError(
        ValueError("Expecting value"), '{"c1_chunk_informative": [1'
    )
    mod.handler = mock_handler

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.process_prompt(_prompt_request()))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == GATE_PARSE_ERROR_DETAIL


def test_process_prompt_generic_exception():
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.side_effect = RuntimeError("boom")
    mod.handler = mock_handler

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.process_prompt(_prompt_request()))
    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "exc, status, detail",
    [
        (APIConnectionError(request=_llm_request()), 502, LLM_UNREACHABLE_DETAIL),
        (_llm_status_error(RateLimitError, 429), 503, LLM_BUSY_DETAIL),
        (_llm_status_error(AuthenticationError, 401), 401, LLM_AUTH_DETAIL),
        (httpx.ReadTimeout("t", request=_llm_request()), 504, LLM_TIMEOUT_DETAIL),
    ],
)
def test_process_prompt_llm_infra_error_maps_to_constant_detail(exc, status, detail):
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.side_effect = exc
    mod.handler = mock_handler

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.process_prompt(_prompt_request()))
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail
    # detail константный: текст исключения (и ключи) наружу не утекают.
    assert "llm said no" not in str(exc_info.value.detail)


def test_process_prompt_dict_prompt_extracted():
    mock_handler = MagicMock()
    mock_handler.ahandle_prompt.return_value = {"ok": True}
    mod.handler = mock_handler

    req = mod.PromptRequest.model_construct(
        prompt={"fragment_data": {"combined_text": "extracted text"}},
        question_type="open",
        source="doc.pdf",
        chat_id=42,
    )
    _run(mod.process_prompt(req))
    call_kwargs = mock_handler.ahandle_prompt.call_args.kwargs
    assert call_kwargs["prompt"] == "extracted text"


# ─── POST /rephrase_questions/ ────────────────────────────────────────


def test_rephrase_questions_success():
    mock_handler = MagicMock()
    mock_handler.ahandle_rephrase_questions.return_value = [{"q": "rephrased"}]
    mod.handler = mock_handler

    req = mod.RephraseQuestionsRequest(
        dataset_name="ds1",
        questions=[{"text": "old"}],
        model_id="m1",
    )
    result = _run(mod.rephrase_questions(req))
    assert result.status == "success"
    assert result.result == [{"q": "rephrased"}]


def test_rephrase_questions_value_error():
    mock_handler = MagicMock()
    mock_handler.ahandle_rephrase_questions.side_effect = ValueError("invalid")
    mod.handler = mock_handler

    req = mod.RephraseQuestionsRequest(dataset_name="ds1", questions=[])
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.rephrase_questions(req))
    assert exc_info.value.status_code == 400


def test_rephrase_questions_llm_busy_maps_to_503():
    mock_handler = MagicMock()
    mock_handler.ahandle_rephrase_questions.side_effect = _llm_status_error(RateLimitError, 429)
    mod.handler = mock_handler

    req = mod.RephraseQuestionsRequest(dataset_name="ds1", questions=[{"task": "q"}])
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.rephrase_questions(req))
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == LLM_BUSY_DETAIL


# ─── POST /chunk_gate/ ────────────────────────────────────────────────


def _chunk_gate_request(**kwargs):
    defaults = dict(chunk="Some legal text.", question_type="open")
    defaults.update(kwargs)
    return mod.ChunkGateRequest(**defaults)


def test_chunk_gate_success():
    gate_result = MagicMock()
    gate_result.model_dump.return_value = {
        "passed": True,
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    chain = MagicMock()
    chain.invoke.return_value = gate_result
    runnables = SimpleNamespace(chunk_gate_chain=chain)

    mock_handler = MagicMock()
    mock_handler._get_runnables.return_value = runnables
    mod.handler = mock_handler

    result = _run(mod.chunk_gate(_chunk_gate_request()))
    assert result.status == "success"
    assert result.result["passed"] is True
    chain.invoke.assert_called_once_with(
        {
            "chunk": "Some legal text.",
            "question_type": "open",
            "document_kind": "normative",
            "block_type": None,
        }
    )


def test_chunk_gate_forwards_document_kind_and_block_type():
    gate_result = MagicMock()
    gate_result.model_dump.return_value = {"passed": False, "rejection_reason": "chunk_exercise_block"}
    chain = MagicMock()
    chain.invoke.return_value = gate_result
    mock_handler = MagicMock()
    mock_handler._get_runnables.return_value = SimpleNamespace(chunk_gate_chain=chain)
    mod.handler = mock_handler

    result = _run(
        mod.chunk_gate(_chunk_gate_request(document_kind="textbook", block_type="inset"))
    )
    assert result.result["rejection_reason"] == "chunk_exercise_block"
    chain.invoke.assert_called_once_with(
        {
            "chunk": "Some legal text.",
            "question_type": "open",
            "document_kind": "textbook",
            "block_type": "inset",
        }
    )


def test_chunk_gate_chain_not_configured():
    runnables = SimpleNamespace(chunk_gate_chain=None)
    mock_handler = MagicMock()
    mock_handler._get_runnables.return_value = runnables
    mod.handler = mock_handler

    result = _run(mod.chunk_gate(_chunk_gate_request()))
    assert result.status == "success"
    assert result.result["passed"] is True
    assert result.result["rejection_reason"] == "gate_chain_not_configured"


def _chunk_gate_failing(exc):
    chain = MagicMock()
    chain.invoke.side_effect = exc
    runnables = SimpleNamespace(chunk_gate_chain=chain)
    mock_handler = MagicMock()
    mock_handler._get_runnables.return_value = runnables
    mod.handler = mock_handler


def test_chunk_gate_chain_error():
    _chunk_gate_failing(RuntimeError("chain failed"))

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.chunk_gate(_chunk_gate_request()))
    assert exc_info.value.status_code == 500
    # Нетранзиентная ошибка: полный текст — только в лог, наружу константа.
    assert exc_info.value.detail == GATE_LLM_ERROR_DETAIL


def test_chunk_gate_parse_error_is_500_not_verdict():
    _chunk_gate_failing(GateParseError(ValueError("Expecting value"), '{"c1_chunk_informative": [1'))

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.chunk_gate(_chunk_gate_request()))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == GATE_PARSE_ERROR_DETAIL


def test_chunk_gate_bad_request_is_gate_llm_error():
    _chunk_gate_failing(_llm_status_error(BadRequestError, 400))

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.chunk_gate(_chunk_gate_request()))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == GATE_LLM_ERROR_DETAIL


@pytest.mark.parametrize(
    "exc, status, detail",
    [
        (_llm_status_error(InternalServerError, 503), 503, GATE_LLM_BUSY_DETAIL),
        (_llm_status_error(RateLimitError, 429), 503, GATE_LLM_BUSY_DETAIL),
        (APIConnectionError(request=_llm_request()), 502, GATE_LLM_UNREACHABLE_DETAIL),
        (httpx.ReadTimeout("t", request=_llm_request()), 504, GATE_LLM_TIMEOUT_DETAIL),
        (_llm_status_error(AuthenticationError, 401), 401, GATE_LLM_AUTH_DETAIL),
    ],
)
def test_chunk_gate_llm_infra_error_maps_to_constant_detail(exc, status, detail):
    _chunk_gate_failing(exc)

    with pytest.raises(HTTPException) as exc_info:
        _run(mod.chunk_gate(_chunk_gate_request()))
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail


def test_constant_details_survive_500_sanitizer():
    """worker матчит эти строки в теле ответа — sanitizer не должен их искажать."""
    for detail in (
        LLM_TIMEOUT_DETAIL,
        LLM_UNREACHABLE_DETAIL,
        LLM_BUSY_DETAIL,
        LLM_AUTH_DETAIL,
        GATE_LLM_TIMEOUT_DETAIL,
        GATE_LLM_UNREACHABLE_DETAIL,
        GATE_LLM_BUSY_DETAIL,
        GATE_LLM_AUTH_DETAIL,
        GATE_LLM_ERROR_DETAIL,
        GATE_PARSE_ERROR_DETAIL,
    ):
        assert _sanitize_500_detail(detail) == detail
    assert _sanitize_500_detail("Connection refused sk-secret") == "Internal server error (connection)"


# ─── GET /health/ ─────────────────────────────────────────────────────


def test_health_check():
    result = _run(mod.health_check())
    assert result == {"status": "healthy"}


# ─── chunk_gate pure logic ────────────────────────────────────────────


def test_normalize_gate_score_variants():
    assert _normalize_gate_score(1) == [1]
    assert _normalize_gate_score(0) == [0]
    assert _normalize_gate_score(True) == [1]
    assert _normalize_gate_score(False) == [0]
    assert _normalize_gate_score("1") == [1]
    assert _normalize_gate_score("0") == [0]
    assert _normalize_gate_score([1]) == [1]
    assert _normalize_gate_score([]) == [0]
    assert _normalize_gate_score("garbage") == [0]


def test_parse_gate_response_well_formed_json():
    raw = json.dumps({
        "c1_chunk_informative": [1],
        "c1_reasoning": "ok",
        "c1_confidence": 0.9,
        "c2_chunk_reference_clarity": [1],
        "c2_reasoning": "ok",
        "c2_confidence": 0.8,
        "c3_chunk_multi_suitability": [0],
        "c3_reasoning": "ok",
        "c3_confidence": 0.7,
    })
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]
    assert parsed["c3_chunk_multi_suitability"] == [0]


def test_parse_gate_response_single_quoted():
    raw = """{'c1_chunk_informative': [1], 'c1_confidence': 0.5,
'c2_chunk_reference_clarity': [1], 'c2_confidence': 0.5,
'c3_chunk_multi_suitability': [1], 'c3_confidence': 0.5}"""
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]
    assert parsed["c2_chunk_reference_clarity"] == [1]


def test_parse_gate_response_markdown_fences():
    raw = '```json\n{"c1_chunk_informative": [1], "c2_chunk_reference_clarity": [1], "c3_chunk_multi_suitability": [1]}\n```'
    parsed = _parse_gate_response(raw)
    assert parsed["c1_chunk_informative"] == [1]


def test_parse_gate_response_missing_criterion_raises_key_error():
    raw = json.dumps({"c1_chunk_informative": [1], "c3_chunk_multi_suitability": [1]})
    with pytest.raises(KeyError):
        _parse_gate_response(raw)


def test_parse_gate_response_garbage_raises():
    with pytest.raises((json.JSONDecodeError, ValueError, TypeError)):
        _parse_gate_response("not json at all {{{")


def test_decide_pass():
    parsed = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    ok, reason = _decide(parsed, "open")
    assert ok is True
    assert reason is None


def test_decide_c1_zero():
    parsed = {
        "c1_chunk_informative": [0],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [1],
    }
    ok, reason = _decide(parsed, "open")
    assert ok is False
    assert reason == "chunk_not_informative"


def test_decide_c2_zero():
    parsed = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [0],
        "c3_chunk_multi_suitability": [1],
    }
    ok, reason = _decide(parsed, "one")
    assert ok is False
    assert reason == "chunk_not_self_contained"


def test_decide_multi_c3_zero():
    parsed = {
        "c1_chunk_informative": [1],
        "c2_chunk_reference_clarity": [1],
        "c3_chunk_multi_suitability": [0],
    }
    ok, reason = _decide(parsed, "multi")
    assert ok is False
    assert reason == "chunk_not_suitable_for_multi"


def test_synthetic_gate_pass():
    result = synthetic_gate_pass("gate_chain_not_configured")
    assert result["passed"] is True
    assert result["rejection_reason"] == "gate_chain_not_configured"
    assert result["c1_chunk_informative"] == [1]
    assert result["c2_chunk_reference_clarity"] == [1]
    assert result["c3_chunk_multi_suitability"] == [1]


# ─── POST /validate_question/ (quality revalidation) ─────────────────


def _validate_request(**kwargs):
    defaults = dict(
        question={
            "question_type": "one",
            "task": "Когда появился портал «Госуслуги»?",
            "options": {"option_1": "2009", "option_2": "2015", "option_3": "", "option_4": "2020"},
            "correct_answer": "1",
        },
        source_text="Первые пользователи увидели портал «Госуслуги» в 2009 г.",
        document_kind="textbook",
        validation_model_id="qwen",
    )
    defaults.update(kwargs)
    return mod.ValidateQuestionRequest(**defaults)


@pytest.fixture(autouse=True)
def _known_models(monkeypatch):
    """/validate_question/ rejects ids missing from the registry; "qwen" is known here."""
    registry = MagicMock()
    registry.get_model.side_effect = lambda mid: object() if mid in ("qwen", "gemma") else None
    monkeypatch.setattr(mod, "registry", registry)


def _validation_handler(chain):
    handler = MagicMock()
    handler.validation_chain_for.return_value = chain
    mod.handler = handler
    return handler


def test_validate_question_runs_only_the_validator_with_generator_shape():
    output = MagicMock()
    output.model_dump.return_value = {"passed": True, "total": 19.0, "max_total": 20.5, "raw": {"c1": "..."}}
    chain = MagicMock()
    chain.invoke.return_value = output
    handler = _validation_handler(chain)

    result = _run(mod.validate_question(_validate_request()))

    assert result.status == "success"
    assert result.result == {"passed": True, "total": 19.0, "max_total": 20.5}  # raw dropped
    handler.validation_chain_for.assert_called_once_with("qwen")
    payload = chain.invoke.call_args[0][0]
    assert payload["question_type"] == "one"
    assert payload["document_kind"] == "textbook"
    assert payload["question"] == {
        "task": "Когда появился портал «Госуслуги»?",
        "option_1": "2009",
        "option_2": "2015",
        "option_4": "2020",
        "outputs": "1",
    }


def test_validate_question_unknown_model_is_rejected_not_swapped_for_default():
    chain = MagicMock()
    handler = _validation_handler(chain)
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.validate_question(_validate_request(validation_model_id="llamacpp-old-id")))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "unknown_validation_model: llamacpp-old-id"
    handler.validation_chain_for.assert_not_called()


def test_validate_question_without_model_uses_default_chain():
    output = MagicMock()
    output.model_dump.return_value = {"passed": False}
    chain = MagicMock()
    chain.invoke.return_value = output
    handler = _validation_handler(chain)
    _run(mod.validate_question(_validate_request(validation_model_id=None)))
    handler.validation_chain_for.assert_called_once_with(None)


def test_validate_question_rejects_unusable_question_without_llm_call():
    chain = MagicMock()
    _validation_handler(chain)
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.validate_question(_validate_request(question={"question_type": "weird", "task": "x"})))
    assert exc_info.value.status_code == 422
    chain.invoke.assert_not_called()


def test_validate_question_infra_error_is_constant_detail():
    chain = MagicMock()
    chain.invoke.side_effect = _llm_status_error(InternalServerError, 503)
    _validation_handler(chain)
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.validate_question(_validate_request()))
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == LLM_BUSY_DETAIL


def test_validate_question_other_error_is_validation_llm_error():
    chain = MagicMock()
    chain.invoke.side_effect = RuntimeError("boom")
    _validation_handler(chain)
    with pytest.raises(HTTPException) as exc_info:
        _run(mod.validate_question(_validate_request()))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "validation llm error"


def test_stored_to_validator_question_shapes():
    from agent.nodes.llm_validator.stored_question import stored_to_validator_question

    assert stored_to_validator_question(
        {"question_type": "open", "task": "Что такое МФЦ?", "correct_answer": "Центр услуг"}
    ) == {"task": "Что такое МФЦ?", "outputs": "Центр услуг"}
    assert stored_to_validator_question(
        {"question_type": "multi", "task": "Т?", "options": '{"option_1": "a", "option_2": "b"}', "correct_answer": "1,2"}
    ) == {"task": "Т?", "option_1": "a", "option_2": "b", "outputs": "1,2"}
    with pytest.raises(ValueError):
        stored_to_validator_question({"question_type": "one", "task": "Т?", "options": {"option_1": "a"}})
    with pytest.raises(ValueError):
        stored_to_validator_question({"question_type": "open", "task": "  "})
