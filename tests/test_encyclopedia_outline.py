"""Unit tests for encyclopedia outline extraction (БРЭ-style PDFs)."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    # Inside chunker image: code at /app, tests at /app/tests
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import docx2json_outline as outline  # noqa: E402
import main as chunker_mod  # noqa: E402


SAMPLE_MD = """\
БОЛЬШАЯ
ЭНЦИКЛОПЕДИЯ
РОССИЙСКАЯ
28

НАУЧНО-РЕДАКЦИОННЫЙ СОВЕТ
Председатель - Ю. С. ОСИПОВ

ПУТОР2АКИ (Diplomesodon), род млекопи -
тающих сем. землеройковых отр. земле -
ройкообразных (Soricomorpha); единст -
венный вид – пегий П. ( D. pulchellum ).
Внешне похож на др. землероек, но от -
личается необычной окраской.
Н. А. Щипанов.

ПУТОР2АНА, плато, самая приподнятая
и расчленённая сев.-зап. часть Среднеси-
бирского плоскогорья, в России.
Туризм.

ПУТОР2АНСКИЙ ЗАПОВ2ЕДНИК, гос. при-
родный заповедник России, в центр. час-
ти плато Путорана.
"""


def _titles(node, out=None):
    if out is None:
        out = []
    t = (node.get("title") or "").strip()
    if t:
        out.append(t)
    for ch in node.get("children") or []:
        _titles(ch, out)
    return out


def _all_content(node, parts=None):
    if parts is None:
        parts = []
    c = (node.get("content") or "").strip()
    if c:
        parts.append(c)
    for ch in node.get("children") or []:
        _all_content(ch, parts)
    return parts


def test_stress_digit_and_space_hyphen_cleanup():
    raw = (
        "ПУТОР2АКИ (Diplomesodon), род млекопи -\n"
        "тающих сем. землеройковых.\n"
        "РЕВОЛ2ЮЦИЯ 1848–49 В 2АВСТРИИ, бур-\n"
        "жуазная революция.\n"
        "Пл. ок. 250 тыс. км 2. Выс. до 1678 м.\n"
    )
    cleaned = outline._textbook_cleanup_text(raw)
    assert "ПУТОРАКИ" in cleaned
    assert "2АКИ" not in cleaned
    assert "млекопитающих" in cleaned
    assert "млекопи -" not in cleaned
    assert "РЕВОЛЮЦИЯ" in cleaned
    assert "В АВСТРИИ" in cleaned  # space preserved
    assert "ВАВСТРИИ" not in cleaned
    # Real superscript / numeric "км 2" must survive
    assert "км 2" in cleaned or "км2" in cleaned


def test_caps_hyphen_join_for_headwords():
    raw = "РЕНТГЕНОФЛУОРЕСЦЕНТНЫЙ АН2А-\nЛИЗ (РФА), физич. метод."
    cleaned = outline._textbook_cleanup_text(raw)
    assert "АНАЛИЗ" in cleaned or "АН2АЛИЗ" not in cleaned.replace("АНАЛИЗ", "")
    # After stress strip + caps join: АНАЛИЗ
    assert "АНАЛИЗ (РФА)" in cleaned or "АНАЛИЗ(РФА)" in cleaned.replace(" ", "")


def test_encyclopedia_rewrite_splits_entries_and_drops_front_matter():
    cleaned = outline._encyclopedia_cleanup_text(SAMPLE_MD)
    rewritten = outline._encyclopedia_rewrite_headings(cleaned)
    assert "НАУЧНО-РЕДАКЦИОННЫЙ СОВЕТ" not in rewritten
    assert "## ПУТОРАКИ" in rewritten
    assert "## ПУТОРАНА" in rewritten
    assert "## ПУТОРАНСКИЙ ЗАПОВЕДНИК" in rewritten
    # Author signature stripped
    assert "Щипанов" not in rewritten
    # Soft hyphens joined
    assert "млекопитающих" in rewritten


def test_encyclopedia_tree_and_chunks():
    cleaned = outline._encyclopedia_cleanup_text(SAMPLE_MD)
    rewritten = outline._encyclopedia_rewrite_headings(cleaned)
    tree = outline.parse_markdown_to_tree(rewritten, legal_postprocess=False)
    titles = _titles(tree)
    assert titles == ["ПУТОРАКИ", "ПУТОРАНА", "ПУТОРАНСКИЙ ЗАПОВЕДНИК"]
    content = "\n".join(_all_content(tree))
    assert "Diplomesodon" in content
    assert "плато" in content

    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="bre", document_kind="encyclopedia"
    )
    assert len(chunks) >= 3
    combined = [c["fragment_data"]["combined_text"] for c in chunks]
    # Encyclopedia prefixes headword into combined_text
    assert any(t.startswith("ПУТОРАКИ") for t in combined)
    assert any("\n\n" in t and t.split("\n\n", 1)[0] == "ПУТОРАКИ" for t in combined)
    # No stress digits left in emitted text
    blob = "\n".join(combined)
    assert "ПУТОР2" not in blob
    assert "ЗАПОВ2" not in blob


def test_mid_sentence_acronym_not_promoted():
    plain = (
        "ПУТОРАКИ (Diplomesodon), род млекопитающих.\n"
        "классы при нём (с 1900 Муз. уч-ще\n"
        "ИРМО, первый директор – пианист\n"
        "Иванов).\n"
        "\n"
        "РАБОТОРГОВЛЯ, продажа и покупка людей.\n"
        "\n"
        "ПУТЬ, в Др.-рус. гос-ве, русских землях.\n"
    )
    rewritten = outline._encyclopedia_rewrite_headings(plain)
    assert "## ИРМО" not in rewritten
    assert "## ПУТОРАКИ" in rewritten
    assert "## РАБОТОРГОВЛЯ" in rewritten
    # 4-letter ПУТЬ after a terminator is allowed
    assert "## ПУТЬ" in rewritten


def test_initials_not_promoted_as_headword():
    md = (
        "НАУЧНО-РЕДАКЦИОННЫЙ СОВЕТ\n"
        "А. А. АВДЕЕВ, Е. Н. АВРОРИН, С. И. АДЯН.\n"
        "\n"
        "ПУТОРАКИ (Diplomesodon), род млекопитающих.\n"
    )
    rewritten = outline._encyclopedia_rewrite_headings(
        outline._encyclopedia_cleanup_text(md)
    )
    assert "## А. А. АВДЕЕВ" not in rewritten
    assert "## ПУТОРАКИ" in rewritten
    assert "НАУЧНО-РЕДАКЦИОННЫЙ" not in rewritten


def test_document_processor_manager_encyclopedia_branch():
    mgr = outline.DocumentProcessorManager(document_kind="encyclopedia")
    assert mgr.document_kind == "encyclopedia"
    assert isinstance(mgr._encyclopedia, outline.EncyclopediaProcessor)


def test_biographical_headword_promoted():
    md = (
        "ПУТОРАКИ (Diplomesodon), род млекопитающих.\n"
        "ПУТИН Владимир Владимирович (р. 7.10.1952, Ленинград), рос. гос. деятель.\n"
        "ПУШКИН Александр Сергеевич [26.5(6.6).1799, Москва], поэт.\n"
    )
    rewritten = outline._encyclopedia_rewrite_headings(md)
    assert "## ПУТОРАКИ" in rewritten
    assert "## ПУТИН" in rewritten
    assert "## ПУШКИН" in rewritten
    assert "Владимир Владимирович" in rewritten
    assert "Александр Сергеевич" in rewritten


def test_encyclopedia_norm_headword_collapses_letterspacing():
    assert outline._encyclopedia_norm_headword("ПУ СУНЛИН") == "ПУСУНЛИН"
    assert outline._encyclopedia_norm_headword("ПУХОВЫЕ В ОЛОСЫ") == "ПУХОВЫЕ ВОЛОСЫ"
    assert outline._encyclopedia_norm_headword("ПУТЕВОЙ РАСП ЕВ") == "ПУТЕВОЙ РАСПЕВ"
    assert (
        outline._encyclopedia_norm_headword("ПУТОРАНСКИЙ ЗАПОВЕДНИК")
        == "ПУТОРАНСКИЙ ЗАПОВЕДНИК"
    )


def _bre_volume_pdf() -> Path | None:
    data = _REPO / "tests" / "data"
    if not data.is_dir():
        data = Path(__file__).resolve().parent / "data"
    if not data.is_dir():
        return None
    for p in sorted(data.glob("*Том_28.pdf")):
        if "стр" in p.name:
            continue
        return p
    return None


def test_encyclopedia_processor_bre_slice():
    """Real two-column БРЭ PDF: pypdf path must keep spaces and find entries."""
    src = _bre_volume_pdf()
    if src is None:
        pytest.skip("БРЭ Том 28 PDF is not in tests/data")
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        pytest.skip("pypdf is not installed")

    reader = PdfReader(str(src))
    writer = PdfWriter()
    for i in range(min(40, len(reader.pages))):
        writer.add_page(reader.pages[i])
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as fh:
            writer.write(fh)
        tree = outline.EncyclopediaProcessor().process(tmp_path)
        titles = _titles(tree)
        blob = "\n".join(_all_content(tree))
        joined = " ".join(titles)
        assert len(titles) >= 25, titles
        assert "ПУТИН" in joined
        assert "ПУЧЧИНИ" in joined
        assert "ПУШКИН" in joined
        assert re.search(r"[а-яё]{25,}", blob) is None
    finally:
        os.unlink(tmp_path)


def test_stress_digit_with_space_rejoins_a_headword():
    """БРЭ marks stress as a digit inside the word, sometimes with a space:
    «МАШ 2ИНЫ» for МАШИ́НЫ. Dropping only the digit left the headword torn in
    two, and the entry title is what gives an encyclopedia question its context.
    """
    assert outline._strip_stress_digits("ПУТЕВ2ЫЕ МАШ 2ИНЫ") == "ПУТЕВЫЕ МАШИНЫ"
    assert outline._strip_stress_digits("ПШЕВ2ОРСКАЯ КУЛЬТ 2УРА") == "ПШЕВОРСКАЯ КУЛЬТУРА"
    assert outline._strip_stress_digits("П2УШКИНСКИЙ ЗАПОВ 2ЕДНИК") == "ПУШКИНСКИЙ ЗАПОВЕДНИК"


def test_stress_digit_keeps_a_real_word_boundary():
    """A short standalone word before the mark is a genuine boundary, not
    kerning — «В 2ИТАЛИИ» must not become «ВИТАЛИИ»."""
    assert outline._strip_stress_digits("РЕВОЛЮЦИЯ 1848 В 2ИТАЛИИ") == "РЕВОЛЮЦИЯ 1848 В ИТАЛИИ"
    assert outline._strip_stress_digits("ПЬЕРО ДИ К2ОЗИМО") == "ПЬЕРО ДИ КОЗИМО"


def test_stress_digit_never_leaves_a_digit_behind():
    for src in ("В 2ИТАЛИИ", "МАШ 2ИНЫ", "ПУТЕВ2ЫЕ", "ДЕ 9ЛА"):
        assert not re.search(r"[А-ЯЁа-яё] ?[29][А-ЯЁа-яё]", outline._strip_stress_digits(src)), src
