"""Regressions for two chunker paths that used to delete normative text silently.

Both were invisible in the logs: the article-marker strip happened before
coverage was measured, and normative documents had no coverage accounting at
all.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import main as chunker_mod  # noqa: E402


def _contents(tree, kind="normative"):
    chunks = chunker_mod.create_llm_chunks(tree, document_name="doc", document_kind=kind)
    return [c["fragment_data"].get("content") or "" for c in chunks]


def _node(title, content, children=None, level=2):
    return {
        "level": level,
        "title": title,
        "content": content,
        "children": children or [],
    }


def _root(*children):
    return {"level": 0, "title": "", "content": "", "children": list(children)}


def test_item_glued_to_article_heading_survives():
    """PDF layouts put «Статья 15. Название» and item 1 on the same line."""
    tree = _root(
        _node(
            "Глава 1",
            "Статья 15. Категории преступлений 1. Преступления подразделяются "
            "на четыре категории, что имеет значение для назначения наказания.\n"
            "2. Преступлениями небольшой тяжести признаются деяния.\n",
        )
    )
    joined = "\n".join(_contents(tree))
    assert "Преступления подразделяются на четыре категории" in joined
    assert "Преступлениями небольшой тяжести" in joined


def test_article_heading_alone_is_still_stripped():
    """The heading itself must not become a chunk — that was the original point."""
    tree = _root(
        _node(
            "Глава 1",
            "Статья 15. Категории преступлений\n"
            "1. Преступления подразделяются на четыре категории по степени тяжести.\n",
        )
    )
    joined = "\n".join(_contents(tree))
    assert "Категории преступлений" not in joined
    assert "Преступления подразделяются" in joined


def test_non_leaf_normative_body_is_not_dropped():
    """A GOST section carrying its own text on top of subsections."""
    tree = _root(
        _node(
            "4 Общие требования",
            "Настоящий раздел устанавливает общие требования к маркировке "
            "продукции и порядку её нанесения на упаковку изделия.",
            children=[
                _node(
                    "4.1 Маркировка",
                    "1. Маркировка наносится несмываемой краской на видную сторону.",
                    level=3,
                )
            ],
        )
    )
    joined = "\n".join(_contents(tree))
    assert "устанавливает общие требования к маркировке" in joined
    assert "несмываемой краской" in joined


def test_prose_non_leaf_body_still_kept():
    tree = _root(
        _node(
            "Глава 2",
            "Вводный абзац главы, который поясняет содержание последующих "
            "параграфов и сам по себе несёт фактическую информацию.",
            children=[
                _node("§ 1", "Текст первого параграфа с фактами и датами 1917 года.", level=3)
            ],
        )
    )
    joined = "\n".join(_contents(tree, kind="textbook"))
    assert "Вводный абзац главы" in joined
