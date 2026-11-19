"""Сегменты всех окон → дерево для create_llm_chunks + границы body для упаковщика."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .blocks import KEEP_POLICY, Paragraph, Segment
from .windows import Window

# Подпись к иллюстрации в одно предложение — не чанк (как caption_short в layout-пути).
CAPTION_MIN_SENTENCES = 2
# Врезка короче — подпись без факта, не чанк (как inset_short в layout-пути).
INSET_MIN_CHARS_DEFAULT = 80


def _inset_min_chars() -> int:
    raw = os.getenv("CHUNKER_LLM_SEG_INSET_MIN_CHARS")
    try:
        return int(raw) if raw and raw.strip() else INSET_MIN_CHARS_DEFAULT
    except ValueError:
        return INSET_MIN_CHARS_DEFAULT


def _topic_key(title: str) -> str:
    return re.sub(r"[^\w]+", " ", (title or "").casefold()).strip()

_CHAPTER_RE = re.compile(r'^(?:Глава|ГЛАВА|Раздел|РАЗДЕЛ|Часть|ЧАСТЬ)\s+', re.IGNORECASE)
_SECTION_RE = re.compile(r'^(?:\d{1,3})?§\s*\d+')


def label_window_fallback(window: Window) -> List[Segment]:
    """Без модели: заголовки по regex/КАПС, остальное — один body."""
    segs: List[Segment] = []
    body_start: Optional[int] = None

    def _close(end: int) -> None:
        nonlocal body_start
        if body_start is not None:
            segs.append(Segment(body_start, end, "body", "", None, "fallback", window.index))
            body_start = None

    for p in window.paras:
        t = p.text.strip()
        letters = [c for c in t if c.isalpha()]
        is_heading = len(t) <= 120 and not t.endswith(('.', '!', '?')) and (
            _CHAPTER_RE.match(t) or _SECTION_RE.match(t) or (len(letters) >= 4 and all(c.isupper() for c in letters))
        )
        if is_heading:
            _close(p.idx - 1)
            level = 1 if _CHAPTER_RE.match(t) else 2 if _SECTION_RE.match(t) else 3
            segs.append(Segment(p.idx, p.idx, "heading", t, level, "fallback", window.index))
        elif body_start is None:
            body_start = p.idx
    _close(window.last_idx)
    return segs


def _heading_level(seg: Segment) -> int:
    t = seg.title or ""
    if _CHAPTER_RE.match(t):
        return 1
    if _SECTION_RE.match(t):
        return 2
    return seg.level if seg.level in (1, 2, 3) else 3


def segments_to_tree(paragraphs: List[Paragraph], segments: Iterable[Segment], stats: Optional[Dict[str, Any]] = None
                     ) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, str], List[str]]:
    """→ (дерево, {абзац-граница body: подтема}, {первый абзац любого body: подтема}, заголовки для identify_document_type).

    Граница чанка перед body-сегментом ставится только когда сегмент
    действительно начинает новую мысль: размечен моделью (не фолбэк и не
    gapfill), не продолжает предыдущее окно, стоит на границе предложений и,
    если между ним и предыдущим body были только врезки/подписи/задания,
    его название подтемы отличается. Нумерованный подзаголовок в начале абзаца
    (``Paragraph.runin``) всегда открывает узел 3 уровня.
    """
    from text_utils import caption_sentence_count, ends_like_sentence

    by_idx = {p.idx: p for p in paragraphs}
    root: Dict[str, Any] = {"level": 0, "title": "", "content": "", "children": []}
    stack: List[Dict[str, Any]] = [root]
    boundaries: Dict[str, str] = {}
    topics: Dict[str, str] = {}
    titles: List[str] = []
    dropped = stats.setdefault("dropped_blocks", {}) if stats is not None else {}
    inset_min = _inset_min_chars()

    def _drop(kind: str, chars: int) -> None:
        entry = dropped.setdefault(kind, {"count": 0, "chars": 0})
        entry["count"] += 1
        entry["chars"] += chars

    def _paras(seg: Segment) -> List[Paragraph]:
        return [by_idx[i] for i in range(seg.start, seg.end + 1) if i in by_idx]

    def _pages(ps: List[Paragraph]) -> Tuple[Optional[int], Optional[int]]:
        pages = [p.page for p in ps if p.page]
        return (min(pages), max(pages)) if pages else (None, None)

    def _append(node: Dict[str, Any], ps: List[Paragraph]) -> None:
        text = "\n\n".join(p.text for p in ps)
        node["content"] = f"{node['content']}\n\n{text}" if node.get("content") else text
        ps_, pe_ = _pages(ps)
        if ps_:
            node.setdefault("page_start", ps_)
            node["page_end"] = pe_
        for p in ps:
            node.setdefault("para_pages", []).append(
                {"head": re.sub(r"\s+", "", p.text)[:40], "page_start": p.page, "page_end": p.page}
            )

    def _open_heading(title: str, level: int, ps: Optional[List[Paragraph]] = None) -> Dict[str, Any]:
        while len(stack) > 1 and stack[-1]["level"] >= level:
            stack.pop()
        node = {"level": level, "title": title, "content": "", "children": []}
        if ps:
            p0, p1 = _pages(ps)
            if p0:
                node["page_start"], node["page_end"] = p0, p1
        stack[-1]["children"].append(node)
        stack.append(node)
        return node

    prev_body: Optional[Segment] = None
    aux_between = False   # между предыдущим body и текущим были только врезки/подписи/задания
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        ps = _paras(seg)
        text = "\n\n".join(p.text for p in ps)
        if not text:
            continue
        if seg.type == "heading":
            level = _heading_level(seg)
            title = (seg.title or text).strip()[:200]
            titles.append(title)
            _open_heading(title, level, ps)
            prev_body = None
            aux_between = False
            continue
        if not KEEP_POLICY.get(seg.type, False):
            _drop("front_matter" if seg.type in ("toc", "imprint", "abbreviations", "legend", "intro") else seg.type, len(text))
            aux_between = True
            continue
        if seg.type == "caption":
            if caption_sentence_count(text) < CAPTION_MIN_SENTENCES:
                _drop("caption_short", len(text))
                aux_between = True
                continue
        if seg.type == "inset" and len(text) < inset_min:
            _drop("inset_short", len(text))
            aux_between = True
            continue
        if seg.type in ("body", "list"):
            # Граница чанка на первом абзаце сегмента — по политике выше.
            first = ps[0]
            if seg.source == "llm" and seg.title:
                topics.setdefault(first.text, seg.title.strip()[:200])
            if prev_body is not None and not first.runin:
                boundary = seg.source == "llm" and not seg.continues
                prev_last = by_idx.get(prev_body.end)
                if boundary and (first.text[:1].islower() or (prev_last is not None and not ends_like_sentence(prev_last.text))):
                    boundary = False
                if boundary and aux_between and _topic_key(prev_body.title) == _topic_key(seg.title):
                    boundary = False
                if boundary:
                    boundaries[first.text] = (seg.title or "").strip()[:200]
            # Абзацы с подзаголовком открывают узел 3 уровня; остальные — в текущий узел.
            run: List[Paragraph] = []
            for p in ps:
                if p.runin:
                    if run:
                        _append(stack[-1], run)
                        run = []
                    titles.append(p.runin)
                    _open_heading(p.runin, 3, [p])
                run.append(p)
            if run:
                _append(stack[-1], run)
            prev_body = seg
            aux_between = False
            continue
        node = stack[-1]
        child = {
            "level": node["level"] + 1,
            "title": (seg.title or ("Врезка" if seg.type == "inset" else "Подпись к иллюстрации" if seg.type == "caption" else "")).strip()[:200],
            "content": text, "children": [], "block_type": seg.type,
        }
        p0, p1 = _pages(ps)
        if p0:
            child["page_start"], child["page_end"] = p0, p1
        node["children"].append(child)
        aux_between = True
    return root, boundaries, topics, titles


class LLMBoundarySegmenter:
    """Границы body-сегментов модели для упаковщика правил (`create_llm_chunks(segmenter=…)`).

    Принимает множество текстов абзацев-границ или словарь {текст: название подтемы};
    ``titles()`` отдаёт названия подтем по индексам единиц (в ``fragment_data.topic_title``).
    """

    def __init__(self, boundary_texts, topics: Optional[Dict[str, str]] = None) -> None:
        if isinstance(boundary_texts, dict):
            self.boundary_titles = {t.strip(): (title or "").strip() for t, title in boundary_texts.items() if t and t.strip()}
        else:
            self.boundary_titles = {t.strip(): "" for t in boundary_texts if t and t.strip()}
        self.boundary_texts = set(self.boundary_titles)
        self.topics = {t.strip(): (title or "").strip() for t, title in (topics or {}).items() if t and t.strip()}
        self.topics.update({t: title for t, title in self.boundary_titles.items() if title})

    @staticmethod
    def _match(paragraph: str, texts) -> Optional[str]:
        head = paragraph.strip()[:120]
        for t in texts:
            if head.startswith(t[:120]):
                return t
        return None

    def boundaries(self, paragraphs: List[str]) -> Set[int]:
        return {i for i, p in enumerate(paragraphs) if self._match(p, self.boundary_texts) is not None}

    def titles(self, paragraphs: List[str]) -> Dict[int, str]:
        """{индекс единицы: подтема} для начал body-сегментов модели (границ и первых в узле)."""
        out: Dict[int, str] = {}
        for i, p in enumerate(paragraphs):
            t = self._match(p, self.topics)
            if t is not None and self.topics.get(t):
                out[i] = self.topics[t]
        return out
