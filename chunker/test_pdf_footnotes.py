"""
Регрессионный тест: цифры-сноски (суперскрипт) в PDF.

В PDF (Конституция РФ index.pdf) суперскрипт-сноска у заголовка статьи
после конверсии markitdown теряет инфу о размере шрифта и приклеивается
к номеру статьи: «Статья 67¹» → «Статья 671». Из-за этого в дереве
появлялись узлы вида `Статья 671`/`Статья 921` (см. датасет 69e60ed3).

Фикс: в `DocumentProcessor.convert_to_markdown` для PDF дополнительно
прогоняется pdfplumber — он находит цифры-суперскрипты (по уменьшенному
font-size + приподнятой baseline) и в markdown-тексте заменяет точное
вхождение «Статья 67» + «1» на «Статья 67 (см.сноску 1)».

Запуск:
    cd chunker && pytest test_pdf_footnotes.py -v
"""

from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent

_CANDIDATE_PDF = [
    _HERE.parent / "tests" / "data" / "Конституция РФ index.pdf",
    _HERE / "tests" / "data" / "Конституция РФ index.pdf",
    Path("/app/tests/data/Конституция РФ index.pdf"),
]
PDF_PATH = next((p for p in _CANDIDATE_PDF if p.is_file()), _CANDIDATE_PDF[0])

pytestmark = pytest.mark.skipif(
    not PDF_PATH.is_file(),
    reason=f"тестовый PDF не найден ни по одному из путей: {_CANDIDATE_PDF}",
)


def _walk(node):
    yield node
    for ch in node.get("children", []):
        yield from _walk(ch)


def test_pdf_superscripts_become_footnote_phrase():
    """Статья 67¹ и 92¹ должны стать отдельными узлами с пометкой
    «(см.сноску 1)», а не склеиваться в «Статья 671/921»."""
    from docx2json_outline import extract_outline_from_document

    tree = extract_outline_from_document(str(PDF_PATH))
    titles = [(n.get("title") or "").strip() for n in _walk(tree)]

    assert "Статья 67 (см.сноску 1)" in titles, (
        "не нашли узел 'Статья 67 (см.сноску 1)'; имеющиеся 'Статья 67…': "
        f"{[t for t in titles if t.startswith('Статья 67')]}"
    )
    assert "Статья 92 (см.сноску 1)" in titles, (
        "не нашли узел 'Статья 92 (см.сноску 1)'; имеющиеся 'Статья 92…': "
        f"{[t for t in titles if t.startswith('Статья 92')]}"
    )
    # Обычные статьи 67/92 не должны исчезнуть (они в Конституции есть отдельно).
    assert "Статья 67" in titles, "пропала обычная 'Статья 67'"
    assert "Статья 92" in titles, "пропала обычная 'Статья 92'"

    # И главное — никаких склеек 'Статья 671'/'Статья 921'.
    assert "Статья 671" not in titles, "осталась склейка 'Статья 671' — фикс не сработал"
    assert "Статья 921" not in titles, "осталась склейка 'Статья 921' — фикс не сработал"


def test_pdf_collect_replacements_finds_known_footnotes():
    """Низкоуровневая проверка: pdfplumber-проход находит как минимум сноски
    у статей 67 и 92 (формат: ('Статья 67', '1') и ('Статья 92', '1'))."""
    from docx2json_outline import DocumentProcessor

    pairs = DocumentProcessor._collect_pdf_footnote_replacements(str(PDF_PATH))
    assert pairs, "pdfplumber вообще не нашёл цифр-суперскриптов в Конституции"

    pairs_set = set(pairs)
    assert ("Статья 67", "1") in pairs_set, (
        f"в найденных суперскрипт-сносках нет ('Статья 67', '1'); найдено: {pairs[:20]}"
    )
    assert ("Статья 92", "1") in pairs_set, (
        f"в найденных суперскрипт-сносках нет ('Статья 92', '1'); найдено: {pairs[:20]}"
    )
