"""Assessment/refine chains: transient LLM errors propagate, the rest is marked.

Before this, any failure inside the provocativeness/difficulty chains — a 503
"busy", a timeout, garbage JSON — was swallowed and replaced by a fail-closed
number (3 / 1) that the worker then stored as a genuine assessment. Refine
behaved the same way: it silently returned the original question and the graph
re-assessed it for nothing.
"""

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

pytest.importorskip("langchain_core")
openai = pytest.importorskip("openai")

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

from agent.nodes.assess_difficulty import estimation as difficulty_mod  # noqa: E402
from agent.nodes.assess_sensitivity import estimation as sensitivity_mod  # noqa: E402
from agent.nodes.refine_question import refine_question as refine_mod  # noqa: E402

_REQUEST = httpx.Request("POST", "http://llm.test/v1/chat/completions")

QUESTION = {
    "task": "Какой орган принимает закон?",
    "text": None,
    "option_1": "Парламент",
    "option_2": "Суд",
    "option_3": "Мэрия",
    "option_4": "Банк",
    "outputs": 1,
}


def _raising_llm(exc: BaseException):
    def _raise(_prompt_value):
        raise exc

    return RunnableLambda(_raise)


def _answering_llm(text: str):
    return RunnableLambda(lambda _prompt_value: AIMessage(content=text))


def _rate_limit() -> BaseException:
    return openai.RateLimitError(
        "busy", response=httpx.Response(429, request=_REQUEST), body=None
    )


def _timeout() -> BaseException:
    return openai.APITimeoutError(request=_REQUEST)


def _patch_llm(monkeypatch, module, llm):
    monkeypatch.setattr(module, "create_chat_llm", lambda *a, **kw: llm)


CHAINS = [
    pytest.param(
        sensitivity_mod, "create_provocativeness_chain", "provocativeness_score", id="sensitivity"
    ),
    pytest.param(difficulty_mod, "create_difficulty_chain", "difficulty", id="difficulty"),
]


@pytest.mark.parametrize("module,factory,score_field", CHAINS)
@pytest.mark.parametrize("make_exc", [_rate_limit, _timeout], ids=["rate_limit", "timeout"])
def test_assessment_transient_error_propagates(monkeypatch, module, factory, score_field, make_exc):
    exc = make_exc()
    _patch_llm(monkeypatch, module, _raising_llm(exc))
    chain = getattr(module, factory)()

    with pytest.raises(type(exc)):
        chain.invoke({"generated_question": QUESTION, "source_text": "src"})


@pytest.mark.parametrize("module,factory,score_field", CHAINS)
def test_assessment_non_transient_error_is_marked_not_faked(monkeypatch, module, factory, score_field):
    _patch_llm(monkeypatch, module, _raising_llm(ValueError("model returned nothing")))
    chain = getattr(module, factory)()

    result = chain.invoke({"generated_question": QUESTION, "source_text": "src"})

    assert result.assessment_failed is True
    assert getattr(result, score_field) is None
    assert result.explanation is None
    assert "model returned nothing" in result.assessment_error


@pytest.mark.parametrize("module,factory,score_field", CHAINS)
@pytest.mark.parametrize(
    "raw",
    ["this is not json at all", '{"explanation": "no score here"}', '{"SCORE": 0}'],
    ids=["garbage", "missing_score", "zero_score"],
)
def test_assessment_unparseable_response_is_marked(monkeypatch, module, factory, score_field, raw):
    _patch_llm(monkeypatch, module, _answering_llm(raw.replace("SCORE", score_field)))
    chain = getattr(module, factory)()

    result = chain.invoke({"generated_question": QUESTION, "source_text": "src"})

    assert result.assessment_failed is True
    assert getattr(result, score_field) is None


@pytest.mark.parametrize("module,factory,score_field", CHAINS)
def test_assessment_success_has_no_failure_marker(monkeypatch, module, factory, score_field):
    _patch_llm(
        monkeypatch, module, _answering_llm('{"%s": 2, "explanation": "ok"}' % score_field)
    )
    chain = getattr(module, factory)()

    result = chain.invoke({"generated_question": QUESTION, "source_text": "src"})

    assert result.assessment_failed is False
    assert result.assessment_error is None
    assert getattr(result, score_field) == 2
    assert result.explanation == "ok"


@pytest.mark.parametrize("module,factory,score_field", CHAINS)
def test_assessment_invalid_question_payload_is_marked(monkeypatch, module, factory, score_field):
    calls = []
    _patch_llm(monkeypatch, module, RunnableLambda(lambda v: calls.append(v)))
    chain = getattr(module, factory)()

    result = chain.invoke({"generated_question": {"not_a_task": 1}, "source_text": "src"})

    assert calls == []
    assert result.assessment_failed is True
    assert getattr(result, score_field) is None


# --- refine ---------------------------------------------------------------

REFINE_INPUT = {
    "question_type": "one",
    "source_text": "Закон принимает парламент.",
    "question": QUESTION,
    "validation_result": {
        "by_block": {"clarity": [0, 1]},
        "justifications": {"clarity": ["непонятно", ""]},
        "total": 3,
        "max_total": 10,
    },
    "language": "ru",
    "document_kind": "normative",
}


def _question_core(q: dict) -> dict:
    return {k: q.get(k) for k in ("task", "option_1", "option_2", "option_3", "option_4")}


@pytest.mark.parametrize("make_exc", [_rate_limit, _timeout], ids=["rate_limit", "timeout"])
def test_refine_transient_error_propagates(monkeypatch, make_exc):
    exc = make_exc()
    _patch_llm(monkeypatch, refine_mod, _raising_llm(exc))
    chain = refine_mod.create_refine_question_chain()

    with pytest.raises(type(exc)):
        chain.invoke(REFINE_INPUT)


@pytest.mark.parametrize(
    "llm",
    [_raising_llm(ValueError("bad request")), _answering_llm("not a question json")],
    ids=["non_transient_error", "unparseable"],
)
def test_refine_failure_keeps_question_and_marks_it(monkeypatch, llm):
    _patch_llm(monkeypatch, refine_mod, llm)
    chain = refine_mod.create_refine_question_chain()

    out = chain.invoke(REFINE_INPUT)

    assert out["refine_failed"] is True
    assert out["refine_error"]
    assert _question_core(out) == _question_core(QUESTION)
    assert str(out["outputs"]) == str(QUESTION["outputs"])
    assert out["source_text"] == REFINE_INPUT["source_text"]


def test_refine_success_has_no_failure_marker(monkeypatch):
    refined = dict(QUESTION, task="Какой орган принимает федеральный закон?")
    import json

    _patch_llm(monkeypatch, refine_mod, _answering_llm(json.dumps(refined, ensure_ascii=False)))
    chain = refine_mod.create_refine_question_chain()

    out = chain.invoke(REFINE_INPUT)

    assert "refine_failed" not in out
    assert out["task"] == refined["task"]
