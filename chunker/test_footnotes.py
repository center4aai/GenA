"""
Тесты на обработку цифр-сносок (суперскрипт) при парсинге документов.

Поведение, которое фиксируется:
  Цифра-суперскрипт рядом с текстом (например, "Статья 92¹" или "Статья 92<sup>1</sup>")
  при парсинге заменяется на фразу " (см.сноску N)".
  Так LLM не путает её с обычной цифрой и не склеивает в "Статья 921".

Запуск:
    cd chunker && pytest test_footnotes.py -v
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from docx2json_outline import DocumentProcessor, UniversalProcessor


# --- 1) Unicode-суперскрипты (¹²³…) ---------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        # Кейс из задачи: "Статья 92" с суперскриптом-сноской "1".
        ("Статья 92\u00b9", "Статья 92 (см.сноску 1)"),
        # Многоразрядная сноска.
        ("Статья 92\u00b9\u00b2", "Статья 92 (см.сноску 12)"),
        # Сноска посреди фразы.
        ("см. ст. 5\u00b3 настоящего Кодекса", "см. ст. 5 (см.сноску 3) настоящего Кодекса"),
        # Все цифровые суперскрипты.
        ("a\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079b", "a (см.сноску 0123456789)b"),
    ],
)
def test_unicode_superscripts_replaced(raw: str, expected: str):
    assert DocumentProcessor._normalize_unicode_superscripts(raw) == expected


def test_text_without_superscripts_is_unchanged():
    src = "Статья 92. Президент Российской Федерации, прекративший исполнение полномочий..."
    assert DocumentProcessor._normalize_unicode_superscripts(src) == src


def test_no_collision_with_regular_digits():
    """Обычные цифры рядом с заголовком не должны превращаться в сноску."""
    src = "Статья 92"
    assert DocumentProcessor._normalize_unicode_superscripts(src) == src
    assert "(см.сноску" not in DocumentProcessor._normalize_unicode_superscripts(src)


# --- 2) DOCX → Markdown: <sup>N</sup> → (см.сноску N) ---------------------

def _make_processor() -> DocumentProcessor:
    """Берём конкретного наследника, у которого можно вызвать защищённый метод."""
    return UniversalProcessor()


def test_docx_sup_tag_replaced_with_footnote_phrase(tmp_path: Path):
    """
    На вход — DOCX, mammoth превращает суперскрипт-сноску "1" в <sup>1</sup>.
    На выходе из _docx_to_markdown_with_footnotes ожидаем фразу " (см.сноску 1)"
    и отсутствие склеек вида "Статья 921".
    """
    fake_docx = tmp_path / "fake.docx"
    fake_docx.write_bytes(b"not a real docx, mammoth is mocked")

    html = "<h2>Статья 92<sup>1</sup></h2><p>Президент Российской Федерации...</p>"

    fake_mammoth_result = MagicMock()
    fake_mammoth_result.value = html

    with patch("mammoth.convert_to_html", return_value=fake_mammoth_result), \
         patch(
             "markitdown.converter_utils.docx.pre_process.pre_process_docx",
             side_effect=lambda f: f,
         ):
        md = _make_processor()._docx_to_markdown_with_footnotes(str(fake_docx))

    assert "(см.сноску 1)" in md, f"Ожидали фразу со сноской в markdown, а получили:\n{md}"
    assert "Статья 92 (см.сноску 1)" in md, (
        f"Ожидали 'Статья 92 (см.сноску 1)' (с пробелом перед скобкой), получили:\n{md}"
    )
    # И никакой склейки цифр после "Статья 92".
    assert "Статья 921" not in md
    assert "Статья 92(" not in md


def test_docx_multidigit_sup_tag(tmp_path: Path):
    """Многоразрядная сноска (например, <sup>12</sup>) сохраняет число целиком."""
    fake_docx = tmp_path / "fake.docx"
    fake_docx.write_bytes(b"")

    html = "<p>пункт 7<sup>12</sup> закона</p>"
    fake_mammoth_result = MagicMock()
    fake_mammoth_result.value = html

    with patch("mammoth.convert_to_html", return_value=fake_mammoth_result), \
         patch(
             "markitdown.converter_utils.docx.pre_process.pre_process_docx",
             side_effect=lambda f: f,
         ):
        md = _make_processor()._docx_to_markdown_with_footnotes(str(fake_docx))

    assert "пункт 7 (см.сноску 12) закона" in md
