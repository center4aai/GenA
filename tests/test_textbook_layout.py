"""Layout-извлечение PDF учебника (chunker/textbook_layout.py) на выдержках из тестовых PDF."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline  # noqa: E402,F401  (pin canonical modules first)
import main as chunker_mod  # noqa: E402
import textbook_layout as tl  # noqa: E402

pytest.importorskip("pdfplumber")

_DATA = _REPO / "tests" / "data"
OBSCH = _DATA / "obsch9_layout_sample.pdf"       # стр. 41-43, 61, 85 «Обществознание 9»
MEDINSKY = _DATA / "medinskiy_layout_sample.pdf"  # стр. 51-52, 121 «История России 11»
MEDINSKY_FULL = _DATA / "Medinskiy_Torkunov_Istoria_Rossii_11_kl_Bazovy_2023.pdf"

needs_obsch = pytest.mark.skipif(not OBSCH.is_file(), reason="fixture missing")
needs_medinsky = pytest.mark.skipif(not MEDINSKY.is_file(), reason="fixture missing")
needs_medinsky_full = pytest.mark.skipif(not MEDINSKY_FULL.is_file(), reason="full textbook missing")

DOC_TYPE_INFO = {"document_type": "unknown", "document_name": "x", "confidence": 0.0,
                 "description": "", "key_indicators": [], "titles_count": 0}


def _chunks(path):
    stats = {}
    blocks, prof = tl.extract_layout_blocks(str(path), stats)
    tree = tl.build_tree(blocks, stats)
    chunks = chunker_mod.create_llm_chunks(tree, min_size=30, document_name="x", document_kind="textbook", stats=stats)
    return blocks, prof, chunks, stats


# ─── helpers ──────────────────────────────────────────────────────────


def test_font_family_and_colors():
    assert tl.font_family("ABCDEF+NewtonSanPin-Regular") == "NewtonSanPin"
    assert tl.font_family("TextbookNSanPin-Bold") == "TextbookNSanPin"
    assert tl.font_family("ReformaGroteskDemi") == "ReformaGrotesk"
    assert tl.font_family("") == "unknown"
    assert tl.is_non_white((0.0, 0.02, 0.06, 0.035))      # CMYK светлая плашка
    assert not tl.is_non_white((0.0, 0.0, 0.0, 0.0))     # CMYK белый
    assert tl.is_non_white((0.903, 0.907, 0.91))         # RGB серый
    assert not tl.is_non_white((1.0, 1.0, 1.0))
    assert not tl.is_non_white(1.0)
    assert tl.is_non_white(0.5)
    assert not tl.is_non_white(None)


def test_layout_unavailable_for_non_pdf(tmp_path):
    doc = tmp_path / "book.docx"
    doc.write_bytes(b"PK")
    with pytest.raises(tl.LayoutUnavailable, match="PDF only"):
        tl.extract_layout_tree(str(doc))


# ─── обществознание 9 ─────────────────────────────────────────────────


@needs_obsch
def test_profile_detects_body_font_even_when_small_font_dominates_sample():
    blocks, prof, _chunks_, stats = _chunks(OBSCH)
    assert prof.body_family == "NewtonSanPin" and prof.body_size == pytest.approx(11.0)
    assert prof.body_x0 == pytest.approx(37.0) and prof.indent_x0 == pytest.approx(51.0)
    assert stats["layout"]["pages"] == 5


@needs_obsch
def test_obsch_blocks_separate_insets_exercises_and_captions():
    blocks, _prof, _c, _s = _chunks(OBSCH)
    kinds = [b.kind for b in blocks]
    assert "inset" in kinds and "exercise" in kinds and "heading" in kinds
    insets = [b for b in blocks if b.kind == "inset"]
    gos = [b for b in insets if "Госуслуги» в 2009" in b.text]
    assert gos and gos[0].title == "Интересно" and "Выясните" not in gos[0].text
    assert any(b.kind == "exercise" and b.text.startswith("Выясните") for b in blocks)
    assert any(b.kind == "heading" and b.title == "ПРОВЕРЯЕМ СЕБЯ" for b in blocks)
    assert not any(b.kind == "body" and "Выясните" in b.text for b in blocks)
    # Подпись «Худ. …» / «Фотография» не становится основным текстом.
    assert not any(b.kind == "body" and "Фотография. 1988" in b.text for b in blocks)


@needs_obsch
def test_obsch_chunks_have_block_types_pages_and_no_exercise_leaks():
    _b, _p, chunks, stats = _chunks(OBSCH)
    contents = [c["fragment_data"]["content"] for c in chunks]
    assert not any("Выясните" in t or "Вспомните из курс" in t or "Напишите сочинение" in t for t in contents)
    inset = next(c for c in chunks if "Госуслуги» в 2009" in c["fragment_data"]["content"])
    assert inset["fragment_data"]["block_type"] == "inset"
    assert inset["fragment_data"]["page_start"] == 5
    assert "Интересно" in inset["fragment_data"]["combined_text"].split("\n\n", 1)[0]
    body = next(c for c in chunks if "Активная жизненная позиция" in c["fragment_data"]["content"])
    assert body["fragment_data"]["block_type"] == "body"
    assert body["fragment_data"]["page_start"] in (1, 2)
    assert stats["dropped_blocks"]["exercise"]["count"] >= 3
    assert stats["chunks_by_block_type"]["inset"]["count"] >= 3


# ─── история России 11 (Мединский) ────────────────────────────────────


@needs_medinsky
def test_medinsky_profile_and_bio_inset():
    blocks, prof, chunks, stats = _chunks(MEDINSKY)
    assert prof.body_family == "NewtonSanPin" and prof.body_size == pytest.approx(11.0)
    assert prof.body_x0 == pytest.approx(57.0) and prof.indent_x0 == pytest.approx(74.0)
    tito = next(b for b in blocks if b.kind == "inset" and "Иосип Броз Тито" in b.text)
    assert "Узнайте" not in tito.text
    contents = [c["fragment_data"]["content"] for c in chunks]
    assert not any("⬤" in t or "Узнайте" in t or "Перечислите категории" in t for t in contents)
    assert any("Федеративная Республика Германии" in t for t in contents)
    assert stats["dropped_blocks"]["exercise"]["count"] >= 3


# ─── интеграция с process_document_to_chunks ──────────────────────────


@needs_obsch
def test_process_document_layout_method_reports_engine():
    stats = {}
    with patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO):
        chunks, _info = chunker_mod.process_document_to_chunks(
            str(OBSCH), min_size=30, document_kind="textbook", stats=stats, chunking_method="layout"
        )
    assert chunks
    assert stats["chunking_method"] == "layout"
    assert stats["chunking_engine"] == "layout_pdf"
    assert stats["chunking_fallback"] is None
    assert any(c["fragment_data"]["block_type"] == "inset" for c in chunks)


def test_process_document_layout_method_falls_back_for_docx(tmp_path):
    doc = tmp_path / "book.docx"
    doc.write_bytes(b"PK")
    tree = {"title": "Глава 1", "level": 1, "content": "Текст главы учебника. " * 20, "children": []}
    stats = {}
    with (
        patch.object(chunker_mod, "extract_outline_from_document", return_value=tree),
        patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO),
    ):
        chunks, _info = chunker_mod.process_document_to_chunks(
            str(doc), min_size=20, document_kind="textbook", stats=stats, chunking_method="layout"
        )
    assert chunks
    assert stats["chunking_engine"] == "hierarchical_outline"
    assert stats["chunking_fallback"] is None  # not a PDF: layout never attempted, rules are the engine


# ─── подзаголовки, колонки, страницы (тикет 71372790) ─────────────────


def _line(text, x0=57.0, x1=400.0, top=100.0, size=11.0, family="NewtonSanPin", page=1, fill_id=None, styled=None):
    runs = [(family, size, text.replace(" ", ""))]
    return tl.Line(
        page=page, x0=x0, x1=x1, top=top, bottom=top + size, text=text, family=family, size=size,
        bold=False, italic=False, runs=runs, fill_id=fill_id, color="0.00,0.00,0.00",
        styled_runs=styled or [(family, size, False, "0.00,0.00,0.00", text.replace(" ", ""))],
    )


def _profile():
    return tl.Profile("NewtonSanPin", 11.0, 0.6, 57.0, 74.0, {"ReformaGrotesk"}, "0.00,0.00,0.00")


def test_runin_heading_detected_from_styled_lead():
    styled = [
        ("TextBookSanPin", 10.0, True, "1.00,1.00,1.00", "4"),
        ("TextBookSanPin", 11.0, True, "0.86,0.26,0.25", "Восстановлениепромышленности."),
        ("NewtonSanPin", 11.0, False, "0.00,0.00,0.00", "Задачиповосстановлениюиразвитию"),
    ]
    line = _line("4 Восстановление промышленности. Задачи по восстановлению и развитию", styled=styled)
    assert tl._runin_heading(line, _profile()) == "4 Восстановление промышленности"
    # Обычная строка основного текста — не подзаголовок.
    assert tl._runin_heading(_line("Обычный абзац основного текста без выделения."), _profile()) == ""
    # Выделена почти вся строка (цитата жирным) — тоже нет.
    quote = [("NewtonSanPin", 11.0, True, "0.00,0.00,0.00", "1Этоцитатажирнымнавсюстроку."),
             ("NewtonSanPin", 11.0, False, "0.00,0.00,0.00", "Да.")]
    assert tl._runin_heading(_line("1 Это цитата жирным на всю строку. Да.", styled=quote), _profile()) == ""


def test_runin_heading_can_be_disabled_by_env(monkeypatch):
    styled = [("TextBookSanPin", 11.0, True, "0.86,0.26,0.25", "1ВлияниеПобеды."),
              ("NewtonSanPin", 11.0, False, "0.00,0.00,0.00", "СССРпобедилвсамойкровопролитнойвойне")]
    line = _line("1 Влияние Победы. СССР победил в самой кровопролитной войне", styled=styled)
    line.cls = "body"
    monkeypatch.setenv("CHUNKER_LAYOUT_RUNIN_HEADINGS", "0")
    assert tl.mark_runin_headings([line], _profile()) == 0 and line.runin == ""
    monkeypatch.setenv("CHUNKER_LAYOUT_RUNIN_HEADINGS", "1")
    assert tl.mark_runin_headings([line], _profile()) == 1 and line.runin == "1 Влияние Победы"


def test_group_blocks_opens_inline_heading_before_runin_paragraph():
    prof = _profile()
    a = _line("Предыдущий абзац заканчивается точкой.", top=100)
    b = _line("1 Влияние Победы. СССР победил в войне.", top=113)
    b.runin = "1 Влияние Победы"
    c = _line("и продолжение абзаца на следующей строке.", top=126)
    for l in (a, b, c):
        l.cls = "body"
    blocks = [tl.classify_block(x, prof) for x in tl.group_blocks([a, b, c], prof)]
    kinds = [(x.kind, x.inline) for x in blocks]
    assert kinds == [("body", False), ("heading", True), ("body", False)], kinds
    assert blocks[1].title == "1 Влияние Победы" and blocks[1].level == 3
    assert blocks[2].text.startswith("1 Влияние Победы. СССР победил")
    tree = tl.build_tree(blocks)
    assert [n["title"] for n in tree["children"]] == ["1 Влияние Победы"]
    assert tree["children"][0]["content"].startswith("1 Влияние Победы.")


def test_heading_inside_sentence_is_dropped_and_paragraph_rejoined():
    prof = _profile()
    a = _line("Текст абзаца обрывается на середине", top=100, page=1)
    h = _line("РУБРИКА ВЁРСТКИ", top=20, page=2, size=14.0, family="ReformaGrotesk")
    h.cls = "heading"
    b = _line("предложения и продолжается на следующей странице.", top=40, page=2)
    for l in (a, b):
        l.cls = "body"
    stats = {}
    blocks = tl._merge_sentence_across_heading(tl.group_blocks([a, h, b], prof), stats)
    assert [x.kind for x in blocks] == ["body"]
    assert [l.text for l in blocks[0].lines] == [a.text, b.text]
    assert stats["dropped_blocks"]["junk"]["count"] == 1
    # Структурный заголовок («§ 2. …») так не выбрасывается.
    h2 = _line("§ 2. Новый параграф", top=20, page=2, size=14.0, family="ReformaGrotesk")
    h2.cls = "heading"
    blocks = tl._merge_sentence_across_heading(tl.group_blocks([a, h2, b], prof), {})
    assert [x.kind for x in blocks] == ["body", "heading", "body"]


def test_decorated_running_header_is_junk_regardless_of_font():
    prof = _profile()
    assert tl.classify_line(_line("8 ГЛАВА I", top=10.0), prof, (459.0, 601.0)) == "junk"
    assert tl.classify_line(_line("§ 1. Восстановление экономики 7", top=10.0), prof, (459.0, 601.0)) == "junk"
    assert tl.classify_line(_line("8 ГЛАВА I", top=300.0), prof, (459.0, 601.0)) == "body"


def test_split_line_columns_by_fill_and_gutter():
    def ch(text, x0, size=9.5):
        return {"text": text, "x0": x0, "x1": x0 + 5.0, "top": 100.0, "bottom": 109.5, "size": size}
    fills = [{"x0": 37, "x1": 230, "top": 90, "bottom": 200}, {"x0": 230, "x1": 422, "top": 90, "bottom": 200}]
    left = [ch(c, 45 + i * 6) for i, c in enumerate("1946 г. — реформа")]
    right = [ch(c, 246 + i * 6) for i, c in enumerate("1945 г. — выборы")]
    tl_line = {"x0": 45, "x1": 350, "top": 100, "bottom": 109.5, "text": "1946 г. — реформа 1945 г. — выборы", "chars": left + right}
    parts = tl._split_line_columns(tl_line, fills)
    assert [p_["text"] for p_ in parts] == ["1946 г. — реформа", "1945 г. — выборы"]
    # Вне заливок строка не делится, даже с широким пробелом.
    assert len(tl._split_line_columns(tl_line, [])) == 1
    # Ярлык в одно слово рядом с текстом — не колонка.
    label = [ch(c, 45 + i * 6) for i, c in enumerate("ИНТЕРЕСНО")]
    text = [ch(c, 140 + i * 6) for i, c in enumerate("Госуслуги появились")]
    one_fill = [{"x0": 37, "x1": 422, "top": 90, "bottom": 200}]
    assert len(tl._split_line_columns({"x0": 45, "x1": 260, "top": 100, "bottom": 109.5, "text": "x", "chars": label + text}, one_fill)) == 1


def test_order_columns_reads_side_by_side_fills_in_turn():
    l1 = _line("левая 1", x0=45, x1=200, top=100, fill_id=0)
    r1 = _line("правая 1", x0=246, x1=400, top=100, fill_id=1)
    l2 = _line("левая 2", x0=45, x1=200, top=112, fill_id=0)
    r2 = _line("правая 2", x0=246, x1=400, top=112, fill_id=1)
    body = _line("основной текст после врезки.", top=300)
    ordered = tl._order_columns([l1, r1, l2, r2, body])
    assert [l.text for l in ordered] == ["левая 1", "левая 2", "правая 1", "правая 2", "основной текст после врезки."]
    # Две колонки внутри одной заливки.
    a1 = _line("кол 1 строка 1", x0=45, x1=200, top=100, fill_id=3)
    b1 = _line("кол 2 строка 1", x0=246, x1=400, top=100, fill_id=3)
    a2 = _line("кол 1 строка 2", x0=45, x1=200, top=112, fill_id=3)
    b2 = _line("кол 2 строка 2", x0=246, x1=400, top=112, fill_id=3)
    assert [l.text for l in tl._order_columns([a1, b1, a2, b2])] == [a1.text, a2.text, b1.text, b2.text]


def test_assign_unit_pages_follows_paragraphs():
    units = [
        {"text": "Первый абзац узла.\n\nВторой абзац узла.", "block_type": "body"},
        {"text": "Третий абзац — хвост после разреза по предложениям", "block_type": "body"},
        {"text": "продолжение третьего абзаца без своего заголовка.", "block_type": "body"},
    ]
    para_pages = [
        {"head": "Первыйабзацузла.", "page_start": 7, "page_end": 7},
        {"head": "Второйабзацузла.", "page_start": 8, "page_end": 8},
        {"head": "Третийабзац—хвостпослеразрезапопредложе", "page_start": 9, "page_end": 10},
    ]
    chunker_mod._assign_unit_pages(units, para_pages)
    assert (units[0]["page_start"], units[0]["page_end"]) == (7, 8)
    assert (units[1]["page_start"], units[1]["page_end"]) == (9, 10)
    assert (units[2]["page_start"], units[2]["page_end"]) == (10, 10)


def _slice_pdf(src, pages, out_dir, name):
    """Страницы (нумерация с 1, можно вразброс) из PDF → временный файл."""
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    out = Path(out_dir) / name
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def _slice_chunks(tmp_path_factory, src, pages, name):
    if not Path(src).is_file():
        pytest.skip("full textbook missing")
    sliced = _slice_pdf(src, pages, tmp_path_factory.mktemp("slices"), name)
    return _chunks(sliced)


@pytest.fixture(scope="module")
def medinsky_6_14(tmp_path_factory):
    return _slice_chunks(tmp_path_factory, MEDINSKY_FULL, range(6, 15), "medinsky_6_14.pdf")


@needs_medinsky_full
def test_medinsky_full_slice_runin_headings_columns_and_pages(medinsky_6_14):
    """Стр. 6–14 полного учебника: подзаголовки «1 …»–«4 …», колонки «РОССИЯ | МИР», страницы по чанкам."""
    blocks, _prof, chunks, stats = medinsky_6_14
    inline = [b.title for b in blocks if b.kind == "heading" and b.inline]
    assert inline == [
        "1 Влияние Победы",
        "2 Потери. Демография",
        "3 Социальная адаптация фронтовиков. Борьба с беспризорностью и преступностью",
        "4 Восстановление промышленности",
    ], inline
    bodies = [c for c in chunks if c["fragment_data"]["block_type"] == "body"]
    assert any(c["fragment_data"]["content"].startswith("1 Влияние Победы.") for c in bodies)
    assert any(c["fragment_data"]["content"].startswith("4 Восстановление промышленности.") for c in bodies)
    assert any(c["fragment_data"]["title"] == "4 Восстановление промышленности" for c in bodies)
    four = next(c for c in bodies if c["fragment_data"]["title"] == "4 Восстановление промышленности")
    assert "4 Восстановление промышленности" in four["fragment_data"]["combined_text"].split("\n\n", 1)[0]
    # Страницы у чанков свои, а не весь диапазон узла.
    ranges = {(c["fragment_data"]["page_start"], c["fragment_data"]["page_end"]) for c in bodies}
    assert len(ranges) >= 4, ranges
    assert all(ps is not None and pe >= ps for ps, pe in ranges)
    # Двухколоночная хронология «РОССИЯ | МИР» читается по колонкам.
    insets = [b.text for b in blocks if b.kind == "inset"]
    russia = next(t for t in insets if "преобразование наркома" in t)
    assert "наркоматов в министерства" in russia.replace("-\n", "").replace("\n", " ")
    assert "поражение на выборах" not in russia
    world = next(t for t in insets if "поражение на выборах" in t)
    assert "Лейбористской партии" in world.replace("\n", " ") and "наркома" not in world
    # Короткие подписи-врезки не становятся чанками, задания вырезаны.
    assert not any(len(c["fragment_data"]["content"]) < tl.INSET_SHORT_MIN_CHARS for c in chunks if c["fragment_data"]["block_type"] == "inset")
    assert not any("⬤" in c["fragment_data"]["content"] or "Узнайте" in c["fragment_data"]["content"] for c in chunks)
    assert stats["layout"]["runin_headings"] == 4


# ─── заголовки врезок, главы, run-in, колонки (тикет 71901445) ────────

OBSCH10 = _DATA / "obsestvoznanie-10-klass-medvedev.pdf"
needs_obsch10 = pytest.mark.skipif(not OBSCH10.is_file(), reason="obsch10 textbook missing")


def _fd(c):
    return c["fragment_data"]


def _path(c):
    return [p["title"] for p in _fd(c)["hierarchy_context"]["breadcrumb_path"]]


def _flat(text):
    return " ".join((text or "").replace("-\n", "").split())


def _walk_nodes(node):
    yield node
    for ch in node.get("children") or []:
        yield from _walk_nodes(ch)


@needs_medinsky_full
def test_medinsky_6_14_inset_title_chapter_and_poster_caption(medinsky_6_14):
    blocks, _prof, chunks, stats = medinsky_6_14
    # «РОССИЯ МИР» в заливке — заголовок врезки, а не раздела.
    assert any(b.kind == "inset" and b.title == "РОССИЯ МИР" for b in blocks)
    assert not any(b.kind == "heading" and "РОССИЯ" in b.title for b in blocks)
    bodies = [c for c in chunks if _fd(c)["block_type"] == "body"]
    assert bodies and not any("РОССИЯ МИР" in t for c in bodies for t in _path(c))
    assert stats["layout"]["inset_titles"] >= 1
    # «ГЛАВА СССР …» + отдельная строка «I» → один заголовок первого уровня.
    chapter = next(b for b in blocks if b.kind == "heading" and b.title.startswith("Глава I "))
    assert chapter.title.startswith("Глава I СССР В 1945—1991 гг") and chapter.level == 1
    assert blocks.index(chapter) == 0
    assert stats["layout"]["chapters_synthesized"] == 1
    section = next(b for b in blocks if b.kind == "heading" and b.title.startswith("§ 1 "))
    assert section.level == 2
    assert all(_path(c)[:2] == [chapter.title, section.title] for c in bodies)
    # Строка контекста первого body-чанка: глава с точкой после «гг.».
    first_ctx = _fd(bodies[0])["combined_text"].split("\n\n", 1)[0]
    assert first_ctx == (
        "Глава I СССР В 1945—1991 гг. > § 1 Восстановление и развитие экономики и социальной сферы"
        " > 1 Влияние Победы"
    ), first_ctx
    # Подпись к плакату — отдельный caption, не склеена с соседними блоками.
    poster = [c for c in chunks if "Плакат «Отстроим" in _fd(c)["content"]]
    assert len(poster) == 1 and _fd(poster[0])["block_type"] == "caption"
    assert _flat(_fd(poster[0])["content"]) == "Плакат «Отстроим на славу!». 1945 г."


@pytest.fixture(scope="module")
def medinsky_18_24(tmp_path_factory):
    # с. 7 — § 1 (без него «ПОДВЕДЁМ ИТОГИ» остался бы level 2), 18–24 — итоги
    # § 1, § 2, Вознесенский; 21, 29, 38 — «Дополнительные материалы» (фильмы).
    pages = [7, *range(18, 25), 29, 38]
    return _slice_chunks(tmp_path_factory, MEDINSKY_FULL, pages, "medinsky_18_24.pdf")


@needs_medinsky_full
def test_medinsky_summary_heading_is_level3_and_next_section_not_under_it(medinsky_18_24):
    blocks, _prof, chunks, stats = medinsky_18_24
    summary = next(b for b in blocks if b.kind == "heading" and b.title == "ПОДВЕДЁМ ИТОГИ")
    assert summary.level == 3
    assert stats["layout"]["heading_levels_demoted"] >= 1
    tree = tl.build_tree(blocks)
    s1 = next(n for n in tree["children"] if n["title"].startswith("§ 1 "))
    s2 = next(n for n in tree["children"] if n["title"].startswith("§ 2 "))
    node = next(n for n in s1["children"] if n["title"] == "ПОДВЕДЁМ ИТОГИ")
    assert any(ch.get("block_type") == "inset" and "мобилизационный характер" in ch["content"] for ch in node["children"])
    assert not any(n["title"].startswith("§") for n in _walk_nodes(node))
    assert s2["level"] == 2
    summary_chunk = next(c for c in chunks if "мобилизационный характер" in _fd(c)["content"])
    assert _fd(summary_chunk)["block_type"] == "inset" and _path(summary_chunk)[-1] == "ПОДВЕДЁМ ИТОГИ"
    assert not any("ПОДВЕДЁМ ИТОГИ" in _path(c) for c in chunks if any(t.startswith("§ 2") for t in _path(c)))


@needs_medinsky_full
def test_medinsky_bio_inset_columns_runin_wrapped_and_apparatus(medinsky_18_24):
    blocks, _prof, chunks, stats = medinsky_18_24
    bio = next(b for b in blocks if b.kind == "inset" and b.title == "Вознесенский Николай Алексеевич")
    text = _flat(bio.text)
    # Шапка (имя, годы) идёт первой, затем левая колонка, затем правая.
    assert "Вознесенский Николай Алексеевич (1903—1950) В 1938—1949 гг. возглавлял Госплан СССР." in text
    after_name = text.split("Алексеевич ", 1)[1]
    assert after_name.startswith("(1903—1950) В 1938—1949 гг. возглавлял Госплан СССР")
    assert text.index("1-й заместитель председателя Совнаркома") < text.index("«ленинградскому делу»")
    bio_chunk = next(c for c in chunks if "возглавлял Госплан" in _fd(c)["content"])
    assert _fd(bio_chunk)["block_type"] == "inset" and _fd(bio_chunk)["title"] == bio.title
    # Абзац с с. 23 продолжается на с. 24 — не рвётся на «партийного контроля…».
    bodies = [c for c in chunks if _fd(c)["block_type"] == "body"]
    assert not any(_fd(c)["content"].lstrip()[:1].islower() for c in bodies if "партийного контроля" in _fd(c)["content"])
    assert any("Система жёсткого\nпартийного контроля" in _fd(c)["content"] for c in bodies)
    # Подзаголовок на всю строку с переносом «взаимо-/отношений».
    inline = [b.title for b in blocks if b.kind == "heading" and b.inline]
    assert "2 Союзный Центр и национальные регионы: проблемы взаимоотношений" in inline
    # «Дополнительные материалы» со списком фильмов выброшены на всех трёх страницах.
    assert stats["dropped_blocks"]["apparatus"]["count"] >= 3
    assert not any("Худ. фильм" in _fd(c)["content"] for c in chunks)
    assert not any("ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ" in t for c in chunks for t in _path(c))


@pytest.fixture(scope="module")
def medinsky_mixed(tmp_path_factory):
    # 46 и 401 — run-in с «:» и «?», 86–87 — биографии у портретов, 120 — стихотворение
    # в две колонки, 278 — страница главы II, 406 — врезка с перемешанными колонками.
    pages = [46, 86, 87, 120, 278, 401, 406]
    return _slice_chunks(tmp_path_factory, MEDINSKY_FULL, pages, "medinsky_mixed.pdf")


@needs_medinsky_full
def test_medinsky_runin_with_colon_and_question(medinsky_mixed):
    blocks, _prof, _chunks_, _stats = medinsky_mixed
    inline = [b.title for b in blocks if b.kind == "heading" and b.inline]
    assert "3 Начало холодной войны: её причины и особенности" in inline
    assert "10 Минские соглашения. Что это было?" in inline


@needs_medinsky_full
def test_medinsky_portrait_bios_are_insets_not_captions(medinsky_mixed):
    blocks, _prof, chunks, _stats = medinsky_mixed
    for name in ("Тамм Игорь Евгеньевич", "Франк Илья Михайлович"):
        b = next(x for x in blocks if x.title == name)
        assert b.kind == "inset", (name, b.kind)
        c = next(x for x in chunks if _fd(x)["title"] == name)
        assert _fd(c)["block_type"] == "inset"


@needs_medinsky_full
def test_medinsky_poem_read_by_columns_not_mixed(medinsky_mixed):
    blocks, _prof, _chunks_, _stats = medinsky_mixed
    poem = next(b for b in blocks if "Я люблю тебя, Жизнь" in (b.text or ""))
    assert poem.kind == "inset"
    text = poem.text
    assert "Я люблю тебя, Жизнь,\nЧто само по себе и не ново," in text
    assert text.index("Что само по себе") < text.index("Как поют соловьи") < text.index("И надеюсь, что это взаимно")
    assert text.index("Жизнь, ты знаешь, что это такое") < text.index("Как поют соловьи")
    # Строки соседних колонок не склеены в одну.
    assert "Жизнь, причалы" not in text and "покоя! В трубных" not in text


@needs_medinsky_full
def test_medinsky_chapter_two_and_mixed_inset_dropped(medinsky_mixed):
    blocks, _prof, chunks, stats = medinsky_mixed
    ch2 = next(b for b in blocks if b.kind == "heading" and b.title.startswith("Глава II"))
    assert ch2.title == "Глава II РОССИЙСКАЯ ФЕДЕРАЦИЯ В 1992 — НАЧАЛЕ 2020-х гг." and ch2.level == 1
    assert any(b.kind == "inset_mixed" for b in blocks)
    assert stats["dropped_blocks"]["inset_mixed"]["count"] >= 1
    assert not any("Штаб-квартира компании «Яндекс»" in _fd(c)["content"] for c in chunks)


@needs_medinsky_full
def test_medinsky_long_scholar_opinion_inset_is_one_chunk(tmp_path_factory):
    _blocks, _prof, chunks, _stats = _slice_chunks(tmp_path_factory, MEDINSKY_FULL, [143], "medinsky_143.pdf")
    insets = [c for c in chunks if _fd(c)["block_type"] == "inset"]
    assert len(insets) == 1, [len(_fd(c)["content"]) for c in insets]
    assert _fd(insets[0])["title"] == "МНЕНИЕ УЧЁНОГО"
    assert len(_fd(insets[0])["content"]) == 2113


@needs_obsch10
def test_obsch10_qr_caption_with_extra_materials_dropped(tmp_path_factory):
    _blocks, _prof, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH10, [186], "obsch10_186.pdf")
    assert stats["dropped_blocks"]["apparatus"]["count"] >= 1
    assert not any("Дополнительные материалы" in _fd(c)["content"] for c in chunks)
    assert not any("денежно-кредитная политика Банка России." in _fd(c)["content"] for c in chunks)


@needs_obsch10
def test_obsch10_glossary_insets_become_entries(tmp_path_factory):
    _blocks, _prof, chunks, _stats = _slice_chunks(tmp_path_factory, OBSCH10, range(310, 322), "obsch10_310_321.pdf")
    entries = [c for c in chunks if _fd(c)["block_type"] == "glossary_entry"]
    assert len(entries) >= 50, len(entries)
    assert not any(_fd(c)["block_type"] in ("list", "inset") for c in chunks)
    assert max(len(_fd(c)["content"]) for c in entries) <= chunker_mod.TEXTBOOK_MAX_CHARS


@needs_obsch10
def test_obsch10_regression_no_runin_no_mixed(tmp_path_factory):
    blocks, _prof, _chunks_, stats = _slice_chunks(tmp_path_factory, OBSCH10, range(8, 34), "obsch10_8_33.pdf")
    assert not any(b.kind == "heading" and b.inline for b in blocks)
    assert stats["layout"]["runin_headings"] == 0
    assert not any(b.kind == "inset_mixed" for b in blocks)
    assert "inset_mixed" not in (stats.get("dropped_blocks") or {})


# ─── юнит-тесты L1 на синтетических строках ──────────────────────────

_RED = "0.86,0.26,0.25"
_BLACK = "0.00,0.00,0.00"


def _hl(text, *, size=14.0, family="ReformaGrotesk", top=100.0, fill_id=None, cls="heading", page=1, x0=57.0, x1=400.0, bold=False):
    line = _line(text, x0=x0, x1=x1, top=top, size=size, family=family, page=page, fill_id=fill_id)
    line.cls = cls
    line.bold = bold
    return line


def test_demote_fill_headings_inset_title_becomes_bg():
    prof = _profile()
    head = _hl("Вознесенский", size=12.0, fill_id=1, top=100)
    body = [_hl(f"строка текста врезки номер {i}", size=9.5, family="NewtonSanPin", fill_id=1, top=115 + 11 * i, cls="bg")
            for i in range(3)]
    lines = [head, *body]
    assert tl._demote_fill_headings(lines, prof) == 1
    assert head.cls == "bg" and head.inset_title is True
    assert all(not l.inset_title for l in body)


def test_demote_fill_headings_keeps_structural_and_summary_headings():
    prof = _profile()
    # «§ 2 …» на цветной плашке — раздел, даже если в плашке есть текст.
    sec = _hl("§ 2 Политическая система в послевоенные годы", fill_id=2, top=100)
    sec_txt = _hl("подпись на плашке мелким кеглем", size=9.5, family="NewtonSanPin", fill_id=2, top=120, cls="bg")
    # «ПОДВЕДЁМ ИТОГИ» с текстом основного кегля под ней — раздел.
    summary = _hl("ПОДВЕДЁМ ИТОГИ", fill_id=3, top=200)
    summary_txt = _hl("После войны экономика СССР сохраняла мобилизационный характер.", size=11.0,
                      family="NewtonSanPin", fill_id=3, top=220, cls="bg")
    # Плашка, где кроме заголовка ничего нет.
    alone = _hl("ВОПРОСЫ К ГЛАВЕ ПЕРВОЙ", fill_id=4, top=300)
    # Рубрика-аппарат: от неё зависит, что выбрасывается следом.
    tasks = _hl("Вопросы и задания", fill_id=5, top=400)
    tasks_txt = _hl("1. Охарактеризуйте состояние хозяйства.", size=9.5, family="NewtonSanPin", fill_id=5, top=420, cls="bg")
    lines = [sec, sec_txt, summary, summary_txt, alone, tasks, tasks_txt]
    assert tl._demote_fill_headings(lines, prof) == 0
    assert [l.cls for l in (sec, summary, alone, tasks)] == ["heading"] * 4
    # Тот же капс, но под ним мелкий текст врезки — заголовок врезки.
    caps = _hl("МНЕНИЕ УЧЁНОГО", fill_id=6, top=500)
    caps_txt = _hl("«Да, в последние несколько лет своей жизни…»", size=9.5, family="NewtonSanPin", fill_id=6, top=520, cls="bg")
    assert tl._demote_fill_headings([caps, caps_txt], prof) == 1 and caps.inset_title


def test_demote_fill_headings_keeps_continuation_of_heading():
    prof = _profile()
    first = _hl("§ 3 Идеология, наука, культура", fill_id=7, top=100)
    second = _hl("и спорт", fill_id=7, top=100 + 14 + 2)
    txt = _hl("текст плашки мелким кеглем", size=9.5, family="NewtonSanPin", fill_id=7, top=150, cls="bg")
    assert tl._demote_fill_headings([first, second, txt], prof) == 0
    assert second.cls == "heading"


def test_synthesize_chapters_joins_word_number_and_title():
    prof = _profile()
    word = _hl("ГЛАВА СССР В 1945—1991 гг.", size=24.0, top=100)
    num = _hl("I", size=60.0, top=130, x0=57.0, x1=80.0)
    body = _hl("Советский космонавт Юрий Гагарин перед стартом", size=11.0, family="NewtonSanPin", top=300, cls="body")
    lines = [word, num, body]
    assert tl._synthesize_chapters(lines, prof) == 1
    assert word.text == "Глава I СССР В 1945—1991 гг." and word.cls == "heading"
    assert num.cls == "junk" and body.cls == "body"
    assert tl._heading_level(word.text, word.size, prof) == 1


def test_synthesize_chapters_collects_title_continuation_and_skips_numbered():
    prof = _profile()
    word = _hl("ГЛАВА РОССИЙСКАЯ ФЕДЕРАЦИЯ", size=24.0, top=100, x0=150)
    cont = _hl("В 1992 — НАЧАЛЕ 2020-х гг.", size=24.0, top=100 + 24 + 4, x0=150)
    num = _hl("II", size=60.0, top=110, x0=57.0, x1=100.0)
    assert tl._synthesize_chapters([word, cont, num], prof) == 1
    assert word.text == "Глава II РОССИЙСКАЯ ФЕДЕРАЦИЯ В 1992 — НАЧАЛЕ 2020-х гг."
    assert cont.cls == "junk" and num.cls == "junk"
    # Уже нумерованная глава и страница без номера не трогаются.
    done = _hl("Глава I. Человек и общество", size=24.0, top=100)
    num2 = _hl("1", size=60.0, top=130)
    assert tl._synthesize_chapters([done, num2], prof) == 0 and done.text == "Глава I. Человек и общество"
    lonely = _hl("ГЛАВА БЕЗ НОМЕРА", size=24.0, top=100)
    assert tl._synthesize_chapters([lonely], prof) == 0 and lonely.text == "ГЛАВА БЕЗ НОМЕРА"


def _styled_line(text, runs, top=100.0, page=1):
    line = _line(text, top=top, page=page, styled=runs)
    line.cls = "body"
    return line


def test_runin_wrapped_heading_across_lines():
    prof = _profile()
    first = _styled_line(
        "2 Союзный Центр и национальные регионы: проблемы взаимо-",
        [("TextBookSanPin", 11.0, True, _RED, "2СоюзныйЦентринациональныерегионы:проблемывзаимо-")],
    )
    second = _styled_line(
        "отношений. В национальной политике особый акцент был сделан",
        [("TextBookSanPin", 11.0, True, _RED, "отношений."),
         ("NewtonSanPin", 11.0, False, _BLACK, "Внациональнойполитикеособыйакцентбылсделан")],
        top=113.0,
    )
    title = "2 Союзный Центр и национальные регионы: проблемы взаимоотношений"
    assert tl._runin_wrapped(first, second, prof) == title
    assert tl.mark_runin_headings([first, second], prof) == 1 and first.runin == title and second.runin == ""
    # Перенос на следующую страницу тоже допустим.
    second_p2 = _styled_line(second.text, second.styled_runs, top=40.0, page=2)
    assert tl._runin_wrapped(first, second_p2, prof) == title
    # Название уместилось в строку и кончается точкой — следующая строка обычная.
    whole = _styled_line("3 Начало холодной войны: её причины и особенности.",
                         [("TextBookSanPin", 11.0, True, _RED, "3Началохолоднойвойны:еёпричиныиособенности.")])
    plain = _styled_line("В исторической науке холодную войну трактуют как",
                         [("NewtonSanPin", 11.0, False, _BLACK, "Висторическойнаукехолоднуювойнутрактуюткак")], top=113.0)
    assert tl._runin_wrapped(whole, plain, prof) == "3 Начало холодной войны: её причины и особенности"


def test_runin_wrapped_requires_distinct_font_not_only_bold():
    prof = _profile()
    first = _styled_line(
        "1 Это цитата, набранная жирным основным шрифтом на всю ши-",
        [("NewtonSanPin", 11.0, True, _BLACK, "1Этоцитата,набраннаяжирнымосновнымшрифтомнавсюши-")],
    )
    second = _styled_line(
        "рину строки. Дальше обычный текст абзаца",
        [("NewtonSanPin", 11.0, True, _BLACK, "рину строки."),
         ("NewtonSanPin", 11.0, False, _BLACK, "Дальшеобычныйтекстабзаца")],
        top=113.0,
    )
    assert tl._runin_wrapped(first, second, prof) == ""
    # Следующая строка далеко (другой абзац) — тоже нет.
    red = _styled_line("2 Потери. Демография", [("TextBookSanPin", 11.0, True, _RED, "2Потери.Демография")])
    far = _styled_line("Дальше обычный текст.", [("NewtonSanPin", 11.0, False, _BLACK, "Дальшеобычныйтекст.")], top=200.0)
    assert tl._runin_wrapped(red, far, prof) == ""
    assert tl._runin_wrapped(red, None, prof) == ""


def test_runin_title_shapes():
    assert tl._runin_title("1. Влияние Победы.") == "1. Влияние Победы"
    assert tl._runin_title("10 Минские соглашения. Что это было?") == "10 Минские соглашения. Что это было?"
    assert tl._runin_title("2 млн") == ""                       # мало букв
    assert tl._runin_title("1 Начало перечисления,") == ""      # обрыв на запятой
    assert tl._runin_title("1 " + "Очень длинное название " * 10) == ""  # > RUNIN_TITLE_MAX_CHARS
    assert tl._runin_title("влияние Победы.") == ""


def test_is_mixed_columns_alternating_vs_column_order():
    l1 = _line("левая колонка строка 1", x0=45, x1=200, top=100, size=9.5, fill_id=1)
    r1 = _line("правая колонка строка 1", x0=246, x1=400, top=100, size=9.5, fill_id=1)
    l2 = _line("левая колонка строка 2", x0=45, x1=200, top=112, size=9.5, fill_id=1)
    r2 = _line("правая колонка строка 2", x0=246, x1=400, top=112, size=9.5, fill_id=1)
    assert tl._is_mixed_columns([l1, r1, l2, r2]) is True
    assert tl._is_mixed_columns([l1, l2, r1, r2]) is False
    assert tl._is_mixed_columns([l1, l2]) is False


def test_is_mixed_columns_marks_block_inset_mixed_and_build_tree_drops_it():
    prof = _profile()
    rows = []
    for i in range(3):
        rows.append(_line(f"Левая колонка, строка номер {i}, длинная.", x0=45, x1=200, top=100 + 12 * i, size=9.5, fill_id=1))
        rows.append(_line(f"Правая колонка, строка номер {i}, длинная.", x0=246, x1=400, top=100 + 12 * i, size=9.5, fill_id=1))
    for r in rows:
        r.cls = "bg"
    block = tl.classify_block(tl.Block("aux", rows), prof)
    assert block.kind == "inset_mixed"
    stats = {}
    tree = tl.build_tree([block], stats)
    assert tree["children"] == [] and stats["dropped_blocks"]["inset_mixed"]["count"] == 1
    assert tl.layout_blocks_to_paragraphs([block]) == []


def test_columns_in_fill_large_head_goes_first():
    head = _line("Вознесенский", x0=45, x1=150, top=90, size=12.0, fill_id=1)
    left = [_line(f"левая {i}", x0=45, x1=200, top=110 + 12 * i, size=9.5, fill_id=1) for i in range(3)]
    right = [_line(f"правая {i}", x0=246, x1=400, top=100 + 12 * i, size=9.5, fill_id=1) for i in range(3)]
    cols = tl._columns_in_fill([*right, head, *left])
    assert [[l.text for l in c] for c in cols] == [["Вознесенский"], [l.text for l in left], [l.text for l in right]]
    # Без колонок шапка всё равно первая.
    plain = [_line(f"строка {i}", x0=45, x1=400, top=80 + 12 * i, size=9.5, fill_id=1) for i in range(3)]
    flat = tl._columns_in_fill([*plain, head])
    assert len(flat) == 1 and flat[0][0] is head


def test_columns_in_fill_full_width_line_splits_into_bands():
    top_l = [_line(f"верх левая {i}", x0=45, x1=200, top=100 + 12 * i, size=9.5, fill_id=1) for i in range(2)]
    top_r = [_line(f"верх правая {i}", x0=246, x1=400, top=100 + 12 * i, size=9.5, fill_id=1) for i in range(2)]
    wide = _line("строка во всю ширину врезки", x0=45, x1=400, top=130, size=9.5, fill_id=1)
    bot_l = [_line(f"низ левая {i}", x0=45, x1=200, top=145 + 12 * i, size=9.5, fill_id=1) for i in range(2)]
    bot_r = [_line(f"низ правая {i}", x0=246, x1=400, top=145 + 12 * i, size=9.5, fill_id=1) for i in range(2)]
    members = [top_l[0], top_r[0], top_l[1], top_r[1], wide, bot_l[0], bot_r[0], bot_l[1], bot_r[1]]
    cols = tl._columns_in_fill(members)
    assert len(cols) == 1
    assert [l.text for l in cols[0]] == [l.text for l in (*top_l, *top_r, wide, *bot_l, *bot_r)]


def test_split_line_columns_single_word_part_vs_label():
    def chars(text, x0, size=9.5, font="NewtonSanPin-Regular"):
        return [{"text": c, "x0": x0 + i * 6, "x1": x0 + i * 6 + 5.0, "top": 100.0, "bottom": 109.5,
                 "size": size, "fontname": font} for i, c in enumerate(text)]

    def split(*groups):
        cs = [c for g in groups for c in g]
        return [p_["text"] for p_ in tl._split_line_columns(
            {"x0": 45, "x1": 400, "top": 100, "bottom": 109.5, "text": "x", "chars": cs}, one_fill)]

    one_fill = [{"x0": 37, "x1": 422, "top": 90, "bottom": 200}]
    text = chars("ной войны когда", 246)
    # Имя крупным кеглем в соседней колонке: «Вознесенский | ной войны, когда…» — делится.
    assert split(chars("Вознесенский", 45, size=15.0), text) == ["Вознесенский", "ной войны когда"]
    # Перенос слова слева: «Отечествен- | тирован.» — делится.
    assert split(chars("Отечествен-", 45), chars("тирован.", 246)) == ["Отечествен-", "тирован."]
    # Одно слово тем же шрифтом — растянутый пробел выключки, строку не делит.
    assert len(split(chars("хологическое", 45), chars("манипулирование", 246))) == 1
    # Ярлык капсом и одиночные 1–2 знака строку не делят.
    for lead in ("ИНТЕРЕСНО", "I"):
        assert len(split(chars(lead, 45, size=15.0), text)) == 1, lead
    assert tl._label_like_group(chars("ИНТЕРЕСНО", 45)) and tl._label_like_group(chars("12", 45))
    assert not tl._label_like_group(chars("Вознесенский", 45))


def test_normalize_heading_levels_after_first_section():
    def h(title, level, inline=False):
        return tl.Block("heading", [_line(title, size=14.0)], title=title, level=level, inline=inline)

    blocks = [
        h("КРУПНАЯ РУБРИКА ДО ПАРАГРАФА", 2),
        h("Глава I СССР В 1945—1991 гг.", 1),
        h("§ 1 Восстановление и развитие экономики", 2),
        tl.Block("body", [_line("Текст.")], text="Текст."),
        h("ПОДВЕДЁМ ИТОГИ", 2),
        h("1 Влияние Победы", 2, inline=True),
        h("§ 2 Политическая система", 2),
    ]
    assert tl._normalize_heading_levels(blocks) == 1
    assert [b.level for b in blocks if b.kind == "heading"] == [2, 1, 2, 3, 2, 2]
    tree = tl.build_tree(blocks)
    s1 = next(n for n in _walk_nodes(tree) if n["title"].startswith("§ 1"))
    assert [n["title"] for n in s1["children"]] == ["ПОДВЕДЁМ ИТОГИ"]
    assert "§ 2 Политическая система" not in [n["title"] for n in _walk_nodes(s1)]


def test_heading_level_loose_section_without_dot():
    prof = _profile()
    assert tl._heading_level("§ 1 Восстановление и развитие экономики", 14.0, prof) == 2
    assert tl._heading_level("§ 12—13 Культура и спорт", 14.0, prof) == 2
    assert tl._heading_level("ПОДВЕДЁМ ИТОГИ", 14.0, prof) == 3


def test_inset_title_priority():
    prof = _profile()
    head = _line("Вознесенский", size=12.0, fill_id=1)
    head.inset_title = True
    body = _line("Текст биографии.", size=9.5, fill_id=1, top=120)
    assert tl._inset_title(tl.Block("inset", [head, body]), "ПОРТРЕТ НА ФОНЕ ЭПОХИ", "Вознесенский") == "Вознесенский"
    assert tl._inset_title(tl.Block("inset", [body, body]), "ИНТЕРЕСНО", body.text) == "Интересно"
    bold = _line("Жирное название", size=9.5, fill_id=1)
    bold.bold = True
    assert tl._inset_title(tl.Block("inset", [bold, body]), "", bold.text) == "Жирное название"
    item = _line("• пункт списка", size=9.5, fill_id=1)
    item.bold = True
    assert tl._inset_title(tl.Block("inset", [item, body]), "", item.text) == "Врезка"


# ─── врезки через стык страниц, задания и материалы во врезках ────────


@needs_medinsky_full
def test_medinsky_inset_across_pages_merged_66_71(tmp_path_factory):
    _b, _p, chunks, stats = _slice_chunks(tmp_path_factory, MEDINSKY_FULL, range(66, 72), "medinsky_66_71.pdf")
    # с. 69–70 среза = страницы 4–5.
    hit = [c for c in chunks if "После смерти Сталина" in _fd(c)["content"]]
    assert len(hit) == 1
    fd = _fd(hit[0])
    assert fd["block_type"] == "inset" and fd["content"].startswith("После смерти Сталина")
    assert "передача Крыма" in _flat(fd["content"])
    assert (fd["page_start"], fd["page_end"]) == (4, 5)
    assert stats["inset_merges"]["page_break"]["count"] >= 1


@needs_medinsky_full
def test_medinsky_inset_across_pages_merged_386_391(tmp_path_factory):
    _b, _p, chunks, _stats = _slice_chunks(tmp_path_factory, MEDINSKY_FULL, range(386, 392), "medinsky_386_391.pdf")
    insets = [_fd(c) for c in chunks if _fd(c)["block_type"] == "inset"]
    spans = [(fd["page_start"], fd["page_end"]) for fd in insets]
    # с. 389–390 среза = страницы 4–5: одна врезка, хвост на с. 390 не остался отдельным чанком.
    assert spans.count((4, 5)) == 1, spans
    assert not any(fd["page_start"] == 5 and fd["content"][:1].islower() for fd in insets)


@needs_medinsky_full
@pytest.mark.parametrize("pages", [range(189, 194), range(282, 287)], ids=["189-193", "282-286"])
def test_medinsky_no_numbered_tasks_or_materials_in_chunks(tmp_path_factory, pages):
    from text_utils import find_numbered_task

    _b, _p, chunks, _stats = _slice_chunks(tmp_path_factory, MEDINSKY_FULL, pages, f"medinsky_{pages.start}.pdf")
    assert chunks
    leaks = [_fd(c)["content"][:80] for c in chunks if find_numbered_task(_fd(c)["content"]) is not None]
    assert leaks == []
    assert not any("Статья на сайте" in _fd(c)["content"] for c in chunks)


# ─── итоги главы, глава в верхней полосе, ярлыки врезок (финал L1) ────


@needs_medinsky_full
def test_medinsky_chapter_summary_is_level2_under_chapter(tmp_path_factory):
    # с. 6 — страница главы I (без неё «ИТОГИ ГЛАВЫ» осталась бы level 1), 268–271 — итоги главы.
    blocks, _p, chunks, _s = _slice_chunks(tmp_path_factory, MEDINSKY_FULL, [6, *range(268, 272)], "medinsky_268_271.pdf")
    summary = next(b for b in blocks if b.kind == "heading" and b.title == "ИТОГИ ГЛАВЫ")
    assert summary.level == 2
    bodies = [c for c in chunks if _fd(c)["block_type"] == "body" and _fd(c)["title"] == "ИТОГИ ГЛАВЫ"]
    assert len(bodies) == 2
    for c in bodies:
        assert _path(c) == ["Глава I СССР В 1945—1991 гг."]
        assert _fd(c)["combined_text"].split("\n\n", 1)[0] == "Глава I СССР В 1945—1991 гг. > ИТОГИ ГЛАВЫ"


@needs_obsch10
def test_obsch10_chapter_heading_in_top_band_is_level1(tmp_path_factory):
    blocks, _p, chunks, _s = _slice_chunks(tmp_path_factory, OBSCH10, [128], "obsch10_128.pdf")
    ch = next(b for b in blocks if b.kind == "heading" and b.title.startswith("Глава III"))
    assert ch.title == "Глава III. ЭКОНОМИЧЕСКАЯ ЖИЗНЬ ОБЩЕСТВА" and ch.level == 1
    assert blocks.index(ch) == next(i for i, b in enumerate(blocks) if b.kind == "heading")
    assert any(_path(c)[:1] == [ch.title] for c in chunks)


@needs_obsch10
def test_obsch10_term_inset_title_and_justified_line_not_split(tmp_path_factory):
    blocks, _p, chunks, _s = _slice_chunks(tmp_path_factory, OBSCH10, [167], "obsch10_167.pdf")
    inset = next(b for b in blocks if b.kind == "inset" and "манипулирование" in b.text)
    assert inset.title == "Социальная инженерия"
    assert "психологическое манипулирование" in _flat(inset.text)
    assert not any(l.text.strip() in ("хологическое", "манипулирование") for b in blocks for l in b.lines)
    c = next(x for x in chunks if _fd(x)["block_type"] == "inset")
    assert _fd(c)["title"] == "Социальная инженерия"


@needs_obsch10
def test_obsch10_wise_thoughts_label_inset(tmp_path_factory):
    _b, _p, chunks, _s = _slice_chunks(tmp_path_factory, OBSCH10, [257], "obsch10_257.pdf")
    c = next(x for x in chunks if "в поте" in _fd(x)["content"])
    assert _fd(c)["block_type"] == "inset" and _fd(c)["title"] == "Мудрые мысли"


def test_normalize_heading_levels_chapter_rubric_becomes_level2():
    def h(title, level):
        return tl.Block("heading", [_line(title, size=24.0)], title=title, level=level)

    blocks = [
        h("БОЛЬШАЯ ОБЛОЖКА", 1),                    # до первой главы — не трогаем
        h("Глава I СССР В 1945—1991 гг.", 1),
        h("§ 1 Восстановление экономики", 2),
        h("ПОДВЕДЁМ ИТОГИ", 2),
        h("ИТОГИ ГЛАВЫ", 1),
        h("Глава II РОССИЙСКАЯ ФЕДЕРАЦИЯ", 1),
    ]
    assert tl._normalize_heading_levels(blocks) == 2
    assert [b.level for b in blocks] == [1, 1, 2, 3, 2, 1]
    tree = tl.build_tree(blocks)
    ch1 = next(n for n in tree["children"] if n["title"].startswith("Глава I "))
    assert "ИТОГИ ГЛАВЫ" in [n["title"] for n in ch1["children"]]
    assert any(n["title"].startswith("Глава II") for n in tree["children"])


def test_front_matter_cutoff_handles_empty_and_lineless_blocks():
    assert tl._front_matter_cutoff([]) == 0
    assert tl._front_matter_cutoff([tl.Block("heading", [], title="1 Влияние Победы", level=3, inline=True)]) == 0
    assert tl.build_tree([tl.Block("heading", [], title="1 Влияние Победы", level=3, inline=True)])["children"]


def test_split_line_columns_single_word_on_supported_gutter():
    def chars(text, x0, size=9.5, font="NewtonSanPin-Regular"):
        return [{"text": c, "x0": x0 + i * 6, "x1": x0 + i * 6 + 5.0, "top": 0.0, "bottom": 9.5,
                 "size": size, "fontname": font} for i, c in enumerate(text)]

    def line(left, right, top):
        cs = chars(left, 45) + chars(right, 246)
        for c in cs:
            c["top"], c["bottom"] = top, top + 9.5
        return {"x0": 45, "x1": 400, "top": top, "bottom": top + 9.5, "text": f"{left} {right}", "chars": cs}

    fills = [{"x0": 37, "x1": 422, "top": 90, "bottom": 300}]
    # Две строки стихов в две колонки с одинаковым разрывом — жёлоб подтверждён.
    support = [line("Вот уж окна зажглись,", "Как поют соловьи,", 100),
               line("Я шагаю с работы ус", "Полумрак, поцелуй", 112)]
    target = line("Я люблю тебя, Жизнь,", "причалы.", 124)
    gutters = tl._page_gutters([*support, target], fills)
    assert gutters and len(gutters[0]) >= tl.COLUMN_GUTTER_SUPPORT
    assert [p_["text"] for p_ in tl._split_line_columns(target, fills, gutters)] == ["Я люблю тебя, Жизнь,", "причалы."]
    # Без подтверждённого жёлоба тот же шрифт — растянутый пробел, не колонка.
    assert len(tl._split_line_columns(target, fills, {})) == 1
    assert len(tl._split_line_columns(target, fills, tl._page_gutters([target], fills))) == 1
    # Одна подтверждающая строка — меньше COLUMN_GUTTER_SUPPORT.
    assert len(tl._split_line_columns(target, fills, tl._page_gutters([support[0], target], fills))) == 1


# ─── «Ключевые понятия», капс-вопросы, «§ N» + название ────────────────────

OBSCH9_FULL = _DATA / "obsestvoznanie-9-klass-medvedev.pdf"
needs_obsch9_full = pytest.mark.skipif(not OBSCH9_FULL.is_file(), reason="obsch9 textbook missing")

S4_TITLE = "§ 4 Деятельность человека как способ существования личности"


def _ctx(c):
    """Путь чанка строкой, как в первой строке combined_text («§ 4 … > РАЗДЕЛ»)."""
    return " > ".join(_path(c) + ([_fd(c)["title"]] if _fd(c)["title"] not in _path(c) else []))


def _norm_title(title):
    return " ".join((title or "").lower().split())


def _contents(chunks):
    return [_flat(_fd(c)["content"]) for c in chunks]


@pytest.fixture(scope="module")
def obsch10_33_36(tmp_path_factory):
    return _slice_chunks(tmp_path_factory, OBSCH10, range(33, 37), "obsch10_33_36.pdf")


@needs_obsch10
def test_obsch10_s4_title_merged_key_terms_dropped_question_heading(obsch10_33_36):
    blocks, _prof, chunks, stats = obsch10_33_36
    headings = [b for b in blocks if b.kind == "heading"]
    sections = [b for b in headings if b.title.startswith("§")]
    assert [(b.title, b.level) for b in sections] == [(S4_TITLE, 2)]
    assert stats["layout"]["section_titles_merged"] == 1
    # Частей названия отдельными заголовками не осталось.
    assert not any(b.title in ("Деятельность человека", "как способ существования личности") for b in headings)
    q = next(b for b in headings if b.title == "ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?")
    assert q.level == 3 and not q.inline
    # Рубрика узла не заводит, список терминов выброшен.
    tree = tl.build_tree(blocks, {})
    assert not any(_norm_title(n.get("title")) == "ключевые понятия" for n in _walk_nodes(tree))
    assert stats["dropped_blocks"]["key_terms"]["count"] >= 1
    assert not any("ответственность • свобода" in t or "• деятельность" in t for t in _contents(chunks))
    for needle in ("Люди строят дома", "Можем ли мы утверждать"):
        c = next(c for c in chunks if needle in _flat(_fd(c)["content"]))
        assert _ctx(c).startswith(f"{S4_TITLE} > ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?"), _ctx(c)
        assert _fd(c)["combined_text"].split("\n\n", 1)[0].startswith(S4_TITLE)
    assert not any(_fd(c)["title"] == "КЛЮЧЕВЫЕ ПОНЯТИЯ" for c in chunks)


@needs_obsch10
def test_obsch10_s7_question_headings_have_body(tmp_path_factory):
    blocks, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH10, range(65, 76), "obsch10_65_75.pdf")
    sections = [b.title for b in blocks if b.kind == "heading" and b.title.startswith("§")]
    assert sections == ["§ 7 Как музеи сохраняют историю"]
    s7 = next(b for b in blocks if b.kind == "heading" and b.title.startswith("§ 7"))
    assert s7.level == 2
    assert stats["layout"]["section_titles_merged"] >= 1
    assert stats["dropped_blocks"]["key_terms"]["count"] >= 1
    for q in ("ЧТО ТАКОЕ МУЗЕЙ?", "КАКИМИ БЫВАЮТ МУЗЕИ?", "КАК РАБОТАЕТ МУЗЕЙ?"):
        h = next(b for b in blocks if b.kind == "heading" and b.title == q)
        assert h.level == 3, q
        bodies = [c for c in chunks if _fd(c)["block_type"] == "body" and _fd(c)["title"] == q]
        assert bodies, q
        assert all(_ctx(c) == f"§ 7 Как музеи сохраняют историю > {q}" for c in bodies), q
    assert not any(_fd(c)["title"] == "КЛЮЧЕВЫЕ ПОНЯТИЯ" for c in chunks)


@needs_obsch10
def test_obsch10_long_key_terms_inset_dropped_body_kept(tmp_path_factory):
    blocks, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH10, [174, 175], "obsch10_174_175.pdf")
    terms = next(b for b in blocks if "• реальный сектор экономики" in b.text)
    assert len(terms.text) >= tl.INSET_MIN_CHARS  # длинный список — inset, не inset_short
    texts = _contents(chunks)
    assert not any("реальный сектор экономики • бюджетные" in t or "• ключевая ставка" in t for t in texts)
    assert stats["dropped_blocks"]["key_terms"]["chars"] >= len(terms.text)
    body = [c for c in chunks if _fd(c)["block_type"] == "body"]
    assert any("Вам уже известно, что основу любой экономики" in t for t in _contents(body))
    assert any(_fd(c)["page_start"] == 2 for c in body)
    s16 = "§ 16 Современная российская экономика и денежно-кредитная политика Банка России"
    assert all(_ctx(c).startswith(s16) for c in body)


@needs_obsch9_full
def test_obsch9_s4_key_terms_aux_block_dropped(tmp_path_factory):
    blocks, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [32, 33], "obsch9_32_33.pdf")
    assert [b.title for b in blocks if b.kind == "heading" and b.title.startswith("§")] == [
        "§ 4 Традиционные ценности российского общества"
    ]
    kt = [b for b in blocks if b.kind == "key_terms"]
    assert kt and kt[0].text.startswith("КЛЮЧЕВЫЕ ПОНЯТИЯ") and "традиция" in kt[0].text
    assert stats["dropped_blocks"]["key_terms"]["count"] >= 2   # плашка + продолжение «••• брак…»
    texts = _contents(chunks)
    for term in ("традиция ••", "брак", "традиционные ценности ••", "КЛЮЧЕВЫЕ ПОНЯТИЯ"):
        assert not any(term in t and "•" in t for t in texts), term
    assert any(_fd(c)["title"] == "НАСЛЕДИЕ" and "Каждый из нас приходит" in _fd(c)["content"] for c in chunks)
    # В плоском тексте (метод llm) плашки тоже нет.
    paras = tl.layout_blocks_to_paragraphs(blocks)
    assert not any("КЛЮЧЕВЫЕ ПОНЯТИЯ" in (p.get("text") or "") for p in paras)


@needs_obsch9_full
def test_obsch9_section_range_title_merged(tmp_path_factory):
    blocks, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [19, 20], "obsch9_19_20.pdf")
    sec = [b for b in blocks if b.kind == "heading" and b.title.startswith("§")]
    assert [(b.title, b.level) for b in sec] == [("§ 2–3 Как устроено общество", 2)]
    assert stats["layout"]["section_titles_merged"] == 1
    assert any(_ctx(c) == "§ 2–3 Как устроено общество > СОЦИАЛЬНЫЕ ОБЩНОСТИ И ГРУППЫ" for c in chunks)


@needs_obsch9_full
def test_obsch9_pua_hyphen_caps_heading(tmp_path_factory):
    blocks, _p, chunks, _s = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [217, 218], "obsch9_217_218.pdf")
    h = next(b for b in blocks if b.kind == "heading" and "ЦИВИЛИЗАЦИЯ" in b.title)
    assert h.title == "РОССИЯ КАК ГОСУДАРСТВО-ЦИВИЛИЗАЦИЯ" and h.level == 3
    assert not any("" in b.title for b in blocks if b.kind == "heading")
    assert any(_fd(c)["title"] == h.title and "Мы с вами живём в стране" in _fd(c)["content"] for c in chunks)


@needs_obsch9_full
def test_obsch9_toc_at_end_not_capped_imprint_dropped(tmp_path_factory):
    # с. 216 — § 23 (предохранитель включается только после §), 240 — «Интернет-ресурсы».
    blocks, _p, chunks, stats = _slice_chunks(
        tmp_path_factory, OBSCH9_FULL, [216, 240, 241, 242], "obsch9_toc_end.pdf"
    )
    assert any(b.kind == "heading" and b.title == "Оглавление" for b in blocks)
    assert stats["layout"]["front_matter_skip_capped"] == 0
    texts = _contents(chunks)
    for needle in ("Подписано в печать", "Учебное издание", "Приветственное слово", "Тираж", "ISBN"):
        assert not any(needle in t for t in texts), needle
    assert not any(_fd(c)["title"] in ("Оглавление", "Интернет-ресурсы") for c in chunks)
    assert any("ГЛОБАЛИЗАЦИЯ ИЛИ МНОГОПОЛЯРНОСТЬ?" in _ctx(c) for c in chunks)


@needs_medinsky_full
def test_medinsky_6_14_section_titles_not_merged(medinsky_6_14):
    """«§ 1 Название» одной строкой (История) сшивка «§ N» не трогает."""
    blocks, _prof, chunks, stats = medinsky_6_14
    assert stats["layout"]["section_titles_merged"] == 0
    assert stats["layout"]["front_matter_skip_capped"] == 0
    first = next(c for c in chunks if _fd(c)["block_type"] == "body")
    assert _fd(first)["combined_text"].split("\n\n", 1)[0] == (
        "Глава I СССР В 1945—1991 гг. > § 1 Восстановление и развитие экономики и социальной сферы"
        " > 1 Влияние Победы"
    )


# Метод semantic: тот же layout-путь, границы от сегментера.


class _ParagraphSegmenter:
    """Сегментер без эмбеддера: граница перед каждым абзацем."""

    calls = 0

    def boundaries(self, items):
        _ParagraphSegmenter.calls += 1
        return set(range(1, len(items)))


@needs_obsch10
def test_obsch10_semantic_method_builds_same_tree(tmp_path_factory, monkeypatch):
    sliced = _slice_pdf(OBSCH10, range(33, 37), tmp_path_factory.mktemp("sem"), "obsch10_33_36_sem.pdf")
    monkeypatch.setenv("CHUNKER_EMBEDDING_URL", "http://embeddings.invalid/v1")

    def run(method):
        stats = {}
        with (
            patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO),
            patch.object(chunker_mod, "_make_semantic_segmenter", side_effect=lambda st: _ParagraphSegmenter()),
        ):
            chunks, _info = chunker_mod.process_document_to_chunks(
                str(sliced), min_size=30, document_kind="textbook", stats=stats, chunking_method=method
            )
        return chunks, stats

    _ParagraphSegmenter.calls = 0
    sem, sem_stats = run("semantic")
    lay, lay_stats = run("layout")
    assert sem_stats["chunking_method"] == "semantic" and sem_stats["chunking_fallback"] is None
    assert sem_stats["layout"]["section_titles_merged"] == 1
    assert _ParagraphSegmenter.calls >= 1
    assert {_ctx(c) for c in sem} == {_ctx(c) for c in lay}
    target = f"{S4_TITLE} > ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?"
    assert any(_ctx(c) == target and "Люди строят дома" in _flat(_fd(c)["content"]) for c in sem)
    assert len(sem) >= len(lay)
    assert not any("• деятельность" in t for t in _contents(sem))
    assert sem_stats["dropped_blocks"]["key_terms"] == lay_stats["dropped_blocks"]["key_terms"]


# Синтетика: рубрика в build_tree, предохранитель, капс-вопрос, сшивка «§ N».


def _hb(title, level, page=1, size=14.0, top=100.0):
    return tl.Block("heading", [_line(title, size=size, page=page, top=top)], title=title, level=level)


def _bb(text, page=1, kind="body", title=""):
    return tl.Block(kind, [_line(text[:40], page=page)], title=title, text=text)


_LONG_BODY = (
    "Это изложение параграфа, которое продолжается на следующей странице учебника. "
    "Оно длиннее ста шестидесяти символов и состоит из нескольких предложений. "
    "Третье предложение добавлено для надёжности."
)


def test_build_tree_key_terms_heading_drops_list_not_section():
    blocks = [
        _hb("§ 4 Деятельность человека", 2),
        _hb("КЛЮЧЕВЫЕ ПОНЯТИЯ", 3),
        _bb("• деятельность • ответственность", kind="inset_short"),
        _bb("• свобода • смысл жизни", kind="debris"),
        _bb("Изложение после рубрики на той же странице."),
        _hb("ВИДЫ ДЕЯТЕЛЬНОСТИ", 3, page=2),
        _bb("Текст раздела.", page=2),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    s4 = tree["children"][0]
    assert s4["title"] == "§ 4 Деятельность человека"
    assert [n["title"] for n in s4["children"]] == ["ВИДЫ ДЕЯТЕЛЬНОСТИ"]
    assert "Изложение после рубрики" in s4["content"]
    assert "•" not in s4["content"]
    kt = stats["dropped_blocks"]["key_terms"]
    assert kt["count"] == 3  # заголовок + два блока списка


def test_build_tree_key_terms_list_on_other_page_not_taken():
    blocks = [
        _hb("§ 4 Деятельность человека", 2),
        _hb("КЛЮЧЕВЫЕ ПОНЯТИЯ", 3),
        _bb("Врезка на следующей странице, а не список терминов рубрики.", page=2, kind="inset", title="Врезка"),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    assert [n.get("block_type") for n in tree["children"][0]["children"]] == ["inset"]
    assert stats["dropped_blocks"]["key_terms"]["count"] == 1


def test_build_tree_key_terms_keep_override_is_plain_heading(monkeypatch):
    monkeypatch.setenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS", "Ключевые понятия")
    blocks = [
        _hb("§ 4 Деятельность человека", 2),
        _hb("КЛЮЧЕВЫЕ ПОНЯТИЯ", 3),
        _bb("Пояснение к понятиям параграфа."),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    node = tree["children"][0]["children"][0]
    assert node["title"] == "КЛЮЧЕВЫЕ ПОНЯТИЯ" and "Пояснение" in node["content"]
    assert "key_terms" not in (stats.get("dropped_blocks") or {})


def test_build_tree_key_terms_drop_override_drops_subtree(monkeypatch):
    monkeypatch.setenv("CHUNKER_TEXTBOOK_DROP_HEADINGS", "Ключевые понятия")
    blocks = [
        _hb("§ 4 Деятельность человека", 2),
        _bb("Эпиграф параграфа."),
        _hb("КЛЮЧЕВЫЕ ПОНЯТИЯ", 3),
        _bb("Изложение под рубрикой выбрасывается целиком."),
        _bb("И второй абзац тоже."),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    s4 = tree["children"][0]
    assert "Изложение" not in str(tree)
    assert s4["content"].startswith("Эпиграф")
    assert stats["dropped_blocks"]["front_matter"]["count"] == 2
    assert "key_terms" not in stats["dropped_blocks"]


def test_build_tree_front_matter_skip_capped_on_next_page():
    blocks = [
        _hb("§ 1 Общество", 2),
        _bb("Текст параграфа."),
        _hb("Литература", 3),
        _bb("Иванов И. И. Общество. М., 2020. Петров П. П. Социология. М., 2021."),
        _bb(_LONG_BODY, page=2),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    assert "Иванов" not in str(tree)
    assert "Это изложение параграфа" in str(tree)
    assert stats["layout"]["front_matter_skip_capped"] == 1
    assert stats["dropped_blocks"]["front_matter"]["count"] == 1


def test_build_tree_front_matter_skip_not_capped_same_page_short_or_before_section():
    # Та же страница — выброшено.
    blocks = [_hb("§ 1 Общество", 2), _hb("Литература", 3), _bb(_LONG_BODY, page=1)]
    stats = {}
    tl.build_tree(blocks, stats)
    assert stats["dropped_blocks"]["front_matter"]["count"] == 1
    assert (stats.get("layout") or {}).get("front_matter_skip_capped", 0) == 0
    # Следующая страница, но короткий или одно предложение — выброшено.
    short = "Короткая строка выходных данных. Тираж 1000 экз."
    one = "Одно предложение " + "очень длинное " * 20 + "без точки в середине."
    for text in (short, one):
        stats = {}
        tree = tl.build_tree([_hb("§ 1 Общество", 2), _hb("Литература", 3), _bb(text, page=2)], stats)
        assert text not in str(tree)
        assert (stats.get("layout") or {}).get("front_matter_skip_capped", 0) == 0
    # До первого § предохранитель не работает (титул, предисловие).
    stats = {}
    tree = tl.build_tree([_hb("Литература", 2), _bb(_LONG_BODY, page=2)], stats)
    assert "Это изложение" not in str(tree)
    # «Оглавление» после § — пропуск не обрывается.
    stats = {}
    tree = tl.build_tree([_hb("§ 1 Общество", 2), _hb("Оглавление", 1), _bb(_LONG_BODY, page=2)], stats)
    assert "Это изложение" not in str(tree)
    assert (stats.get("layout") or {}).get("front_matter_skip_capped", 0) == 0


def test_looks_caps_question():
    for s in ("ЧТО ТАКОЕ МУЗЕЙ?", "КАКИМИ БЫВАЮТ МУЗЕИ?", "КАК РАБОТАЕТ МУЗЕЙ?", "ГЛОБАЛИЗАЦИЯ ИЛИ МНОГОПОЛЯРНОСТЬ?"):
        assert tl._looks_caps_question(s), s
    for s in (
        "ЧТО?",                                   # одно слово
        "что такое музей?",                       # не капс
        "Что такое музей?",
        "ПОЧЕМУ " * 12 + "МУЗЕЙ?",                # длиннее 80 символов
        "ВСПОМНИТЕ",                              # без «?»
        "ЧТО ТАКОЕ МУЗЕЙ.",
        "ЧТО, ГДЕ, КОГДА, ?",                     # перед «?» запятая
        "",
    ):
        assert not tl._looks_caps_question(s), s


def test_classify_line_caps_question_heading_needs_style_and_no_fill():
    prof = _profile()
    page = (595.0, 842.0)

    def ln(text, *, bold=False, family="NewtonSanPin", fill_id=None, size=11.0):
        line = _line(text, size=size, family=family, fill_id=fill_id, top=300.0)
        line.bold = bold
        return line

    assert tl.classify_line(ln("ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?", bold=True), prof, page) == "heading"
    assert tl.classify_line(ln("ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?", family="ReformaGrotesk"), prof, page) == "heading"
    assert tl.classify_line(ln("ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?"), prof, page) != "heading"           # не выделена
    assert tl.classify_line(ln("ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?", bold=True, fill_id=1), prof, page) != "heading"
    # Глиф дефиса из частной области Unicode → «-», строка становится заголовком.
    line = ln("РОССИЯ КАК ГОСУДАРСТВОЦИВИЛИЗАЦИЯ", bold=True)
    assert tl.classify_line(line, prof, page) == "heading"
    assert line.text == "РОССИЯ КАК ГОСУДАРСТВО-ЦИВИЛИЗАЦИЯ"
    line = ln("ВЗАИМОПОМОЩЬ  ОСНОВА ОБЩЕСТВА", bold=True)
    assert tl.classify_line(line, prof, page) == "heading"
    assert line.text == "ВЗАИМОПОМОЩЬ — ОСНОВА ОБЩЕСТВА"


def _sb(text, *, top, size=20.0, page=1, kind="heading", level=3):
    line = _line(text, size=size, top=top, page=page, family="ReformaGrotesk")
    return tl.Block(kind, [line], title=text if kind == "heading" else "", level=level, text=text)


def test_merge_section_titles_line_before_number_and_after():
    prof = _profile()
    blocks = [
        _sb("Деятельность человека", top=100.0),
        _sb("§ 4", top=95.0, size=30.0, level=2),
        _sb("как способ существо-", top=122.0),
        _sb("вания личности", top=144.0),
        _bb("Изложение параграфа."),
    ]
    out, merged = tl._merge_section_titles(blocks, prof)
    assert merged == 1
    assert [b.kind for b in out] == ["heading", "body"]
    assert out[0].title == "§ 4 Деятельность человека как способ существования личности"
    assert out[0].level == 2 and len(out[0].lines) == 4


def test_merge_section_titles_number_then_two_lines():
    prof = _profile()
    blocks = [
        _sb("§ 7", top=100.0, size=30.0, level=2),
        _sb("Как музеи", top=100.0),
        _sb("сохраняют историю", top=122.0),
        _sb("ЧТО ТАКОЕ МУЗЕЙ?", top=300.0, size=12.0),
    ]
    out, merged = tl._merge_section_titles(blocks, prof)
    assert merged == 1
    assert [b.title for b in out] == ["§ 7 Как музеи сохраняют историю", "ЧТО ТАКОЕ МУЗЕЙ?"]
    assert out[0].level == 2


def test_merge_section_titles_leaves_full_title_and_far_lines_alone():
    prof = _profile()
    full = [_sb("§ 1 Восстановление экономики", top=100.0, size=24.0, level=2), _bb("Текст.")]
    out, merged = tl._merge_section_titles(list(full), prof)
    assert merged == 0 and out == full
    # Голый «§ 2», рядом ничего подходящего: далеко по высоте, другая страница,
    # мелкий кегль, рубрика-аппарат.
    lonely = [
        _sb("Далёкая строка", top=400.0),
        _sb("§ 2", top=100.0, size=30.0, level=2),
        _sb("Строка на другой странице", top=100.0, page=2),
    ]
    out, merged = tl._merge_section_titles(list(lonely), prof)
    assert merged == 0 and [b.title for b in out] == [b.title for b in lonely]
    small = [_sb("§ 3", top=100.0, size=30.0, level=2), _sb("Мелкая строка", top=100.0, size=11.0)]
    assert tl._merge_section_titles(list(small), prof)[1] == 0
    rubric = [_sb("§ 3", top=100.0, size=30.0, level=2), _sb("ВОПРОСЫ И ЗАДАНИЯ", top=100.0)]
    assert tl._merge_section_titles(list(rubric), prof)[1] == 0


def test_merge_section_titles_keeps_junk_between_title_lines():
    prof = _profile()
    junk1 = tl.Block("junk", [_line("12", page=1, top=110.0)])
    junk2 = tl.Block("junk", [_line("13", page=1, top=130.0)])
    blocks = [
        _sb("§ 7", top=100.0, size=30.0, level=2),
        junk1,
        _sb("Как музеи", top=100.0),
        junk2,
        _sb("сохраняют историю", top=122.0),
    ]
    out, merged = tl._merge_section_titles(blocks, prof)
    assert merged == 1 and out[0].title == "§ 7 Как музеи сохраняют историю"
    # Колонтитулы между строками названия не теряются (их счёт идёт в dropped_blocks.junk).
    assert [b for b in out if b.kind == "junk"] == [junk1, junk2]


def test_merge_section_titles_skips_rubric_lines():
    prof = _profile()
    for rubric in ("ВСПОМНИТЕ", "КЛЮЧЕВЫЕ ПОНЯТИЯ"):
        after = [_sb("§ 3", top=100.0, size=30.0, level=2), _sb(rubric, top=100.0)]
        out, merged = tl._merge_section_titles(list(after), prof)
        assert merged == 0 and [b.title for b in out] == ["§ 3", rubric], rubric
        before = [_sb(rubric, top=100.0), _sb("§ 3", top=95.0, size=30.0, level=2)]
        assert tl._merge_section_titles(list(before), prof)[1] == 0, rubric
    # Рубрика не мешает настоящему названию.
    mixed = [_sb("§ 3", top=100.0, size=30.0, level=2), _sb("Общество", top=100.0), _sb("ВСПОМНИТЕ", top=122.0)]
    out, merged = tl._merge_section_titles(mixed, prof)
    assert merged == 1 and [b.title for b in out] == ["§ 3 Общество", "ВСПОМНИТЕ"]


def test_build_tree_front_matter_skip_capped_counts_rescues_not_blocks():
    blocks = [
        _hb("§ 1 Общество", 2),
        _bb("Текст параграфа."),
        _hb("Литература", 3),
        _bb("Иванов И. И. Общество. М., 2020."),
        _bb(_LONG_BODY, page=2),
        _bb("Короткий абзац.", page=2),
        _bb("Врезка после спасённого изложения, тоже остаётся.", page=2, kind="inset", title="Врезка"),
        _bb(_LONG_BODY.replace("Это", "Ещё одно"), page=3),
    ]
    stats = {}
    tree = tl.build_tree(blocks, stats)
    s1 = tree["children"][0]
    assert "Короткий абзац." in str(s1) and "Врезка после" in str(s1) and "Ещё одно изложение" in str(s1)
    assert "Иванов" not in str(tree)
    assert stats["layout"]["front_matter_skip_capped"] == 1


@needs_obsch9_full
def test_obsch9_chapter_number_and_title_in_one_plate(tmp_path_factory):
    blocks, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [7, 8, 9], "obsch9_7_9.pdf")
    ch = [b for b in blocks if b.kind == "heading" and b.title.startswith("Глава")]
    assert [(b.title, b.level) for b in ch] == [("Глава I. ЧЕЛОВЕК И ОБЩЕСТВО", 1)]
    assert chunks and all(_ctx(c).startswith("Глава I. ЧЕЛОВЕК И ОБЩЕСТВО") for c in chunks)
    assert any(_ctx(c) == "Глава I. ЧЕЛОВЕК И ОБЩЕСТВО > § 1 Человек > ЗАГАДКА ЧЕЛОВЕКА" for c in chunks)
    assert not any(b.kind == "heading" and b.title in ("Глава I", "ЧЕЛОВЕК И ОБЩЕСТВО") for b in blocks)


@needs_obsch9_full
def test_obsch9_chapter_title_before_number_in_reading_order(tmp_path_factory):
    blocks, _p, chunks, _s = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [197, 198], "obsch9_197_198.pdf")
    ch = [b for b in blocks if b.kind == "heading" and b.level == 1]
    assert [b.title for b in ch] == ["Глава V. РОССИЯ НА ПУТИ В БУДУЩЕЕ"]
    assert chunks and all(_ctx(c).startswith("Глава V. РОССИЯ НА ПУТИ В БУДУЩЕЕ") for c in chunks)
    assert any(" > § 21–22 Россия — страна возможностей" in _ctx(c) for c in chunks)


@needs_obsch9_full
def test_obsch9_s10_epigraph_starts_first_section_chunk(tmp_path_factory):
    _b, _p, chunks, stats = _slice_chunks(tmp_path_factory, OBSCH9_FULL, [87, 88], "obsch9_87_88.pdf")
    c = next(c for c in chunks if "Право есть искусство добра и справедливости" in _fd(c)["content"])
    assert _ctx(c) == "§ 10 Нормы и отрасли права. Правоотношения > ЧТО ТАКОЕ ПРАВО?"
    assert _fd(c)["block_type"] == "body"
    assert _fd(c)["content"].startswith("Право есть искусство добра и справедливости.")
    assert "Публий Ювентий Цельс" in _fd(c)["content"]
    assert len(_fd(c)["content"]) > 200  # склеен с первым абзацем раздела
    assert _fd(c)["page_start"] == 1
    assert sum("Ювентий" in _fd(x)["content"] for x in chunks) == 1
