"""Контракт с моделью: промпт окна, разбор и валидация JSON с сегментами."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .blocks import Segment, normalize_type
from .windows import Window

PROMPT_VERSION = "2"
_PROMPT_PATH = Path(__file__).parent / "prompt_freeform_segmentation.txt"

# Заголовок не бывает длиннее.
HEADING_MAX_CHARS = 200
# Окно, где сброшено больше этой доли символов, помечаем как подозрительное.
SUSPICIOUS_DROP_SHARE = 0.7

_DROP_TYPES = {"junk", "exercise", "toc", "imprint", "abbreviations", "legend", "intro", "table"}


class ContractError(ValueError):
    """Ответ модели не разбирается или пуст."""


def system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def render_window_prompt(window: Window) -> str:
    lines: List[str] = []
    if window.context:
        lines.append("[КОНТЕКСТ — предыдущие абзацы, НЕ размечать]")
        for p in window.context:
            lines.append(f"(к) {p.text[:400]}")
        lines.append("")
    lines.append(f"[АБЗАЦЫ ДЛЯ РАЗМЕТКИ: {window.first_idx}–{window.last_idx}]")
    prev_page = window.context[-1].page if window.context else None
    for p in window.paras:
        hints = [p.hint] if p.hint else []
        if p.page and prev_page and p.page != prev_page:
            hints.append(f"стр. {p.page}")
        prev_page = p.page or prev_page
        hint = f"|{'; '.join(hints)}" if hints else ""
        lines.append(f"[{p.idx}{hint}] {p.text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Разбор JSON (устойчивый к типичным поломкам локальных моделей)
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)
_MISSING_COMMA_RE = re.compile(r"(\}|\]|\"|\d|true|false|null)\s*\n\s*(?=[\{\[\"])")
_TRAILING_COMMA_RE = re.compile(r",\s*([\]\}])")


def _cleanup(raw: str) -> str:
    text = _THINK_RE.sub("", raw or "")
    text = _FENCE_RE.sub("", text.strip())
    start = text.find("{")
    if start > 0:
        text = text[start:]
    return text.strip()


def _salvage_segments(text: str) -> Tuple[List[dict], bool]:
    """Собрать целые объекты из обрезанного массива "segments": [ … ."""
    m = re.search(r'"segments"\s*:\s*\[', text)
    if not m:
        return [], False
    pos = m.end()
    decoder = json.JSONDecoder()
    objs: List[dict] = []
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            break
        try:
            obj, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            objs.append(obj)
        pos = end
    return objs, True


def parse_segments_response(raw: str) -> Tuple[Dict[str, Any], bool]:
    """→ (данные, truncated). Данные: {"continues_previous": bool, "segments": [...]}."""
    text = _cleanup(raw)
    if not text:
        raise ContractError("empty response")
    candidates = [text, _TRAILING_COMMA_RE.sub(r"\1", _MISSING_COMMA_RE.sub(r"\1,\n", text))]
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("segments"), list):
            return data, False
        if isinstance(data, list):
            return {"segments": data}, False
    try:
        import ast

        data = ast.literal_eval(text)
        if isinstance(data, dict) and isinstance(data.get("segments"), list):
            return data, False
    except (ValueError, SyntaxError):
        pass
    objs, salvaged = _salvage_segments(text)
    if salvaged and objs:
        cont = bool(re.search(r'"continues_previous"\s*:\s*true', text))
        return {"continues_previous": cont, "segments": objs}, True
    raise ContractError(f"unparseable segmentation response: {text[:200]!r}")


# ---------------------------------------------------------------------------
# Валидация
# ---------------------------------------------------------------------------

def _to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def validate_segments(objs: List[dict], first_idx: int, last_idx: int, window_index: int,
                      paragraphs_text: Optional[Dict[int, str]] = None) -> Tuple[List[Segment], Dict[str, Any]]:
    """Сегменты окна: нормализация, обрезка, сортировка, заполнение дыр.

    Возвращает (segments, info) где info = {"gapfill": n, "suspicious": bool}.
    """
    segs: List[Segment] = []
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        start, end = _to_int(obj.get("start")), _to_int(obj.get("end"))
        if start is None and end is None:
            continue
        if start is None:
            start = end
        if end is None:
            end = start
        if start > end:
            start, end = end, start
        if end < first_idx or start > last_idx:
            continue
        start, end = max(start, first_idx), min(end, last_idx)
        seg_type = normalize_type(obj.get("type"))
        title = str(obj.get("title") or "").strip()[:200]
        level = _to_int(obj.get("level"))
        if seg_type == "heading":
            text_len = sum(len(paragraphs_text.get(i, "")) for i in range(start, end + 1)) if paragraphs_text else 0
            if end - start > 0 or text_len > HEADING_MAX_CHARS:
                seg_type = "body"
        segs.append(Segment(start, end, seg_type, title, level, "llm", window_index))
    segs.sort(key=lambda s: (s.start, s.end))
    cleaned: List[Segment] = []
    for s in segs:
        if cleaned and s.start <= cleaned[-1].end:
            s.start = cleaned[-1].end + 1
            if s.start > s.end:
                continue
        cleaned.append(s)
    if not cleaned:
        raise ContractError("no segments in window")
    filled: List[Segment] = []
    cursor = first_idx
    gapfill = 0
    for s in cleaned:
        if s.start > cursor:
            title = filled[-1].title if filled and filled[-1].type == "body" else ""
            filled.append(Segment(cursor, s.start - 1, "body", title, None, "gapfill", window_index))
            gapfill += 1
        filled.append(s)
        cursor = s.end + 1
    if cursor <= last_idx:
        filled.append(Segment(cursor, last_idx, "body", "", None, "gapfill", window_index))
        gapfill += 1
    suspicious = False
    if paragraphs_text:
        total = sum(len(paragraphs_text.get(i, "")) for i in range(first_idx, last_idx + 1)) or 1
        dropped = sum(
            len(paragraphs_text.get(i, ""))
            for s in filled if s.type in _DROP_TYPES for i in range(s.start, s.end + 1)
        )
        suspicious = dropped / total >= SUSPICIOUS_DROP_SHARE
    return filled, {"gapfill": gapfill, "suspicious": suspicious}
