"""
Регрессионный тест на парсинг DOCX, где статьи приходят жирным
(`**Статья N.** ...`) после mammoth/markitdown.

Фиксирует баг, при котором GarantProcessor / ConsultantProcessor не
распознавали такие строки как заголовки статьи. В результате вся «Глава N»
становилась листом, в `content` которой подряд лежали все статьи, а
`split_content_into_items` приклеивал строку «**Статья 15.** ...» как
продолжение предыдущего нумерованного пункта статьи 14. Чанки последующих
пунктов теряли упоминание статьи в title/breadcrumb.

Проверяется на реальном файле `tests/data/УК РФ part1.docx`.

Запуск:
    cd chunker && pytest test_garant_bold_articles.py -v
"""

from pathlib import Path
from unittest.mock import patch

import pytest


_HERE = Path(__file__).resolve().parent

# Файл может лежать в репозитории (../tests/data) или быть скопированным
# рядом с модулем (например, в Docker-контейнере, где исходники собраны плоско).
_CANDIDATE_PATHS = [
    _HERE.parent / "tests" / "data" / "УК РФ part1.docx",
    _HERE / "tests" / "data" / "УК РФ part1.docx",
    Path("/app/tests/data/УК РФ part1.docx"),
]

DOCX_PATH = next((p for p in _CANDIDATE_PATHS if p.is_file()), _CANDIDATE_PATHS[0])

pytestmark = pytest.mark.skipif(
    not DOCX_PATH.is_file(),
    reason=f"тестовый файл не найден ни по одному из путей: {_CANDIDATE_PATHS}",
)


def _walk(node, depth=0):
    yield depth, node
    for ch in node.get("children", []):
        yield from _walk(ch, depth + 1)


def _find_node(tree, predicate):
    for _depth, node in _walk(tree):
        if predicate(node):
            return node
    return None


def test_uk_docx_articles_become_separate_nodes_under_chapter():
    """В дереве «Глава 3. Понятие преступления...» должны быть отдельные
    дочерние узлы для статей 14, 15, 16, 17, 18 — а не одна свалка в content."""
    from docx2json_outline import extract_outline_from_document

    tree = extract_outline_from_document(str(DOCX_PATH))

    chapter3 = _find_node(
        tree,
        lambda n: n.get("title", "").startswith("Глава 3."),
    )
    assert chapter3 is not None, "не нашли узел 'Глава 3' в дереве"

    child_titles = [ch.get("title", "") for ch in chapter3.get("children", [])]
    # Главное: статья НЕ оказалась внутри content главы как простой текст,
    # а стала отдельным дочерним узлом.
    for expected in ("Статья 14.", "Статья 15.", "Статья 17.", "Статья 18."):
        assert any(t.startswith(expected) for t in child_titles), (
            f"под 'Глава 3' не нашли отдельный узел {expected!r}; "
            f"были: {child_titles}"
        )

    # И никакая «Статья 15.» не должна болтаться внутри content самой 'Глава 3'.
    chapter_content = chapter3.get("content", "") or ""
    assert "Статья 15" not in chapter_content, (
        "заголовок 'Статья 15' остался внутри content 'Главы 3' — фикс не отработал"
    )

    # Заголовки статей не должны прийти с обёрткой "**...**".
    for t in child_titles:
        assert not t.startswith("**"), f"в title статьи остались markdown-маркеры: {t!r}"


def _make_chunks(docx_path: Path):
    """Полный пайплайн до чанков, с замоканным определением типа документа."""
    import main as M

    fake_dt = {
        "document_type": "unknown",
        "document_name": "УК РФ part1",
        "confidence": 0.0,
        "description": "",
        "titles_count": 0,
        "key_indicators": [],
    }
    with patch.object(M, "identify_document_type", return_value=fake_dt):
        chunks, _dt = M.process_document_to_chunks(str(docx_path), min_size=50)
    return chunks


def test_uk_docx_chunks_for_article_15_keep_article_in_title():
    """Чанки для пунктов статьи 15 должны содержать «Статья 15» в title,
    а breadcrumb — указывать на 'Глава 3'."""
    chunks = _make_chunks(DOCX_PATH)

    art15_chunks = [
        c for c in chunks
        if (c["fragment_data"].get("title") or "").startswith("Статья 15.")
    ]
    assert art15_chunks, "не нашли ни одного чанка со статьёй 15 в title"

    for c in art15_chunks:
        bc = c["hierarchy_context"].get("breadcrumb_text", "")
        assert "Глава 3" in bc, (
            f"breadcrumb чанка статьи 15 потерял главу: {bc!r}"
        )
        # Контент чанка должен начинаться с одного из пунктов 1..6 (или быть
        # самим заголовком, если у статьи в этом месте нет нумерованных пунктов),
        # но в любом случае не должен начинаться с заголовка ДРУГОЙ статьи.
        content = c["fragment_data"].get("content") or ""
        assert "Статья 14" not in content, (
            "в content чанка статьи 15 затесалась статья 14 — границы статей съехали"
        )
        assert "Статья 16" not in content, (
            "в content чанка статьи 15 затесалась статья 16 — границы статей съехали"
        )

    # И зеркальная проверка: в чанках статьи 14 не должно быть упоминания статьи 15.
    art14_chunks = [
        c for c in chunks
        if (c["fragment_data"].get("title") or "").startswith("Статья 14.")
    ]
    assert art14_chunks, "не нашли ни одного чанка со статьёй 14 в title"
    for c in art14_chunks:
        content = c["fragment_data"].get("content") or ""
        assert "Статья 15" not in content, (
            "к чанку статьи 14 приклеилась строка 'Статья 15' — старый баг вернулся"
        )
