"""Окна абзацев для LLM-разметки: независимы, с read-only контекстом."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .blocks import Paragraph

_HEADING_LIKE_RE = re.compile(r'^(?:Глава|ГЛАВА|§|Раздел|РАЗДЕЛ|Тема|Урок|\d{1,2}\.\s*[А-ЯЁ])', re.IGNORECASE)


@dataclass
class Window:
    index: int
    context: List[Paragraph] = field(default_factory=list)   # предыдущие абзацы, не размечать
    paras: List[Paragraph] = field(default_factory=list)

    @property
    def first_idx(self) -> int:
        return self.paras[0].idx

    @property
    def last_idx(self) -> int:
        return self.paras[-1].idx

    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.paras)


def _heading_like(p: Paragraph) -> bool:
    t = p.text.strip()
    if len(t) > 80 or t.endswith(('.', '!', '?')):
        return False
    letters = [c for c in t if c.isalpha()]
    return bool(_HEADING_LIKE_RE.match(t)) or (len(letters) >= 4 and all(c.isupper() for c in letters))


def build_windows(paragraphs: List[Paragraph], *, window_chars: int = 12000, context_paras: int = 3,
                  soft_boundary_ratio: float = 0.75) -> List[Window]:
    windows: List[Window] = []
    i, n = 0, len(paragraphs)
    while i < n:
        chars = 0
        j = i
        while j < n and (chars == 0 or chars + len(paragraphs[j].text) <= window_chars):
            chars += len(paragraphs[j].text)
            j += 1
        # Мягкая граница: заголовок в последней четверти окна открывает следующее.
        soft_from = i + max(1, int((j - i) * soft_boundary_ratio))
        for k in range(j - 1, soft_from - 1, -1):
            if _heading_like(paragraphs[k]):
                j = k
                break
        ctx = paragraphs[max(0, i - context_paras):i]
        windows.append(Window(len(windows), list(ctx), list(paragraphs[i:j])))
        i = j
    return windows
