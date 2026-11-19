"""parse_pydantic_output: a response cut off by max_tokens (Gemma echoes the
whole chunk into source_text, which comes after outputs)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from import_paths import prefer_agent_api  # noqa: E402

prefer_agent_api()

from agent.llm_parsing import parse_pydantic_output  # noqa: E402
from agent.schemas.question import StructuredQuestionOutput  # noqa: E402

HEAD = (
    '```json\n{\n  "task": "Что представляет собой конфискация имущества?",\n  "text": null,\n'
    '  "option_1": "Добровольная передача имущества",\n'
    '  "option_2": "Принудительное безвозмездное изъятие",\n'
)


def test_truncated_after_outputs_is_recovered():
    raw = HEAD + '  "option_3": null,\n  "outputs": "2",\n  "source_text": "Глава 15.1. Конфискация имущества\\n\\n1. Конфискация есть [а)](http://ivo'
    q = parse_pydantic_output(raw, StructuredQuestionOutput)
    assert q.task.startswith("Что представляет")
    assert (q.option_1, q.option_2, q.outputs) == (
        "Добровольная передача имущества", "Принудительное безвозмездное изъятие", "2",
    )
    assert q.source_text is None  # the cut field is dropped, the caller sets it


def test_truncated_before_outputs_stays_an_error():
    raw = HEAD + '  "option_3": "Изъятие в пользу потерпев'
    with pytest.raises(ValueError):
        parse_pydantic_output(raw, StructuredQuestionOutput)


def test_truncated_inside_options_without_outputs_is_an_error():
    with pytest.raises(ValueError):
        parse_pydantic_output(HEAD, StructuredQuestionOutput)


def test_complete_response_unchanged():
    raw = HEAD + '  "outputs": 2\n}\n```'
    q = parse_pydantic_output(raw, StructuredQuestionOutput)
    assert q.outputs == 2 and q.option_2 == "Принудительное безвозмездное изъятие"
