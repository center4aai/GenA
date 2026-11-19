"""Unit tests for textbook outline extraction (обществознание-style PDFs)."""

from __future__ import annotations

import sys
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
10

к л а с с
к л а с с

Е
И
Н
А
Н
З
О
В
Т
С
Е
Щ
Б
О

ОБЩЕСТВОЗНАНИЕ

УДК 373.167.1:3+3(075.3)
ББК 60я721
О-28

Исключительные права на учебник
принадлежат Российской Федерации

В оформлении обложки использованы: фотография памятника
(Pavel Mastepanov/Shutterstock.com)

16-0885-02-002.indd    2    09.07.2026       11:03:57   SKrutikov

0A594

Глава I.   ЧЕЛОВЕК

ИОКРУЖАЮЩИЙ МИР

§ 1

Сознание человека.
Мировоззрение и ценности

«Каждый  хочет  изменить  человечество,  но  никто  не  задумывается,
как изменить себя».

Л. Н. Толстой (1828—1910), русский писатель и мыслитель

КЛЮЧЕВЫЕ  ПОНЯТИЯ

 (cid:120) сознание
 (cid:120) самосознание
 (cid:120) мировоззрение

0FBF7

§ 1.  Сознание  человека.  Мировоззрение  и  ценности

5

СОЗНАНИЕ

Каждый из нас обладает сознанием. С точки зрения психологии
сознание  человека  —  связанная  с  речью  высшая  функция  мозга,
которая  заключается  в  обобщённом  и  целенаправленном  отраже-
нии  действительности.

ГЛАВА  I

§ 1.  Сознание  человека.  Мировоззрение  и  ценности

6

Сторонники материалистического подхода считают, что Вселенная
возникла в результате «Большого взрыва».

Статья 2. Воспроизведение Государственного герба Российской Федерации допу-
скается без специального разрешения.

А. С. Пушкин (1799—1837) — великий русский поэт.

ПРОВЕРЯЕМ  СЕБЯ

