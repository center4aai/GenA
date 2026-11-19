"""Stored question (dataset_api row) → the shape the validator was written for.

The validator scores generator output: ``{task, option_1..option_9, outputs}``.
Questions in the database keep ``options`` as a dict (or its JSON string) and
the answer in ``correct_answer``; revalidation has to hand the validator the
same shape the generation pipeline did.
"""

from __future__ import annotations

import json
from typing import Any, Dict

QUESTION_TYPES = frozenset({"open", "one", "multi"})


def _options_dict(options: Any) -> Dict[str, str]:
    if isinstance(options, dict):
        return {str(k): str(v) for k, v in options.items() if v is not None and str(v).strip()}
    if isinstance(options, str) and options.strip():
        try:
            parsed = json.loads(options)
        except (ValueError, TypeError):
            return {}
        return _options_dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def stored_to_validator_question(question: Dict[str, Any]) -> Dict[str, Any]:
    """Validator input from a stored question. Raises ValueError if unusable."""
    qtype = str(question.get("question_type") or "").strip().lower()
    if qtype not in QUESTION_TYPES:
        raise ValueError(f"unsupported question_type {qtype!r}")
    task = str(question.get("task") or "").strip()
    if not task:
        raise ValueError("question has no task text")
    out: Dict[str, Any] = {"task": task}
    if qtype != "open":
        options = _options_dict(question.get("options"))
        if len(options) < 2:
            raise ValueError("choice question needs at least two options")
        for key, value in options.items():
            if key.startswith("option_"):
                out[key] = value
    answer = question.get("correct_answer")
    out["outputs"] = "" if answer is None else str(answer)
    return out
