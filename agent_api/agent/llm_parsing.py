"""Shared helpers for parsing LLM responses with markdown fences and safe fallbacks."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Dict, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def strip_markdown_fences(text: str) -> str:
    """Remove optional ```json fences from an LLM response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _loads_lenient(cleaned: str) -> Dict[str, Any]:
    candidates = [cleaned]
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    if start >= 0:
        try:
            obj, _end = decoder.raw_decode(cleaned[start:])
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
                return obj[0]
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match and match.group(0) != cleaned:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
                return obj[0]
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        try:
            obj = ast.literal_eval(candidate)
            if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
                return obj[0]
            if isinstance(obj, dict):
                return obj
        except (ValueError, SyntaxError):
            pass

    raise ValueError("Could not parse JSON object from LLM response")


def _coerce_question_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common LLM omissions before question schema validation."""
    if not isinstance(data, dict):
        return data
    if "task" not in data:
        for wrap in ("question", "data", "result"):
            inner = data.get(wrap)
            if isinstance(inner, dict) and "task" in inner:
                data = dict(inner)
                break
    if "outputs" not in data or data.get("outputs") in (None, ""):
        for alias in ("correct_answer", "answer", "correct"):
            if data.get(alias) not in (None, ""):
                data["outputs"] = data[alias]
                break
    outputs = data.get("outputs")
    if isinstance(outputs, list):
        data["outputs"] = ",".join(str(x) for x in outputs)
    elif isinstance(outputs, float) and outputs.is_integer():
        data["outputs"] = int(outputs)
    if "task" in data and data.get("outputs") is None:
        data["outputs"] = ""
    return data


def _salvage_truncated_object(cleaned: str) -> Dict[str, Any]:
    """Complete key/value pairs of a top-level JSON object cut off mid-way.

    A model that runs into max_tokens leaves the object unterminated; every
    pair before the cut is still valid JSON. Reads pairs in order and stops at
    the first incomplete one.
    """
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    if start < 0:
        return {}
    pos, out = start + 1, {}
    ws = re.compile(r"[\s,]*")
    while True:
        pos = ws.match(cleaned, pos).end()
        if pos >= len(cleaned) or cleaned[pos] == "}":
            return out
        try:
            key, pos = decoder.raw_decode(cleaned, pos)
            pos = re.compile(r"\s*:\s*").match(cleaned, pos).end()
            value, pos = decoder.raw_decode(cleaned, pos)
        except (json.JSONDecodeError, AttributeError):
            return out
        if not isinstance(key, str):
            return out
        out[key] = value


def parse_pydantic_output(text: str, model: Type[T]) -> T:
    """Parse LLM text into a Pydantic model, tolerating markdown fences.

    Last resort: an object truncated by max_tokens, accepted only when every
    required field of ``model`` came through complete (a question cut before
    ``outputs`` stays a parse error instead of getting an empty answer).
    """
    cleaned = strip_markdown_fences(text)
    try:
        return model.model_validate_json(cleaned)
    except (ValidationError, ValueError):
        try:
            data = _loads_lenient(cleaned)
        except ValueError:
            data = _salvage_truncated_object(cleaned)
            required = [n for n, f in model.model_fields.items() if f.is_required()]
            if not data or any(n not in data for n in required):
                raise
            logger.warning(
                "Recovered %s from a truncated LLM response (%d fields kept)",
                model.__name__, len(data),
            )
        data = _coerce_question_dict(data)
        return model.model_validate(data)


def parse_json_model(text: str, model: Type[T], fallback: T) -> T:
    """Parse LLM text into a Pydantic model; return fallback on failure."""
    try:
        return parse_pydantic_output(text, model)
    except Exception as exc:
        logger.warning("Failed to parse LLM JSON for %s: %s", model.__name__, exc)
        return fallback


_EXPLANATION_ALIASES = ("explanation", "reason", "rationale", "justification")


def _coerce_score_candidate(value: Any) -> int | None:
    """Return a 1-3 score, or None when the value is missing or unusable (e.g. 0)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 3:
        return 3
    return n


def normalize_score_1_3(value: Any, default: int = 1) -> int:
    """Map LLM score values onto the 1-3 scale."""
    score = _coerce_score_candidate(value)
    return score if score is not None else default


def resolve_score_1_3(
    data: Dict[str, Any],
    score_field: str,
    score_aliases: tuple[str, ...],
    default: int,
) -> int:
    """Pick the first valid 1-3 score from the primary field or aliases."""
    for key in (score_field, *score_aliases):
        if key not in data:
            continue
        score = _coerce_score_candidate(data[key])
        if score is not None:
            return score
    return default


def resolve_explanation(data: Dict[str, Any], fallback: str) -> str:
    """Extract explanation text, accepting common LLM field aliases."""
    for key in _EXPLANATION_ALIASES:
        val = data.get(key)
        if val is not None:
            text = str(val).strip()
            if text:
                return text
    return fallback


def parse_assessment_model(
    text: str,
    model: Type[T],
    score_field: str,
    fallback: T,
    score_aliases: tuple[str, ...] = (),
) -> T:
    """Parse assessment JSON with score aliases, 1-3 normalization, and explanation default."""
    try:
        data = _loads_lenient(strip_markdown_fences(text))
        score = resolve_score_1_3(
            data, score_field, score_aliases, getattr(fallback, score_field)
        )
        explanation = resolve_explanation(data, fallback.explanation)
        return model.model_validate({score_field: score, "explanation": explanation})
    except Exception as exc:
        logger.warning("Failed to parse assessment JSON for %s: %s", model.__name__, exc)
        return fallback


def safe_default_question(question_type: str, input_text: str) -> Dict[str, Any]:
    """Minimal question dict used when generation/refine parsing fails.

    Do not copy the source chunk into ``task`` — that would look like a
    successful question and leak source-text references.
    """
    qtype = (question_type or "one").lower().strip()
    base: Dict[str, Any] = {
        "task": "",
        "text": None,
        "source_text": input_text,
        "generation_parse_failed": True,
    }
    if qtype == "open":
        base["outputs"] = ""
        for i in range(1, 10):
            base[f"option_{i}"] = None
    elif qtype == "one":
        for i in range(1, 5):
            base[f"option_{i}"] = f"Вариант {i}"
        for i in range(5, 10):
            base[f"option_{i}"] = None
        base["outputs"] = 1
    elif qtype == "multi":
        for i in range(1, 7):
            base[f"option_{i}"] = f"Вариант {i}"
        for i in range(7, 10):
            base[f"option_{i}"] = None
        base["outputs"] = "1,2"
    else:
        for i in range(1, 5):
            base[f"option_{i}"] = f"Вариант {i}"
        for i in range(5, 10):
            base[f"option_{i}"] = None
        base["outputs"] = 1
    return base