1. Что такое сознание?
2. Чем мировоззрение отличается от убеждений?
"""


def _titles(node, acc=None):
    if acc is None:
        acc = []
    t = (node.get("title") or "").strip()
    if t:
        acc.append((node.get("level"), t))
    for ch in node.get("children") or []:
        _titles(ch, acc)
    return acc


def _all_content(node) -> str:
    parts = [(node.get("content") or "").strip()]
    for ch in node.get("children") or []:
        parts.append(_all_content(ch))
    return "\n".join(p for p in parts if p)


def test_textbook_cleanup_strips_junk_and_dehyphenates():
    cleaned = outline._textbook_cleanup_text(SAMPLE_MD)
    assert "УДК" not in cleaned
    assert "ББК" not in cleaned
    assert ".indd" not in cleaned
    assert "Исключительные права" not in cleaned
    assert "Shutterstock" not in cleaned
    assert "0A594" not in cleaned
    assert "0FBF7" not in cleaned
    # cover letter run removed
    assert "\nЕ\nИ\nН\n" not in cleaned
    # de-hyphenation
    assert "отражении" in cleaned or "отраже\nнии" not in cleaned
    assert "отраже-\nнии" not in cleaned
    # cid bullet → •
    assert "(cid:" not in cleaned
    assert "•" in cleaned


def test_textbook_rewrite_headings_and_running_header_dedup():
    cleaned = outline._textbook_cleanup_text(SAMPLE_MD)
    rewritten = outline._textbook_rewrite_headings(cleaned)
    chapter_lines = [l for l in rewritten.splitlines() if l.startswith("# ")]
    section_lines = [l for l in rewritten.splitlines() if l.startswith("## ")]
    sub_lines = [l for l in rewritten.splitlines() if l.startswith("### ")]

    assert len(chapter_lines) == 1, chapter_lines
    assert "Глава I" in chapter_lines[0]
    assert "ЧЕЛОВЕК" in chapter_lines[0].upper() or "Человек" in chapter_lines[0]

    # bare § 1 + title absorbed; later «§ 1. …» running headers dropped
    assert len(section_lines) == 1, section_lines
    assert section_lines[0].startswith("## § 1")
    assert "Сознание" in section_lines[0]

    assert any("КЛЮЧЕВЫЕ ПОНЯТИЯ" in l for l in sub_lines)
    assert any("СОЗНАНИЕ" in l for l in sub_lines)
    assert any("ПРОВЕРЯЕМ СЕБЯ" in l for l in sub_lines)


def test_parse_markdown_textbook_builds_hierarchy():
    cleaned = outline._textbook_cleanup_text(SAMPLE_MD)
    rewritten = outline._textbook_rewrite_headings(cleaned)
    tree = outline.parse_markdown_to_tree(rewritten, legal_postprocess=False)
    titles = _titles(tree)
    levels = {t: lv for lv, t in titles}

    assert any("Глава I" in t for t in levels), titles
    assert any(t.startswith("§ 1") for t in levels), titles
    # stray Статья must NOT become a heading when legal_postprocess=False
    assert not any(t.startswith("Статья") for t in levels), titles

    content = _all_content(tree)
    assert "обладает сознанием" in content
    assert "Большого взрыва" in content
    assert "Пушкин" in content
    # year range must not shred into numbered-item titles
    assert not any("1799" in t and t.strip().startswith("1") for lv, t in titles)


def test_stray_article_and_year_range_do_not_shred_with_legal_postprocess_off():
    """Regression: one «Статья N» quote + birth years must not swallow the book."""
    md = (
        "Введение\n\n"
        "Проза главы учебника с достаточным количеством текста для проверки.\n\n"
        "Статья 2. Воспроизведение Государственного герба Российской Федерации.\n\n"
        "Ещё больше учебного текста после цитаты статьи закона.\n\n"
        "А. С. Пушкин (1799—1837) — великий русский поэт и мыслитель эпохи.\n\n"
        "Заключительный абзац тоже должен сохраниться целиком в content.\n"
    )
    tree_legal = outline.parse_markdown_to_tree(md, legal_postprocess=True)
    tree_tb = outline.parse_markdown_to_tree(md, legal_postprocess=False)

    content_tb = _all_content(tree_tb)
    assert "Проза главы учебника" in content_tb
    assert "Ещё больше учебного текста" in content_tb
    assert "Заключительный абзац" in content_tb
    assert "Пушкин" in content_tb

    # With legal postprocess the article may split, but textbook mode must keep a flat/simple tree
    titles_tb = _titles(tree_tb)
    assert not any(t.startswith("Статья") for _, t in titles_tb)


def test_create_llm_chunks_textbook_preserves_prose_and_breadcrumbs():
    cleaned = outline._textbook_cleanup_text(SAMPLE_MD)
    rewritten = outline._textbook_rewrite_headings(cleaned)
    tree = outline.parse_markdown_to_tree(rewritten, legal_postprocess=False)
    stats = {}
    chunks = chunker_mod.create_llm_chunks(
        tree, min_size=20, document_name="obsh", document_kind="textbook", stats=stats
    )
    assert chunks
    texts = [c["fragment_data"]["content"] for c in chunks]
    joined = "\n".join(texts)
    assert "обладает сознанием" in joined
    # «Проверяем себя» и «Ключевые понятия» — аппарат учебника, не чанки.
    assert "Что такое сознание?" not in joined
    assert "exercise" in stats["dropped_blocks"]
    assert "Большого взрыва" in joined
    assert ".indd" not in joined
    assert "УДК" not in joined

    # breadcrumb should carry chapter for section/subhead chunks
    with_bc = [
        c for c in chunks
        if (c.get("hierarchy_context") or {}).get("breadcrumb_text")
    ]
    assert with_bc, "expected breadcrumbs from chapter > section hierarchy"
    assert any("Глава" in (c["hierarchy_context"]["breadcrumb_text"] or "") for c in with_bc)

    # Списки ключевых понятий и остатки обложки выбрасываются как огрызки
    # вёрстки, поэтому полного покрытия дерева не ждём.
    assert "• сознание" not in joined

    # coverage guard: chunk content vs tree content
    def sum_content(n):
        t = len((n.get("content") or "").strip())
        for ch in n.get("children") or []:
            t += sum_content(ch)
        return t

    source = sum_content(tree)
    chunk_chars = sum(len(t) for t in texts)
    dropped = sum(v["chars"] for v in stats["dropped_blocks"].values())
    assert source > 0
    assert (chunk_chars + dropped) / source >= 0.85, f"coverage {chunk_chars}+{dropped}/{source}"


def test_section_range_heading():
    md = "§ 2–3\n\nКак устроено общество\n\nТекст параграфа про общество и социальные группы.\n"
    cleaned = outline._textbook_cleanup_text(md)
    rewritten = outline._textbook_rewrite_headings(cleaned)
    assert any(
        l.startswith("## § 2") and "общество" in l.casefold()
        for l in rewritten.splitlines()
    ), rewritten


def test_textbook_heading_table_marks_are_stripped():
    md = "§ 18. | | Хозяйственная | | | система | | --- | --- |\n\nТекст параграфа про хозяйственную систему и её признаки.\n"
    cleaned = outline._textbook_cleanup_text(md)
    rewritten = outline._textbook_rewrite_headings(cleaned)
    assert any(l == "## § 18. Хозяйственная система" for l in rewritten.splitlines()), rewritten


def test_running_headers_with_page_numbers_do_not_become_headings():
    """«8 ГЛАВА I», «ГЛАВА I 9», «§ 1. Название 7» — колонтитулы страниц.

    Раньше «8 ГЛАВА I» проходил как КАПС-подзаголовок и каждая страница
    становилась узлом дерева (чанк = страница). Теперь номер страницы
    отбрасывается, а повторы главы/параграфа дедупятся как раньше.
    """
    md = "\n".join([
        "ГЛАВА I",
        "",
        "§ 1 Восстановление и развитие экономики",
        "",
        "Первый абзац параграфа о восстановлении экономики после войны.",
        "",
        "8 ГЛАВА I",
        "",
        "Второй абзац продолжает рассказ о восстановлении.",
        "",
        "§ 1. Восстановление и развитие экономики 9",
        "",
        "Третий абзац — ещё одна страница того же параграфа.",
        "",
        "10 ГЛАВА I",
        "",
        "ПОДВЕДЁМ ИТОГИ",
        "",
        "Итоги параграфа.",
        "",
        "ГЛАВА I 11",
        "",
        "§ 2. Политическая система 12",
        "",
        "Абзац второго параграфа.",
        "",
        "§ 2. Политическая система 13",
        "",
        "Ещё один абзац второго параграфа.",
    ])
    rewritten = outline._textbook_rewrite_headings(md)
    lines = rewritten.splitlines()
    chapters = [l for l in lines if l.startswith("# ")]
    sections = [l for l in lines if l.startswith("## ")]
    subs = [l for l in lines if l.startswith("### ")]
    assert chapters == ["# Глава I"], chapters
    assert len(sections) == 2 and sections[0].startswith("## § 1") and sections[1].startswith("## § 2"), sections
    assert not any("8 ГЛАВА" in l or "10 ГЛАВА" in l or "ГЛАВА I 11" in l for l in lines), lines
    assert sections[1] == "## § 2. Политическая система", sections[1]
    assert subs == ["### ПОДВЕДЁМ ИТОГИ"], subs
    assert "Второй абзац" in rewritten and "Третий абзац" in rewritten


def test_running_header_core_requires_repeats():
    """Одиночный «Глава I 1945»-подобный заголовок не трогаем: нет повторов."""
    assert outline._textbook_running_header_core("8 ГЛАВА I") == "ГЛАВА I"
    assert outline._textbook_running_header_core("§ 3. Право и мораль 45") == "§ 3. Право и мораль"
    assert outline._textbook_running_header_core("ГЛАВА I") is None
    assert outline._textbook_running_header_core("1 Влияние Победы.") is None
    once = {outline._textbook_norm_heading_key("ГЛАВА II"): 1}
    assert outline._textbook_strip_running_header_page("14 ГЛАВА II", once) == "14 ГЛАВА II"
    twice = {outline._textbook_norm_heading_key("ГЛАВА II"): 2}
    assert outline._textbook_strip_running_header_page("14 ГЛАВА II", twice) == "ГЛАВА II"


# ─── аппарат «Дополнительные материалы», врезки, словарь (тикет 71901445) ──


def _sentences(n_chars: int, sent: str = "Это предложение основного текста учебника истории России. ") -> str:
    text = ""
    while len(text) < n_chars:
        text += sent
    return text[:n_chars].rsplit(" ", 1)[0].rstrip(".") + "."


def test_split_prose_units_drops_extra_materials_and_film_list():
    content = "\n\n".join([
        "Основной текст параграфа заканчивается точкой. " * 3,
        "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ",
        "1. «БАЛЛАДА О СОЛДАТЕ». Худ. фильм 1959 г., реж. Г. Чухрай.",
        "2. «ВЕСНА». Худ. фильм 1947 г., реж. Г. Александров.",
        "Следующий абзац обычного текста остаётся в чанке.",
    ])
    stats = {}
    units = chunker_mod._split_prose_units(content, 0, stats)
    text = "\n\n".join(u["text"] for u in units)
    assert "Худ. фильм" not in text and "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ" not in text
    assert "Следующий абзац обычного текста остаётся в чанке." in text
    assert "Основной текст параграфа" in text
    assert stats["dropped_blocks"]["apparatus"]["count"] == 3


def test_split_prose_units_cuts_rubric_on_last_line_of_paragraph():
    long_para = "Логика изучает формы и законы правильного мышления человека. " * 6
    content = long_para.strip() + "\nДополнительные материалы"
    stats = {}
    units = chunker_mod._split_prose_units(content, 0, stats)
    assert [u["text"] for u in units] == [long_para.strip()]
    assert stats["dropped_blocks"]["apparatus"]["count"] == 1
    assert stats["dropped_blocks"]["apparatus"]["chars"] == len("Дополнительные материалы")


def test_inset_is_one_chunk_up_to_hard_cap():
    paras = [_sentences(870) for _ in range(4)]
    inset = "\n\n".join(paras)
    assert 3400 < len(inset) < chunker_mod.TEXTBOOK_LIST_HARD_CAP
    units = chunker_mod._split_prose_units(inset, 0, {}, node_block_type="inset")
    assert len(units) == 1 and units[0]["block_type"] == "inset" and units[0]["text"] == inset
    big = "\n\n".join([_sentences(870) for _ in range(8)])
    assert len(big) > chunker_mod.TEXTBOOK_LIST_HARD_CAP
    parts = chunker_mod._split_prose_units(big, 0, {}, node_block_type="inset")
    assert len(parts) >= 2 and all(u["block_type"] == "inset" for u in parts)
    assert all(len(u["text"]) <= chunker_mod.TEXTBOOK_MAX_CHARS for u in parts)


def test_split_long_paragraph_balances_parts():
    para = _sentences(2365)
    assert len(para) > chunker_mod.TEXTBOOK_MAX_CHARS
    parts = chunker_mod._split_long_paragraph(para)
    assert len(parts) == 2 and all(t == "body" for _p, t in parts)
    a, b = (len(p) for p, _t in parts)
    assert abs(a - b) < 200, (a, b)
    assert " ".join(p for p, _t in parts) == para


def test_drop_apparatus_sections_extra_materials_mixed_and_glossary():
    tree = {"title": "", "level": 0, "content": "", "children": [
        {"title": "§ 1 Восстановление экономики", "level": 2, "content": "Текст параграфа.", "children": [
            {"title": "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ", "level": 3, "content": "", "children": [
                {"title": "Врезка", "level": 4, "block_type": "inset",
                 "content": "1. «ЗОЛУШКА». Худ. фильм 1947 г.", "children": []},
            ]},
            {"title": "Врезка", "level": 3, "block_type": "inset",
             "content": "Дополнительные материалы\nСовременная экономика и политика Банка России.", "children": []},
            {"title": "Подпись к иллюстрации", "level": 3, "block_type": "caption",
             "content": "Дополнительные материалы\nQR-код", "children": []},
            {"title": "Врезка", "level": 3, "block_type": "inset_mixed",
             "content": "Штаб-квартира Центральный офис компании", "children": []},
            {"title": "РОССИЯ МИР", "level": 3, "block_type": "inset",
             "content": "• 1946 г. — преобразование наркоматов", "children": []},
        ]},
        {"title": "Словарь терминов", "level": 1, "content": "", "children": [
            {"title": "Врезка", "level": 2, "block_type": "inset",
             "content": "Демократия — форма правления, при которой власть принадлежит народу.", "children": []},
        ]},
    ]}
    stats = {}
    out = chunker_mod._drop_apparatus_sections(tree, stats)
    s1, gloss = out["children"]
    assert [ch["title"] for ch in s1["children"]] == ["РОССИЯ МИР"]
    assert stats["dropped_blocks"]["apparatus"]["count"] == 3
    assert stats["dropped_blocks"]["inset_mixed"]["count"] == 1
    assert gloss["block_type"] == "glossary"
    assert gloss["children"][0]["block_type"] == "glossary"


def test_clean_prose_heading_keeps_abbreviation_dot():
    assert chunker_mod._clean_prose_heading("Глава I СССР В 1945—1991 гг.") == "Глава I СССР В 1945—1991 гг."
    assert chunker_mod._clean_prose_heading("Россия в XIX в.") == "Россия в XIX в."
    assert chunker_mod._clean_prose_heading("Культура до н. э.") == "Культура до н. э."
    assert chunker_mod._clean_prose_heading("Реформы 1861 г.") == "Реформы 1861 г."
    assert chunker_mod._clean_prose_heading("1 Влияние Победы.") == "1 Влияние Победы"
    assert chunker_mod._clean_prose_heading("§ 3 Наука.") == "§ 3 Наука"
    assert chunker_mod._clean_prose_heading("Наука, искусство и др.") == "Наука, искусство и др."


def test_strip_inset_title_lines():
    tree = {"title": "", "children": [
        {"title": "РОССИЯ МИР", "block_type": "inset", "content": "РОССИЯ МИР\n• 1946 г. — выборы", "children": []},
        {"title": "Вознесенский Николай Алексеевич", "block_type": "inset",
         "content": "ПОРТРЕТ НА ФОНЕ ЭПОХИ\nВознесенский\nНиколай\nАлексеевич\n(1903—1950)", "children": []},
        {"title": "Интересно", "block_type": "inset", "content": "Интересно", "children": []},
        {"title": "§ 1 Раздел", "content": "§ 1 Раздел\nТекст", "children": [
            {"title": "Мнение учёного", "block_type": "inset", "content": "МНЕНИЕ УЧЁНОГО.\n«Цитата»", "children": []},
        ]},
    ]}
    chunker_mod._strip_inset_title_lines(tree)
    russia, bio, alone, section = tree["children"]
    assert russia["content"] == "• 1946 г. — выборы"
    assert bio["content"].startswith("ПОРТРЕТ НА ФОНЕ ЭПОХИ\nВознесенский")
    assert alone["content"] == "Интересно"
    assert section["content"] == "§ 1 Раздел\nТекст"           # не врезка — не трогаем
    assert section["children"][0]["content"] == "«Цитата»"      # вложенная, регистр и точка не важны


# ─── чистка и склейка врезок (_fix_inset_nodes) ───────────────────────


def _inset(content, title="Врезка", page=1, block_type="inset"):
    return {"title": title, "level": 3, "block_type": block_type, "content": content,
            "page_start": page, "page_end": page, "children": []}


def test_fix_inset_nodes_merges_page_break_continuation():
    first = _inset("Хрущёв стремился к переменам, однако", title="ПОРТРЕТ", page=69)
    second = _inset("многие его действия были непоследовательны и вызывали недовольство.", page=70)
    tree = {"title": "", "children": [{"title": "§ 9", "level": 2, "content": "", "children": [first, second]}]}
    stats = {}
    chunker_mod._fix_inset_nodes(tree, stats)
    kids = tree["children"][0]["children"]
    assert len(kids) == 1
    assert kids[0]["content"] == "Хрущёв стремился к переменам, однако многие его действия были непоследовательны и вызывали недовольство."
    assert kids[0]["page_start"] == 69 and kids[0]["page_end"] == 70
    assert stats["inset_merges"]["page_break"]["count"] == 1


def test_fix_inset_nodes_hyphen_join_and_no_merge_cases():
    tree = {"title": "", "children": [_inset("Развитие экономики продолжа-", page=1), _inset("лось высокими темпами.", page=2)]}
    chunker_mod._fix_inset_nodes(tree, {})
    assert [k["content"] for k in tree["children"]] == ["Развитие экономики продолжалось высокими темпами."]
    # Хроника и подпись к фото на следующей странице — не продолжение.
    chron = _inset("• 1960 г. — избрание президентом", page=60)
    photo = _inset("Похороны И. Сталина. Москва. 1953 г. Траурная процессия.", page=61)
    # Подписи не склеиваются, даже если вторая со строчной.
    cap1 = _inset("Плакат «Новая пятилетка", block_type="caption", page=62)
    cap2 = _inset("великой стройки». 1948 г.", block_type="caption", page=63)
    # Именованная вторая врезка — отдельная.
    named1 = _inset("Текст без конца предложения и", page=64)
    named2 = _inset("продолжение с названием", title="МНЕНИЕ УЧЁНОГО", page=65)
    tree = {"title": "", "children": [chron, photo, cap1, cap2, named1, named2]}
    stats = {}
    chunker_mod._fix_inset_nodes(tree, stats)
    assert len(tree["children"]) == 6
    assert "inset_merges" not in stats


def test_fix_inset_nodes_cuts_numbered_tasks_and_drops_materials():
    body = "После смерти Сталина произошли изменения в высших эшелонах власти. " * 3
    tail = "\n4. Какие последствия имели эти изменения?"
    keep = _inset(body.strip() + tail)
    stub = _inset("ми. 4. Какие последствия имела реформа? 5. Почему?")
    films = _inset("3. «ЛЕОНИД БРЕЖНЕВ». Статья на сайте История.РФ.\n4. «ВЕСНА». Худ. фильм 1947 г.")
    tree = {"title": "", "children": [keep, stub, films]}
    stats = {}
    chunker_mod._fix_inset_nodes(tree, stats)
    assert [k["content"] for k in tree["children"]] == [body.strip()]
    ex = stats["dropped_blocks"]["exercise"]
    assert ex["count"] == 2 and ex["chars"] == len(tail) + len(stub["content"])
    assert stats["dropped_blocks"]["apparatus"]["count"] == 1
    # Только хвост: отдельная проверка суммы для первой врезки.
    one = {"title": "", "children": [_inset(body.strip() + tail)]}
    st1 = {}
    chunker_mod._fix_inset_nodes(one, st1)
    assert st1["dropped_blocks"]["exercise"]["chars"] == len(tail)


def test_drop_apparatus_sections_key_terms_rubric_hoists_prose():
    """«Ключевые понятия» первым ребёнком § — выбрасывается
    только список терминов, изложение и подузлы рубрики уходят в параграф."""
    prose = "Люди строят дома и разбивают сады. Деятельность отличает человека."
    tree = {"title": "", "level": 0, "content": "", "children": [
        {"title": "§ 4 Деятельность человека", "level": 2, "content": "Эпиграф.", "children": [
            {"title": "Ключевые понятия", "level": 3,
             "content": "• деятельность • ответственность\n\n• свобода • смысл жизни\n\n" + prose,
             "children": [
                 {"title": "ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?", "level": 4, "content": "Текст раздела.", "children": []},
             ]},
            {"title": "ВИДЫ ДЕЯТЕЛЬНОСТИ", "level": 3, "content": "Виды.", "children": []},
            # Не первым ребёнком — прежнее правило: поддерево выбрасывается.
            {"title": "КЛЮЧЕВЫЕ ПОНЯТИЯ", "level": 3, "content": "• a • b\n\nХвост.", "children": []},
        ]},
    ]}
    stats = {}
    out = chunker_mod._drop_apparatus_sections(tree, stats)
    sec = out["children"][0]
    assert sec["content"] == "Эпиграф.\n\n" + prose
    assert "•" not in sec["content"]
    assert [ch["title"] for ch in sec["children"]] == ["ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?", "ВИДЫ ДЕЯТЕЛЬНОСТИ"]
    kt = stats["dropped_blocks"]["key_terms"]
    assert kt["count"] == 1
    assert kt["chars"] == len("• деятельность • ответственность") + len("• свобода • смысл жизни")
    assert stats["dropped_blocks"]["front_matter"]["count"] == 1
    assert "Хвост." not in str(out)


def test_drop_apparatus_sections_key_terms_rubric_without_prose_and_empty_content():
    tree = {"title": "§ 1 Общество", "level": 2, "content": "", "children": [
        {"title": "Ключевые слова", "level": 3, "content": "общество, группа, норма", "children": []},
        {"title": "СОЦИАЛЬНЫЕ ГРУППЫ", "level": 3, "content": "Текст.", "children": []},
    ]}
    stats = {}
    out = chunker_mod._drop_apparatus_sections(tree, stats)
    assert out["content"] == ""
    assert [ch["title"] for ch in out["children"]] == ["СОЦИАЛЬНЫЕ ГРУППЫ"]
    assert stats["dropped_blocks"]["key_terms"]["chars"] == len("общество, группа, норма")


# ─── эпиграф под названием § ────────────────────────


def _sec_tree(intro, *, inset_first=False):
    section = {"title": "ЧТО ТАКОЕ ПРАВО?", "level": 3, "children": [],
               "content": "Мы живём в обществе, где поведение людей регулируется нормами. " * 3}
    children = [section]
    if inset_first:
        children.insert(0, {"title": "Врезка", "level": 3, "block_type": "inset", "children": [],
                            "content": "Право — совокупность общеобязательных правил, установленных государством."})
    return {"title": "", "level": 0, "content": "", "children": [
        {"title": "§ 10 Нормы и отрасли права", "level": 2, "content": intro, "children": children},
    ]}


def test_push_short_intro_down_moves_epigraph_into_first_section():
    tree = _sec_tree("Право есть искусство добра и справедливости.\nПублий Ювентий Цельс")
    chunker_mod._push_short_intro_down(tree)
    sec = tree["children"][0]
    assert sec["content"] == ""
    assert sec["children"][0]["content"].startswith(
        "Право есть искусство добра и справедливости.\nПублий Ювентий Цельс\n\nМы живём"
    )


def test_push_short_intro_down_skips_inset_before_section():
    tree = _sec_tree("Право есть искусство добра и справедливости.\nПублий Ювентий Цельс", inset_first=True)
    chunker_mod._push_short_intro_down(tree)
    inset, section = tree["children"][0]["children"]
    assert inset["content"].startswith("Право — совокупность")
    assert section["content"].startswith("Право есть искусство добра")
    assert tree["children"][0]["content"] == ""


def test_push_short_intro_down_keeps_fragment_and_chunker_drops_it():
    fragment = "? Обрывок нача-"
    tree = _sec_tree(fragment)
    probe = _sec_tree(fragment)
    chunker_mod._push_short_intro_down(probe)
    assert probe["children"][0]["content"] == fragment
    assert not probe["children"][0]["children"][0]["content"].startswith("?")
    stats = {}
    chunks = chunker_mod.create_llm_chunks(tree, min_size=30, document_name="x", document_kind="textbook", stats=stats)
    assert chunks and not any("Обрывок" in c["fragment_data"]["content"] for c in chunks)
    assert stats["dropped_blocks"]["debris"]["count"] >= 1
