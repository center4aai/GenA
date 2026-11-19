"""Normalize LLM question output to a consistent shape per question type."""

from __future__ import annotations

from typing import Any, Dict, List, Union


_EMPTY_VALUES = frozenset({None, "", "None", "null", "NULL"})


def _to_dict(question: Any) -> Dict[str, Any]:
    if isinstance(question, dict):
        return dict(question)
    if hasattr(question, "model_dump"):
        return question.model_dump()
    if hasattr(question, "dict"):
        return question.dict()
    return dict(question)


def _clean_optional_str(value: Any) -> Any:
    if isinstance(value, str) and value.strip() in _EMPTY_VALUES:
        return None
    return value


def _normalize_index_part(value: Any) -> str:
    """Format a single answer index for one/multi outputs."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return ""
    text = str(value).strip()
    if text.isdigit():
        return str(int(text))
    try:
        number = float(text)
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return text


def _split_digit_indices(text: str) -> List[str]:
    """Split concatenated index digits like '13' into ['1', '3']."""
    if len(text) <= 1 or not text.isdigit():
        return []
    if all(char in "123456789" for char in text):
        return list(text)
    return []


def _split_output_parts(outputs: Any, split_digits: bool = True) -> List[str]:
    """Split LLM outputs into index parts for one/multi question types."""
    if isinstance(outputs, bool):
        return []
    if isinstance(outputs, (list, tuple)):
        parts = []
        for item in outputs:
            part = _normalize_index_part(item)
            if part:
                parts.append(part)
        return parts
    if isinstance(outputs, (int, float)):
        if isinstance(outputs, float) and not outputs.is_integer():
            return []
        number = int(outputs)
        if split_digits and number > 9:
            digits = _split_digit_indices(str(number))
            if digits:
                return digits
        return [str(number)]
    text = str(outputs).strip()
    if not text:
        return []
    normalized = text.replace(";", ",")
    if "," not in normalized:
        if split_digits:
            digit_parts = _split_digit_indices(normalized)
            if digit_parts:
                return digit_parts
        part = _normalize_index_part(normalized)
        return [part] if part else []
    parts = []
    for raw in normalized.split(","):
        part = _normalize_index_part(raw)
        if part:
            parts.append(part)
    return parts


def _coerce_one_index(parts: List[str]) -> Union[int, str]:
    if not parts:
        return ""
    first = parts[0]
    if first.isdigit():
        return int(first)
    try:
        return int(float(first))
    except (TypeError, ValueError):
        return first


def _coerce_multi_indices(parts: List[str]) -> str:
    return ",".join(parts)


def normalize_question_output(question: Any, question_type: str) -> Dict[str, Any]:
    """Normalize parsed LLM output for one/multi/open question types."""
    q = _to_dict(question)
    qtype = (question_type or "one").lower().strip()

    for key in list(q.keys()):
        if key.startswith("option_") or key == "text":
            q[key] = _clean_optional_str(q.get(key))

    if qtype == "open":
        for i in range(1, 10):
            q[f"option_{i}"] = None
        outputs = q.get("outputs", "")
        q["outputs"] = str(outputs) if outputs is not None else ""
        return q

    parts = _split_output_parts(q.get("outputs", ""), split_digits=(qtype == "multi"))
    if qtype == "one":
        q["outputs"] = _coerce_one_index(parts)
    elif qtype == "multi":
        q["outputs"] = _coerce_multi_indices(parts)
    else:
        q["outputs"] = ",".join(parts)

    # Move the correct option into the kept range before truncating. Dropping
    # options first produced questions whose `outputs` pointed at a removed
    # option; it was then blanked, shuffling silently no-opped on the empty
    # value, and the worker stored a question with no correct answer at all.
    if qtype in ("one", "multi"):
        keep_upto = 4 if qtype == "one" else 6
        _promote_correct_options(q, keep_upto)

    if qtype == "one":
        for i in range(5, 10):
            q[f"option_{i}"] = None
    elif qtype == "multi":
        for i in range(7, 10):
            q[f"option_{i}"] = None

    valid = {
        str(i)
        for i in range(1, 10)
        if q.get(f"option_{i}") not in _EMPTY_VALUES and q.get(f"option_{i}") is not None
    }
    if qtype == "one":
        out = str(q.get("outputs") or "")
        if out and out not in valid:
            q["outputs"] = ""
    elif qtype == "multi":
        kept = [p for p in str(q.get("outputs") or "").split(",") if p and p in valid]
        q["outputs"] = ",".join(kept)

    return q


def _promote_correct_options(q: dict, keep_upto: int) -> None:
    """Swap correct options that sit past ``keep_upto`` into the kept range.

    Models occasionally answer with more options than the type allows and put
    the correct one last. Swapping keeps the question answerable instead of
    discarding the answer along with the surplus options.
    """
    correct = [p.strip() for p in str(q.get("outputs") or "").split(",") if p.strip()]
    if not correct:
        return

    free = [
        i
        for i in range(1, keep_upto + 1)
        if str(i) not in correct
        and q.get(f"option_{i}") not in _EMPTY_VALUES
        and q.get(f"option_{i}") is not None
    ]
    remapped = []
    for idx in correct:
        if idx.isdigit() and int(idx) > keep_upto and free:
            target = free.pop(0)
            q[f"option_{idx}"], q[f"option_{target}"] = (
                q.get(f"option_{target}"),
                q.get(f"option_{idx}"),
            )
            remapped.append(str(target))
        else:
            remapped.append(idx)
    q["outputs"] = ",".join(remapped)
