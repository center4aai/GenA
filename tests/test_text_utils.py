"""Тесты текстовых эвристик чанкера прозы (chunker/text_utils.py)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if not _CHUNKER.is_dir():
    _CHUNKER = _REPO
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

# pytest re-inserts the repo root at sys.path[0] before importing every test
# module, so pin the canonical chunker modules in sys.modules while chunker/
# is still first — otherwise the root shim shadows them for later test files.
import docx2json_outline  # noqa: E402,F401
import main as chunker_mod  # noqa: E402,F401
import text_utils as tu  # noqa: E402


# ─── split_sentences ──────────────────────────────────────────────────


def test_split_sentences_keeps_year_abbreviation_inside_sentence():
    text = "Указом Президента в 2022 г. утверждены Основы политики. Это второе предложение."
    assert tu.split_sentences(text) == [
        "Указом Президента в 2022 г. утверждены Основы политики.",
        "Это второе предложение.",
    ]


def test_split_sentences_year_abbreviation_can_end_sentence():
    text = "Первые пользователи увидели портал «Госуслуги» в 2009 г. Сайт носил информационный характер."
    assert tu.split_sentences(text) == [
        "Первые пользователи увидели портал «Госуслуги» в 2009 г.",
        "Сайт носил информационный характер.",
    ]


def test_split_sentences_initials_and_city_are_not_boundaries():
    text = "Л. Н. Толстой родился в 1828 году. Съезд прошёл в г. Москва. М. Лазарев открыл Антарктиду."
    assert tu.split_sentences(text) == [
        "Л. Н. Толстой родился в 1828 году.",
        "Съезд прошёл в г. Москва.",
        "М. Лазарев открыл Антарктиду.",
    ]


def test_split_sentences_non_closing_and_closing_abbreviations():
    text = "Это касается прав, т.е. возможностей, т. к. иначе нельзя. См. рис. 5 и табл. 2. Дальше и т. д. Новое предложение."
    assert tu.split_sentences(text) == [
        "Это касается прав, т.е. возможностей, т. к. иначе нельзя.",
        "См. рис. 5 и табл. 2.",
        "Дальше и т. д.",
        "Новое предложение.",
    ]


def test_split_sentences_short_fragment_before_century_is_not_a_sentence():
    assert tu.split_sentences("В XIX в. Россия расширялась. В 480 г. до н. э. греки победили.") == [
        "В XIX в. Россия расширялась.",
        "В 480 г. до н. э. греки победили.",
    ]


def test_split_sentences_list_numbering_is_not_a_boundary():
    assert tu.split_sentences("1. Что такое сознание?\n2. Чем мировоззрение отличается от убеждений?") == [
        "1. Что такое сознание?",
        "2. Чем мировоззрение отличается от убеждений?",
    ]


def test_split_sentences_question_and_exclamation():
    assert tu.split_sentences("Почему так? Потому что! Вот… и всё.") == [
        "Почему так?",
        "Потому что!",
        "Вот… и всё.",
    ]


def test_split_sentences_empty():
    assert tu.split_sentences("") == []
    assert tu.split_sentences("   ") == []


# ─── lists / definitions ──────────────────────────────────────────────


def test_list_item_detection():
    assert tu.is_list_item("1. Первый пункт")
    assert tu.is_list_item("а) второй пункт")
    assert tu.is_list_item("• маркер")
    assert tu.is_list_item("2) скобка")
    assert not tu.is_list_item("Обычный абзац.")
    assert not tu.is_list_item("1998 год стал переломным.")


def test_definition_paragraph_detection():
    assert tu.is_definition_paragraph("Государство — институт управления обществом.")
    assert tu.is_definition_paragraph("«Общество» – совокупность людей.")
    assert not tu.is_definition_paragraph("обычный абзац — со строчной буквы")
    assert not tu.is_definition_paragraph("Обычный абзац без тире.")


def test_list_intro_detection():
    assert tu.is_list_intro("К признакам государства относятся:")
    assert tu.is_list_intro("Короткий вводный абзац.")
    assert not tu.is_list_intro("1. Это пункт, а не вводный абзац")
    assert not tu.is_list_intro("Д" * 400 + ".")


# ─── exercises ────────────────────────────────────────────────────────


def test_exercise_paragraph_imperatives():
    assert tu.is_exercise_paragraph(
        "Выясните, какие государственные услуги вы можете получить с 14 лет. "
        "Какими государственными услугами вы уже пользовались?"
    )
    assert tu.is_exercise_paragraph("⬤ Узнайте, что такое движение неприсоединения.")
    assert tu.is_exercise_paragraph("3. Составьте опросный лист о повседневной жизни советского человека.")
    assert tu.is_exercise_paragraph("6*. Напишите (в тетради) эссе об одном дне из жизни советского человека.")
    assert tu.is_exercise_paragraph("Вспомните из курсов истории имена выдающихся дипломатов. Какой след они оставили?")
    assert tu.is_exercise_paragraph("Используя карту, объясните причины миграции.")
    assert tu.is_exercise_paragraph("Как вы думаете, почему семья играет важнейшую роль?")


def test_exercise_paragraph_all_questions():
    assert tu.is_exercise_paragraph("Что такое выборы? Чем они отличаются от референдума?")


def test_exercise_paragraph_negatives():
    assert not tu.is_exercise_paragraph(
        "Используя новые технологии, компании снижают издержки и выходят на новые рынки."
    )
    assert not tu.is_exercise_paragraph(
        "Активная жизненная позиция — когда ты сам решаешь, как жить, берёшь на себя ответственность."
    )
    assert not tu.is_exercise_paragraph("Что же такое общество? Ответ на этот вопрос не так прост.")
    assert not tu.is_exercise_paragraph(
        "Учёные подсчитали, что за один год человечество производит столько же информации, "
        "сколько за всю предыдущую историю."
    )


# ─── headings ─────────────────────────────────────────────────────────


def test_heading_kind_exercise():
    for title in (
        "ВОПРОСЫ И ЗАДАНИЯ",
        "Проверяем себя",
        "Вопросы и задания к главе 3",
        "Работаем с ХРОНОЛОГИЕЙ",
        "Работаем с источником",
        "Практикум",
        "ИССЛЕДУЕМ",
        "Учителю и ученику",
        "§ 5. Вопросы для самоконтроля",
    ):
        assert tu.heading_kind(title) == "exercise", title


def test_heading_kind_front_matter():
    for title in (
        "Содержание",
        "ОГЛАВЛЕНИЕ",
        "Введение",
        "Предисловие",
        "Как работать с учебником",
        "Список сокращений",
        "Условные обозначения",
        "КЛЮЧЕВЫЕ ПОНЯТИЯ",
        "Ответы",
        "Литература",
    ):
        assert tu.heading_kind(title) == "front_matter", title


def test_heading_kind_glossary_and_content():
    assert tu.heading_kind("Словарь терминов") == "glossary"
    assert tu.heading_kind("Основные понятия") == "glossary"
    assert tu.heading_kind("Глоссарий") == "glossary"
    for title in (
        "§ 4. Традиционные ценности российского общества",
        "Введение в макроэкономику",
        "Подведём итоги",
        "Это интересно",
        "Глава I. Человек и общество",
        "",
    ):
        assert tu.heading_kind(title) is None, title


def test_heading_kind_env_overrides(monkeypatch):
    monkeypatch.setenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS", "Введение")
    assert tu.heading_kind("Введение") is None
    monkeypatch.setenv("CHUNKER_TEXTBOOK_DROP_HEADINGS", "Хронология")
    assert tu.heading_kind("Хронология") == "front_matter"


# ─── structural front matter ──────────────────────────────────────────


def test_toc_block_detection():
    toc = (
        "Введение . . . . . . . 3\n"
        "Глава 1. Личность и общество . . . . 6\n"
        "§ 1. Что делает человека человеком? 6\n"
        "§ 2. Человек, общество, природа 12"
    )
    assert tu.is_toc_block(toc)
    assert not tu.is_toc_block("Обычный абзац.\nВторая строка.\nТретья строка.")


def test_imprint_and_abbreviation_detection():
    assert tu.is_imprint_block(
        "Рецензенты: доктор наук И. И. Иванов. Учебник для 9 класса. Издательство «Просвещение»."
    )
    assert not tu.is_imprint_block("Общество — совокупность людей, объединённых общими интересами.")
    assert tu.is_abbreviation_list(
        "АО — акционерное общество\nГК — Гражданский кодекс\nМФЦ — многофункциональный центр\nРФ — Российская Федерация"
    )
    assert tu.classify_dropped_paragraph("Выясните, какие услуги доступны с 14 лет.") == "exercise"
    assert tu.classify_dropped_paragraph("Государство выполняет внешние и внутренние функции.") is None


# ─── exercises glued into a paragraph ─────────────────────────────────


def test_strip_exercise_lines_cuts_task_run_until_sentence_end():
    para = (
        "Число пользователей порталом «Госуслуги» к концу 2025 г. составило более 120 млн человек.\n"
        "Выясните, какие государственные услуги вы можете получить с 14 лет. Какими\n"
        "государственными услугами вы уже пользовались?"
    )
    cleaned, cut = tu.strip_exercise_lines(para)
    assert cleaned == "Число пользователей порталом «Госуслуги» к концу 2025 г. составило более 120 млн человек."
    assert cut > 0


def test_strip_exercise_lines_keeps_body_after_bullet_task():
    para = (
        "Убедительную победу в командном зачёте одержала сборная СССР — 43 золотые медали.\n"
        "⬤ Какому виду спорта посвящена данная марка? Выясните имена советских спортсменов,\n"
        "получивших золотые медали в данном виде\n"
        "спорта на Олимпийских играх, которым\n"
        "посвящена данная марка.\n"
        "Лучшим на Олимпиаде в Мельбурне (1956) был назван советский бегун В. Куц."
    )
    cleaned, _cut = tu.strip_exercise_lines(para)
    assert cleaned == (
        "Убедительную победу в командном зачёте одержала сборная СССР — 43 золотые медали.\n"
        "Лучшим на Олимпиаде в Мельбурне (1956) был назван советский бегун В. Куц."
    )


def test_strip_exercise_lines_heading_line_drops_rest_of_paragraph():
    para = (
        "Советский народ одержал победу, но война лишила счастья миллионы людей.\n"
        "Вопросы и задания\n"
        "1. Перечислите категории населения, положение которых улучшилось.\n"
        "2. Составьте опросный лист о повседневной жизни."
    )
    cleaned, cut = tu.strip_exercise_lines(para)
    assert cleaned == "Советский народ одержал победу, но война лишила счастья миллионы людей."
    assert cut > 0
    assert tu.is_exercise_paragraph("Вопросы и задания\n1. Перечислите категории населения.")


def test_strip_exercise_lines_leaves_plain_prose_alone():
    para = "Первая строка изложения.\nВторая строка продолжает мысль и ничего не поручает."
    assert tu.strip_exercise_lines(para) == (para, 0)
    assert tu.strip_exercise_lines("Одна строка.") == ("Одна строка.", 0)


def test_strip_inline_labels_removes_glued_rubric_label():
    text = "Первые пользователи увидели портал «Госуслуги» в 2009 г. Сайт ИНТЕРЕСНО\nносил информационный характер."
    assert tu.strip_inline_labels(text) == (
        "Первые пользователи увидели портал «Госуслуги» в 2009 г. Сайт\nносил информационный характер."
    )
    # A label that starts its own line is a real heading and stays.
    assert tu.strip_inline_labels("Текст.\nВАЖНО\nследующая строка") == "Текст.\nВАЖНО\nследующая строка"
    # Glued label before a paragraph break is still a glued label.
    assert tu.strip_inline_labels("портал появился давно. Сайт ИНТЕРЕСНО\n\nносил справочный характер.") == (
        "портал появился давно. Сайт\n\nносил справочный характер."
    )


def test_strip_exercise_lines_handles_bullet_in_the_middle_of_a_line():
    para = "Участники КВН готовятся к выступлению. Москва. 1965 г. ⬤ Выясните, долго ли просуществовала\nтелепрограмма, участники которой изображены на фотографии.\nВ условиях холодной войны получил распространение «тамиздат»."
    cleaned, cut = tu.strip_exercise_lines(para)
    assert cleaned == "Участники КВН готовятся к выступлению. Москва. 1965 г.\nВ условиях холодной войны получил распространение «тамиздат»."
    assert cut > 0


def test_strip_table_lines_drops_markdown_table_rows_and_lone_markers():
    para = (
        "Золотой молот символизировал рабочий класс, золотой венок пшеницы — крестьянство.\n"
        "| ⬤ В каком | году | была выпущена | данная | | |\n"
        "| ------------ | ----- | --------------- | -------- | --- | --- |\n"
        "⬤\n"
        "Советская и китайская помощь переломила ситуацию."
    )
    cleaned, cut = tu.strip_table_lines(para)
    assert cleaned == (
        "Золотой молот символизировал рабочий класс, золотой венок пшеницы — крестьянство.\n"
        "Советская и китайская помощь переломила ситуацию."
    )
    assert cut > 0
    plain = "Строка одна.\nСтрока два."
    assert tu.strip_table_lines(plain) == (plain, 0)
    # Text glued before the first cell is still a table row.
    assert tu.strip_table_lines("Изложение.\nсек тора? | | | Большие | перс\nЕщё изложение.") == (
        "Изложение.\nЕщё изложение.",
        len("сек тора? | | | Большие | перс"),
    )
    # A paragraph that is nothing but one table row disappears entirely.
    assert tu.strip_table_lines("| --- | --- | --- |") == ("", len("| --- | --- | --- |"))


def test_strip_table_marks_cleans_heading_turned_into_table_row():
    assert tu.strip_table_marks("§ 18. | | Хозяйственная | | | система | | --- | -------------- | --- |") == "§ 18. Хозяйственная система"
    assert tu.strip_table_marks("Обычный заголовок") == "Обычный заголовок"


def test_strip_inline_labels_leading_and_midline():
    assert tu.strip_inline_labels("ИНТЕРЕСНО Мышление — это активный процесс.") == "Мышление — это активный процесс."
    assert tu.strip_inline_labels("поддержки.\nИНТЕРЕСНО В пещере Шанидар найдены останки.") == (
        "поддержки.\nВ пещере Шанидар найдены останки."
    )
    assert tu.strip_inline_labels("собственными усилиями. ИНТЕРЕСНО Мышление — это процесс.") == (
        "собственными усилиями. Мышление — это процесс."
    )
    # Standalone heading line and a label used as a word inside a sentence stay.
    assert tu.strip_inline_labels("Текст.\nИНТЕРЕСНО\nдалее") == "Текст.\nИНТЕРЕСНО\nдалее"
    assert tu.strip_inline_labels("Это очень ВАЖНО для нас.") == "Это очень ВАЖНО для нас."


def test_caption_sentence_count_ignores_figure_number():
    assert tu.caption_sentence_count("Рис. 14. Здание Государственной Думы") == 1
    assert tu.caption_sentence_count("Рис. 3. Портрет П. А. Столыпина. Художник И. Е. Репин, 1910 г.") == 2
    assert tu.caption_sentence_count("С. Капица. Фотография. 1988 г.") == 3


def test_boundary_flags():
    assert tu.boundary_flags("Целое предложение.") == {"starts_mid_sentence": False, "ends_mid_sentence": False}
    assert tu.boundary_flags("обрывок в начале. Конец целый.") == {"starts_mid_sentence": True, "ends_mid_sentence": False}
    assert tu.boundary_flags("Начало целое, конец оборван") == {"starts_mid_sentence": False, "ends_mid_sentence": True}
    assert tu.boundary_flags("«цитата» со строчной внутри кавычек")["starts_mid_sentence"] is True
    assert tu.boundary_flags("1. пункт перечня.")["starts_mid_sentence"] is False
    assert tu.boundary_flags("К признакам относятся:")["ends_mid_sentence"] is False
    assert tu.boundary_flags("") == {"starts_mid_sentence": False, "ends_mid_sentence": False}


# ─── «Дополнительные материалы», словарь (тикет 71901445) ─────────────


def test_heading_kind_apparatus():
    for title in (
        "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ",
        "Дополнительные материалы",
        "Дополнительный материал",
        "Дополнительные материалы к параграфу",
        "Дополнительные материалы по теме: экономика",
        "Материалы для дополнительного чтения",
    ):
        assert tu.heading_kind(title) == "apparatus", title
    assert "apparatus" in tu.DROP_HEADING_KINDS
    assert {"exercise", "front_matter"} <= tu.DROP_HEADING_KINDS and "glossary" not in tu.DROP_HEADING_KINDS
    assert tu.heading_kind("Дополнительные материалы изучаются в школе") is None


def test_heading_kind_apparatus_keep_override(monkeypatch):
    monkeypatch.setenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS", "Дополнительные материалы")
    assert tu.heading_kind("ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ") is None
    assert not tu.is_apparatus_line("Дополнительные материалы")
    assert tu.classify_dropped_paragraph("ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ") != "apparatus"


def test_heading_kind_glossary_terms_and_concepts():
    assert tu.heading_kind("Словарь понятий и терминов") == "glossary"
    assert tu.heading_kind("Словарь терминов и понятий") == "glossary"
    assert tu.heading_kind("СЛОВАРЬ ПОНЯТИЙ") == "glossary"


def test_apparatus_line_and_paragraph():
    assert tu.is_apparatus_line("Дополнительные материалы")
    assert not tu.is_apparatus_line("дополнительные материалы по теме")       # перенос строки текста
    assert not tu.is_apparatus_line("Дополнительные материалы к параграфу " + "о" * 60)  # длиннее рубрики
    assert tu.classify_dropped_paragraph(
        "Логика и её законы.\nДоказательство и опровержение.\nДополнительные материалы"
    ) == "apparatus"
    assert tu.classify_dropped_paragraph(
        "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ\n1. «БАЛЛАДА О СОЛДАТЕ». Худ. фильм 1959 г., реж. Г. Чухрай."
    ) == "apparatus"
    assert tu.classify_dropped_paragraph(
        "Подробнее о законах логики можно найти\nдополнительные материалы по теме"
    ) is None
    # Длинный абзац с рубрикой последней строкой — не подпись к QR, целиком не выбрасывается.
    long_para = "Логика изучает формы и законы мышления. " * 10 + "\nДополнительные материалы"
    assert len(long_para) > tu.APPARATUS_CAPTION_MAX_CHARS
    assert not tu.is_apparatus_paragraph(long_para)


def test_strip_apparatus_lines_cuts_rubric_and_tail():
    para = "Первая строка абзаца заканчивается.\nДополнительные материалы\n1. «ВЕСНА». Худ. фильм 1947 г."
    kept, cut = tu.strip_apparatus_lines(para)
    assert kept == "Первая строка абзаца заканчивается."
    assert cut == len("Дополнительные материалы") + len("1. «ВЕСНА». Худ. фильм 1947 г.")
    # Рубрика после оборванной строки — это перенос фразы, не трогаем.
    para2 = "Здесь можно найти\nДополнительные материалы"
    assert tu.strip_apparatus_lines(para2) == (para2, 0)
    assert tu.strip_apparatus_lines("Обычный абзац.") == ("Обычный абзац.", 0)


# ─── номерные задания и списки материалов во врезках ──────────────────


def test_find_numbered_task_positions():
    text = "Текст врезки кончается.\n4. Какие последствия имела реформа?"
    assert tu.find_numbered_task(text) == text.index("4.")
    assert tu.find_numbered_task("ми. 4. Какие последствия имела реформа?") == 4
    assert tu.find_numbered_task("2) Почему так произошло?") == 0
    # Звёздочка — задание повышенной сложности, «?» не нужен.
    assert tu.find_numbered_task("6*. В исторической науке существуют разные оценки реформ.") == 0


def test_find_numbered_task_ignores_lists_and_dates():
    for text in (
        "• 1946 г. — выборы в Верховный Совет СССР",
        "1. «ВЕСНА». Худ. фильм 1947 г., реж. Г. Александров.",
        "Причины:\n1. Рост цен.\n2. Нехватка товаров.",
        "В 1953 г. 5 марта умер И. Сталин.",
        "",
    ):
        assert tu.find_numbered_task(text) is None, text


def test_strip_numbered_tasks_returns_head_and_cut():
    head = "После смерти Сталина произошли изменения в высших эшелонах власти."
    tail = "\n4. Какие последствия имели эти изменения?"
    assert tu.strip_numbered_tasks(head + tail) == (head, len(tail))
    assert tu.strip_numbered_tasks(head) == (head, 0)


def test_is_materials_list():
    assert tu.is_materials_list("3. «ЛЕОНИД БРЕЖНЕВ. ГЕНЕРАЛЬНЫЙ СЕКРЕТАРЬ». Статья на сайте История.РФ.")
    assert tu.is_materials_list("4. «ВЕСНА». Худ. фильм 1947 г.\n5. «ПОДРАНКИ». Худ. фильм 1976 г.")
    assert not tu.is_materials_list("• 1946 г. — выборы в Верховный Совет\n• 1947 г. — денежная реформа")
    assert not tu.is_materials_list("Фильм «Весна» снят в 1947 г. Статья на сайте не нужна.")  # нет пунктов «N. «…»


# ─── блочная рубрика «Ключевые понятия» ─────────────────────────────


def test_block_rubric_kind_variants():
    for title in (
        "КЛЮЧЕВЫЕ ПОНЯТИЯ",
        "Ключевые понятия:",
        "Ключевые слова",
        "Ключевые термины.",
        "Ключевые понятия и термины",
        "Ключевые слова и понятия",
        "Основные понятия параграфа",
        "Основные термины главы",
        "Основные понятия темы",
        "Основные термины урока",
        "§ 4. Ключевые понятия",
    ):
        assert tu.block_rubric_kind(title) == tu.BLOCK_RUBRIC_KIND == "key_terms", title
        # Для правил/DOCX и env-переопределений вид заголовка прежний.
        assert tu.heading_kind(title) == "front_matter", title
    for title in (
        "Ключевые идеи",
        "Ключевые понятия эпохи Петра",
        "Основные понятия",             # словарь, а не рубрика
        "Литература",
        "Оглавление",
        "ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?",
        "",
    ):
        assert tu.block_rubric_kind(title) is None, title


def test_block_rubric_kind_env_overrides(monkeypatch):
    # KEEP: рубрика становится обычным заголовком.
    monkeypatch.setenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS", "Ключевые понятия")
    assert tu.heading_kind("КЛЮЧЕВЫЕ ПОНЯТИЯ") is None
    assert tu.block_rubric_kind("КЛЮЧЕВЫЕ ПОНЯТИЯ") is None
    assert tu.block_rubric_kind("Ключевые слова") == "key_terms"
    monkeypatch.delenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS")
    # Явный DROP: секционная рубрика, как остальной front_matter.
    monkeypatch.setenv("CHUNKER_TEXTBOOK_DROP_HEADINGS", "Ключевые понятия")
    assert tu.heading_kind("КЛЮЧЕВЫЕ ПОНЯТИЯ") == "front_matter"
    assert tu.block_rubric_kind("КЛЮЧЕВЫЕ ПОНЯТИЯ") is None
    assert tu.block_rubric_kind("Ключевые термины") == "key_terms"
