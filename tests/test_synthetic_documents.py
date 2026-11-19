"""Чанкер на синтетических документах (tests/data/synthetic, генератор tests/fixtures/synthetic/build.py).

Синтетика повторяет приёмы вёрстки реальных учебников и энциклопедии, которые
в публичную копию репозитория не попадают: те же свойства, что проверяют
test_textbook_layout / test_encyclopedia_outline / test_normative_golden на
реальных файлах, здесь проверяются на вымышленном тексте.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline as outline  # noqa: E402
import main as chunker_mod  # noqa: E402
import textbook_layout as tl  # noqa: E402

pytest.importorskip("pdfplumber")

_BUILD_PY = _REPO / "tests" / "fixtures" / "synthetic" / "build.py"
_SYN = _REPO / "tests" / "data" / "synthetic"
MAX_FIXTURE_BYTES = 200 * 1024


def _load_build():
    spec = importlib.util.spec_from_file_location("synthetic_build", _BUILD_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses ищут модуль в sys.modules
    spec.loader.exec_module(module)
    return module


build = _load_build()
TEXTBOOK = _SYN / build.TEXTBOOK
ENCYCLOPEDIA = _SYN / build.ENCYCLOPEDIA
NORMATIVE = _SYN / build.NORMATIVE

CHAPTER = "Глава I САДЫ ЗЕЛЁНОЙ ДОЛИНЫ"
S1 = "§ 1 Как устроен сад на склоне холма"
S2 = build.SECTION_2
RUNIN_TITLES = [t.rstrip(".") for t, _ in (build.RUNIN_1, build.RUNIN_2, build.RUNIN_3, build.RUNIN_4, build.RUNIN_5)]
INSET_HEADS = [build.INSET_PLAIN_HEAD, build.BIO_NAME, build.COLUMN_LEFT_HEAD, build.COLUMN_RIGHT_HEAD]

DOC_TYPE_INFO = {"document_type": "unknown", "document_name": "x", "confidence": 0.0,
                 "description": "", "key_indicators": [], "titles_count": 0}


def _fd(c):
    return c["fragment_data"]


def _path(c):
    return [p["title"] for p in _fd(c)["hierarchy_context"]["breadcrumb_path"]]


def _ctx(c):
    """Путь чанка с его заголовком: «Глава … > § … > раздел»."""
    return " > ".join(_path(c) + ([_fd(c)["title"]] if _fd(c)["title"] not in _path(c) else []))


def _flat(text):
    return " ".join((text or "").replace("-\n", "").split())


def _walk(node):
    yield node
    for ch in node.get("children") or []:
        yield from _walk(ch)


def _layout_chunks(path):
    stats = {}
    blocks, prof = tl.extract_layout_blocks(str(path), stats)
    tree = tl.build_tree(blocks, stats)
    chunks = chunker_mod.create_llm_chunks(tree, min_size=30, document_name="x", document_kind="textbook", stats=stats)
    return blocks, prof, tree, chunks, stats


def _process(path, kind, method="rules", min_size=30):
    stats = {}
    with patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO):
        chunks, _info = chunker_mod.process_document_to_chunks(
            str(path), min_size=min_size, document_kind=kind, stats=stats, chunking_method=method
        )
    return chunks, stats


@pytest.fixture(scope="module")
def textbook():
    return _layout_chunks(TEXTBOOK)


# ─── учебник: профиль, глава, параграфы ───────────────────────────────


def test_textbook_profile_is_unambiguous(textbook):
    _blocks, prof, _tree, _chunks, stats = textbook
    assert (prof.body_family, prof.body_size) == ("DejaVuSerif", 11.0)
    assert prof.body_x0 == pytest.approx(57.0) and prof.indent_x0 == pytest.approx(74.0)
    assert prof.heading_families == {"DejaVuSans"}
    assert stats["layout"]["pages"] == 5


def test_textbook_chapter_number_line_joins_chapter_title(textbook):
    blocks, _prof, _tree, _chunks, stats = textbook
    headings = [b for b in blocks if b.kind == "heading"]
    assert (headings[0].title, headings[0].level) == (CHAPTER, 1)
    assert stats["layout"]["chapters_synthesized"] == 1
    assert not any(b.title in ("I", "ДОЛИНЫ", "ГЛАВА САДЫ ЗЕЛЁНОЙ") for b in headings)


def test_textbook_three_line_section_title_around_number_is_merged(textbook):
    blocks, _prof, _tree, _chunks, stats = textbook
    sections = [(b.title, b.level) for b in blocks if b.kind == "heading" and b.title.startswith("§")]
    assert sections == [(S1, 2), (S2, 2)]
    assert stats["layout"]["section_titles_merged"] == 1
    parts = set(build.SECTION_1)
    assert not any(b.kind == "heading" and b.title in parts for b in blocks)


def test_textbook_body_chunks_have_chapter_section_and_subsection_paths(textbook):
    _blocks, _prof, _tree, chunks, _stats = textbook
    bodies = [c for c in chunks if _fd(c)["block_type"] == "body"]
    intro, rest = bodies[0], bodies[1:]
    assert _ctx(intro) == CHAPTER and build.CHAPTER_INTRO[0][:30] in _flat(_fd(intro)["content"])
    assert all(_ctx(c).startswith(f"{CHAPTER} > §") for c in rest), [_ctx(c) for c in rest]
    first_runin = next(c for c in bodies if _fd(c)["title"] == RUNIN_TITLES[0])
    assert _fd(first_runin)["combined_text"].split("\n\n", 1)[0] == f"{CHAPTER} > {S1} > {RUNIN_TITLES[0]}"


# ─── run-in и капс-вопросы — границы чанков ───────────────────────────


def test_textbook_runin_headings_start_their_own_chunks(textbook):
    blocks, _prof, _tree, chunks, stats = textbook
    assert [b.title for b in blocks if b.kind == "heading" and b.inline] == RUNIN_TITLES
    assert stats["layout"]["runin_headings"] == len(RUNIN_TITLES)
    for title in RUNIN_TITLES:
        own = [c for c in chunks if _fd(c)["block_type"] == "body" and _fd(c)["title"] == title]
        assert own and _fd(own[0])["content"].startswith(f"{title}."), title


def test_textbook_caps_question_is_a_section_heading(textbook):
    blocks, _prof, _tree, chunks, _stats = textbook
    q = next(b for b in blocks if b.kind == "heading" and b.title == build.QUESTION_HEADING)
    assert q.level == 3 and not q.inline
    own = [c for c in chunks if _fd(c)["title"] == build.QUESTION_HEADING]
    assert own and all(_ctx(c) == f"{CHAPTER} > {S1} > {build.QUESTION_HEADING}" for c in own)
    assert _flat(build.QUESTION_BODY[0]) in _flat(_fd(own[0])["content"])


# ─── врезки, подписи ──────────────────────────────────────────────────


def test_textbook_inset_headings_title_insets_not_body_paths(textbook):
    blocks, _prof, _tree, chunks, stats = textbook
    assert not any(b.kind == "heading" and b.title in INSET_HEADS for b in blocks)
    assert stats["layout"]["inset_titles"] == len(INSET_HEADS)
    inset_titles = [_fd(c)["title"] for c in chunks if _fd(c)["block_type"] == "inset"]
    assert set(INSET_HEADS) <= set(inset_titles)
    for c in chunks:
        assert not set(INSET_HEADS) & set(_path(c)), _ctx(c)


@pytest.mark.parametrize("title,text", [
    (build.INSET_PLAIN_HEAD, build.INSET_PLAIN),
    (build.BIO_NAME, build.BIO_TEXT),
    (build.COLUMN_LEFT_HEAD, build.COLUMN_LEFT),
    (build.COLUMN_RIGHT_HEAD, build.COLUMN_RIGHT),
])
def test_textbook_each_inset_is_exactly_one_whole_chunk(textbook, title, text):
    _blocks, _prof, _tree, chunks, _stats = textbook
    holders = [c for c in chunks if _flat(text)[:40] in _flat(_fd(c)["content"])]
    assert len(holders) == 1
    fd = _fd(holders[0])
    assert (fd["block_type"], fd["title"]) == ("inset", title)
    # Колонки соседних плашек не перемешаны: текст врезки — ровно её колонка.
    assert _flat(fd["content"]) == _flat(text)


def test_textbook_caption_is_separate_chunk(textbook):
    _blocks, _prof, _tree, chunks, _stats = textbook
    caps = [c for c in chunks if "Рис. 1." in _fd(c)["content"]]
    assert len(caps) == 1 and _fd(caps[0])["block_type"] == "caption"
    assert _flat(_fd(caps[0])["content"]) == build.FIGURE_CAPTION


def test_textbook_summary_on_fill_is_level3_and_next_section_not_under_it(textbook):
    blocks, _prof, tree, chunks, stats = textbook
    summary = next(b for b in blocks if b.kind == "heading" and b.title == build.SUMMARY_HEAD)
    assert summary.level == 3 and stats["layout"]["heading_levels_demoted"] >= 1
    chunk = next(c for c in chunks if _flat(build.SUMMARY)[:40] in _flat(_fd(c)["content"]))
    assert _fd(chunk)["block_type"] == "inset" and _path(chunk)[-1] == build.SUMMARY_HEAD
    chapter = tree["children"][0]
    assert [n["title"] for n in chapter["children"]] == [S1, S2]
    assert not any(build.SUMMARY_HEAD in _path(c) for c in chunks if S2 in _path(c) or _fd(c)["title"] == S2)


# ─── аппарат: ключевые понятия, задания, доп. материалы ───────────────


def test_textbook_key_terms_exercises_and_materials_are_dropped(textbook):
    _blocks, _prof, tree, chunks, stats = textbook
    dropped = stats["dropped_blocks"]
    assert dropped["key_terms"]["count"] >= 1
    assert dropped["exercise"]["count"] >= 1
    assert dropped["apparatus"]["count"] >= 1
    assert not any(_flat(n.get("title")).lower() == "ключевые понятия" for n in _walk(tree))
    texts = [_flat(_fd(c)["content"]) for c in chunks]
    needles = ["• терраса", build.EXERCISES[0][3:30], build.EXERCISES[2][3:30], "Учебный фильм", "Книга для чтения"]
    for needle in needles:
        assert not any(needle in t for t in texts), needle
    rubrics = {"КЛЮЧЕВЫЕ ПОНЯТИЯ", build.EXERCISES_HEAD, build.MATERIALS_HEAD}
    assert not any(rubrics & set(_path(c) + [_fd(c)["title"]]) for c in chunks)


def test_textbook_key_terms_do_not_reach_llm_paragraphs(textbook):
    blocks, _prof, _tree, _chunks, _stats = textbook
    paras = tl.layout_blocks_to_paragraphs(blocks)
    assert not any("• терраса" in p["text"] for p in paras)
    assert [p["runin"] for p in paras if p.get("runin")] == RUNIN_TITLES


# ─── страницы и колонтитулы ───────────────────────────────────────────


def test_textbook_paragraph_across_page_break_is_one_chunk(textbook):
    _blocks, _prof, _tree, chunks, _stats = textbook
    head, tail = _flat(build.CROSSING)[:40], _flat(build.CROSSING)[-40:]
    holders = [c for c in chunks if head in _flat(_fd(c)["content"])]
    assert len(holders) == 1 and tail in _flat(_fd(holders[0])["content"])
    assert (_fd(holders[0])["page_start"], _fd(holders[0])["page_end"]) == (3, 4)
    assert not any(_fd(c)["content"].lstrip()[:1].islower() for c in chunks)


def test_textbook_running_headers_and_page_numbers_are_not_text(textbook):
    _blocks, _prof, _tree, chunks, stats = textbook
    for c in chunks:
        for line in _fd(c)["content"].splitlines():
            assert not re.fullmatch(r"\d{1,3}", line.strip()), _ctx(c)
            assert "ГЛАВА I" not in line and build.SECTION_1_RUNNING not in line, line
    # 4 колонтитула + 5 номеров страниц + номер и продолжение названия главы.
    assert stats["dropped_blocks"]["junk"]["count"] >= 9


# ─── методы rules / layout / semantic ─────────────────────────────────


class _ParagraphSegmenter:
    """Сегментер без эмбеддера: граница перед каждым абзацем."""

    calls = 0

    def boundaries(self, items):
        _ParagraphSegmenter.calls += 1
        return set(range(1, len(items)))


def test_textbook_layout_method_reports_engine_and_same_paths(textbook):
    _blocks, _prof, _tree, direct, _s = textbook
    chunks, stats = _process(TEXTBOOK, "textbook", "layout")
    assert (stats["chunking_method"], stats["chunking_engine"], stats["chunking_fallback"]) == ("layout", "layout_pdf", None)
    assert [_ctx(c) for c in chunks] == [_ctx(c) for c in direct]


def test_textbook_semantic_method_keeps_layout_tree(monkeypatch):
    monkeypatch.setenv("CHUNKER_EMBEDDING_URL", "http://embeddings.invalid/v1")
    _ParagraphSegmenter.calls = 0
    with patch.object(chunker_mod, "_make_semantic_segmenter", side_effect=lambda st: _ParagraphSegmenter()):
        sem, sem_stats = _process(TEXTBOOK, "textbook", "semantic")
    lay, lay_stats = _process(TEXTBOOK, "textbook", "layout")
    assert sem_stats["chunking_method"] == "semantic" and sem_stats["chunking_fallback"] is None
    assert _ParagraphSegmenter.calls >= 1
    assert {_ctx(c) for c in sem} == {_ctx(c) for c in lay}
    assert len(sem) >= len(lay)
    assert sem_stats["dropped_blocks"] == lay_stats["dropped_blocks"]


def test_textbook_rules_method_still_chunks_the_pdf():
    chunks, stats = _process(TEXTBOOK, "textbook", "rules")
    assert stats["chunking_engine"] == "hierarchical_outline"
    blob = _flat(" ".join(_fd(c)["content"] for c in chunks))
    assert _flat(build.SECTION_1_LEAD[0])[:40] in blob


def test_textbook_same_structure_when_pdf_draws_space_glyphs(tmp_path, textbook):
    """Текстовый редактор рисует пробелы глифами, вёрстка — сдвигом слов: разбор один и тот же."""
    pytest.importorskip("reportlab")
    spaced = build.build_textbook(tmp_path / "spaced.pdf", spaced=True)
    *_rest, chunks, _stats = _layout_chunks(spaced)
    assert [_ctx(c) for c in chunks] == [_ctx(c) for c in textbook[3]]
    assert [_flat(_fd(c)["content"]) for c in chunks] == [_flat(_fd(c)["content"]) for c in textbook[3]]


# ─── энциклопедия ─────────────────────────────────────────────────────


def _headword(head):
    """«ЛЕСОВИЧ Мирон (1801—1870),» → «ЛЕСОВИЧ»; «ТЕРРАСА,» → «ТЕРРАСА»."""
    return re.match(r"[А-ЯЁ]+(?:\s[А-ЯЁ]+\b)*", head).group(0)


ENTRY_TITLES = [_headword(h) for h, _t in build.ENTRIES]


@pytest.fixture(scope="module")
def encyclopedia_tree():
    return outline.EncyclopediaProcessor().process(str(ENCYCLOPEDIA))


def test_encyclopedia_two_column_entries_are_separate_nodes(encyclopedia_tree):
    nodes = encyclopedia_tree["children"]
    assert [n["title"] for n in nodes] == ENTRY_TITLES
    for node, (_head, text) in zip(nodes, build.ENTRIES):
        assert _flat(node["content"]).endswith(_flat(text)[-30:]), node["title"]
        assert not node["children"]


def test_encyclopedia_entry_across_page_and_signature(encyclopedia_tree):
    by_title = {n["title"]: _flat(n["content"]) for n in encyclopedia_tree["children"]}
    crossing = dict((_headword(h), t) for h, t in build.ENTRIES)["ЦВЕТЕНИЕ ЯБЛОНЬ"]
    assert by_title["ЦВЕТЕНИЕ ЯБЛОНЬ"] == _flat(crossing)
    assert by_title["ЛЕСОВИЧ"].startswith("Мирон (1801—1870),")
    assert not any(build.ENTRY_SIGNATURE in t for t in by_title.values())
    assert build.ENCYCLOPEDIA_TITLE not in " ".join(by_title.values())


def test_encyclopedia_chunks_prefix_headword():
    chunks, _stats = _process(ENCYCLOPEDIA, "encyclopedia")
    assert [_fd(c)["title"] for c in chunks] == ENTRY_TITLES
    for c in chunks:
        head, _sep, body = _fd(c)["combined_text"].partition("\n\n")
        assert head == _fd(c)["title"] and body.strip()
        assert not re.search(r"(?m)^\s*\d{1,3}\s*$", _fd(c)["content"])


# ─── нормативный акт (DOCX) ───────────────────────────────────────────


ARTICLES = [(chapter, article) for chapter, arts in build.LAW for article, _paras in arts]


@pytest.fixture(scope="module")
def law_chunks():
    pytest.importorskip("docx")
    return _process(NORMATIVE, "normative", min_size=50)[0]


def test_normative_chapters_and_articles_are_in_paths(law_chunks):
    seen = []
    for c in law_chunks:
        path = _path(c) + [_fd(c)["title"]]
        pair = (path[0], path[1])
        assert pair in ARTICLES, path
        if pair not in seen:
            seen.append(pair)
    assert seen == ARTICLES


def test_normative_points_are_nested_under_their_article(law_chunks):
    points = [c for c in law_chunks if re.match(r"\d\) ", _fd(c)["title"])]
    assert [_fd(c)["title"][:2] for c in points] == ["1)", "2)", "3)", "1)", "2)", "3)"]
    assert {tuple(_path(c)) for c in points} == {
        ("Глава 1. Общие положения", "Статья 2. Основные понятия"),
        ("Глава 2. Устройство террас", "Статья 3. Разрешение на устройство террасы"),
    }


def test_normative_layout_method_is_rejected():
    with pytest.raises(chunker_mod.ChunkingMethodUnavailable):
        _process(NORMATIVE, "normative", "layout")


@pytest.mark.xfail(strict=True, reason=(
    "docx2json_outline._extract_numbered_items_from_content_with_positions "
    "(chunker/docx2json_outline.py:563-569): последний пункт «N)» тянется до конца статьи, "
    "поэтому следующая за пунктами часть «3. …» попадает внутрь пункта «3) …»"
))
def test_normative_part_after_points_belongs_to_article(law_chunks):
    part = next(c for c in law_chunks if "рассматривает обращение" in _fd(c)["content"])
    assert _path(part) + [_fd(part)["title"]] == [
        "Глава 2. Устройство террас", "Статья 3. Разрешение на устройство террасы",
    ]


# ─── генератор ────────────────────────────────────────────────────────


def _pdf_signature(path):
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return [(page.extract_text(), sorted({c["fontname"] for c in page.chars}), len(page.rects), len(page.images))
                for page in pdf.pages]


def _docx_signature(path):
    from docx import Document

    return [(p.style.name, p.text) for p in Document(str(path)).paragraphs]


def _signature(path):
    return _pdf_signature(path) if path.suffix == ".pdf" else _docx_signature(path)


def test_committed_fixtures_are_small_and_match_generator(tmp_path, capsys):
    pytest.importorskip("reportlab")
    assert build.main(["--out", str(tmp_path)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == len(build.OUTPUTS)
    for name in build.OUTPUTS:
        committed = _SYN / name
        assert committed.stat().st_size <= MAX_FIXTURE_BYTES, name
        # Сверяем содержимое, а не байты: другая версия python-docx/reportlab
        # может иначе упаковать тот же документ.
        assert _signature(tmp_path / name) == _signature(committed), f"{name}: перегенерируйте build.py"


def test_build_is_byte_for_byte_reproducible(tmp_path):
    pytest.importorskip("reportlab")
    first = build.build_all(tmp_path / "a")
    second = build.build_all(tmp_path / "b")
    for a, b in zip(first, second):
        assert a.read_bytes() == b.read_bytes(), a.name
