"""Типы сегментов LLM-разметки и политика «оставить/выбросить».

Расширяет общий словарь `chunker/blocks.py`: модель различает больше типов
(таблица, легенда, введение…), но в чанки попадают только сохраняемые.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

SEGMENT_TYPES = frozenset({
    "heading", "body", "list", "glossary_entry", "inset", "table", "caption",
    "exercise", "toc", "imprint", "abbreviations", "legend", "intro", "junk",
})

# Что попадает в чанки. Политика, а не мнение модели: меньше «творчества».
KEEP_POLICY: Dict[str, bool] = {
    "body": True, "list": True, "glossary_entry": True, "inset": True, "caption": True,
    "table": False, "heading": False, "exercise": False, "toc": False, "imprint": False,
    "abbreviations": False, "legend": False, "intro": False, "junk": False,
}

# Синонимы, которые модель может вернуть вместо канонического типа.
TYPE_ALIASES = {
    "врезка": "inset", "подпись": "caption", "задание": "exercise", "задания": "exercise",
    "вопросы": "exercise", "оглавление": "toc", "содержание": "toc", "заголовок": "heading",
    "текст": "body", "абзац": "body", "список": "list", "термин": "glossary_entry",
    "понятие": "glossary_entry", "глоссарий": "glossary_entry", "таблица": "table",
    "выходные данные": "imprint", "титул": "imprint", "сокращения": "abbreviations",
    "условные обозначения": "legend", "введение": "intro", "предисловие": "intro",
    "мусор": "junk", "колонтитул": "junk", "paragraph": "body", "text": "body",
    "sidebar": "inset", "figure": "caption", "exercises": "exercise", "question": "exercise",
    "questions": "exercise", "glossary": "glossary_entry", "term": "glossary_entry",
    "front_matter": "imprint", "title": "heading", "header": "heading",
}


@dataclass
class Paragraph:
    idx: int
    text: str
    page: Optional[int] = None
    hint: Optional[str] = None
    # Нумерованный подзаголовок в начале абзаца («1 Влияние Победы»), найденный
    # layout-извлечением: детерминированная граница и заголовок 3 уровня.
    runin: str = ""


@dataclass
class Segment:
    start: int
    end: int                 # включительно
    type: str
    title: str = ""
    level: Optional[int] = None
    source: str = "llm"      # llm | fallback | gapfill
    window: int = -1
    # Модель сказала, что сегмент продолжает мысль предыдущего окна:
    # граница чанка на стыке окон не ставится.
    continues: bool = False


def normalize_type(value) -> str:
    if not isinstance(value, str):
        return "body"
    v = value.strip().lower().replace("-", "_")
    if v in SEGMENT_TYPES:
        return v
    v2 = v.replace("_", " ")
    return TYPE_ALIASES.get(v, TYPE_ALIASES.get(v2, "body"))
