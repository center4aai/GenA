"""Документ → пронумерованные абзацы для LLM-разметки."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .blocks import Paragraph

logger = logging.getLogger(__name__)

_LIST_LINE_RE = re.compile(r'^\s*(?:\(?\d{1,2}[.)]|\(?[а-яa-z][.)]|[•●▪■◆*–—-])\s+\S')
_SENT_END_RE = re.compile(r'[.!?…:;]\s*["»)\]]?$')
_LOWER_START_RE = re.compile(r'^[«"(]?[а-яёa-z]')


def text_to_paragraphs(text: str) -> List[Paragraph]:
    """Текст → абзацы: пустые строки и маркеры списков делят, разрыв страницы склеивается."""
    from text_utils import split_sentences

    raw_paras = [p.strip() for p in re.split(r'\n\s*\n', text or "") if p.strip()]
    pieces: List[str] = []
    for para in raw_paras:
        lines = para.split("\n")
        buf: List[str] = []
        for line in lines:
            if _LIST_LINE_RE.match(line) and buf:
                pieces.append("\n".join(buf).strip())
                buf = [line]
            else:
                buf.append(line)
        if buf:
            pieces.append("\n".join(buf).strip())
    merged: List[str] = []
    for piece in pieces:
        if merged and _LOWER_START_RE.match(piece):
            prev = merged[-1]
            if not _SENT_END_RE.search(prev):
                merged[-1] = f"{prev} {piece}"
                continue
            sentences = split_sentences(prev)
            tail = sentences[-1] if sentences else prev
            probe = split_sentences(f"{tail} {piece[:80]}")
            if probe and len(probe[0]) > len(tail):
                merged[-1] = f"{prev} {piece}"
                continue
        merged.append(piece)
    return [Paragraph(i, re.sub(r'[ \t]+', ' ', p).strip()) for i, p in enumerate(merged)]


def document_to_paragraphs(path: str, kind: str = "textbook", stats: Optional[Dict[str, Any]] = None) -> List[Paragraph]:
    """Файл → абзацы. PDF при возможности идёт через layout-путь (страницы и подсказки)."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        try:
            from textbook_layout import extract_layout_blocks, layout_blocks_to_paragraphs

            blocks, _prof = extract_layout_blocks(str(p), stats)
            items = layout_blocks_to_paragraphs(blocks)
            if items:
                if stats is not None:
                    stats["llm_segmentation_source"] = "layout"
                return [
                    Paragraph(i, it["text"], it.get("page"), it.get("hint"), it.get("runin") or "")
                    for i, it in enumerate(items)
                ]
        except Exception as exc:  # noqa: BLE001 — layout необязателен
            logger.info("Layout paragraphs unavailable for LLM segmentation (%s), using text path", exc)
    if suffix in (".txt", ".md"):
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        from docx2json_outline import DocumentProcessor, _textbook_cleanup_text

        text = DocumentProcessor().convert_to_markdown(str(p), apply_pdf_footnotes=False)
        text = _textbook_cleanup_text(text)
    if stats is not None:
        stats["llm_segmentation_source"] = "text"
    return text_to_paragraphs(text)
