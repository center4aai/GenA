"""Текстовые эвристики для прозы учебников: предложения, списки, задания, аппарат.

Чистый python без зависимостей — модуль переиспользуется чанкером (`main.py`),
layout-извлечением PDF и будущим LLM-чанкером.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Dict

# ---------------------------------------------------------------------------
# Предложения: точка не всегда конец предложения
# ---------------------------------------------------------------------------

# Сокращения, после которых предложение НЕ заканчивается (без завершающей точки,
# в нижнем регистре, внутренние точки сохранены: «т.к»).
NON_CLOSING_ABBREVIATIONS = frozenset({
    "т.е", "т.к", "т.н", "т.наз", "т.о", "т.ч",
    "см", "ср", "им", "ул", "пл", "просп", "пр-т", "пер", "наб", "д", "стр", "с",
    "ст", "п", "пп", "ч", "чч", "рис", "табл", "гл", "разд", "напр", "прим",
    "примеч", "млн", "млрд", "тыс", "руб", "коп", "проф", "акад", "доц", "св",
    "о", "оз", "р", "англ", "лат", "греч", "нем", "фр", "итал", "исп", "ок",
    "кв", "корп", "ред", "изд", "т", "тт", "экз", "вып", "ин-т", "ун-т", "мин",
    "сек", "м", "км", "кг", "га", "обл", "р-н", "с.-х", "физ", "хим", "мат",
    "ист", "букв", "напр", "др.-рус", "ст.-слав", "др.-греч", "н",
})

# Сокращения, которые МОГУТ завершать предложение: рвём, если дальше заглавная.
CLOSING_ABBREVIATIONS = frozenset({"гг", "вв", "др", "пр", "проч", "т.д", "т.п", "э"})

_ABBR_EXTRA = os.getenv("CHUNKER_ABBREVIATIONS_EXTRA", "")
if _ABBR_EXTRA.strip():
    NON_CLOSING_ABBREVIATIONS = NON_CLOSING_ABBREVIATIONS | frozenset(
        a.strip().rstrip(".").lower() for a in _ABBR_EXTRA.split(",") if a.strip()
    )

_SENTENCE_CANDIDATE_RE = re.compile(r'([.!?…]+)(["»)\]]*)[ \t]*(?:\n|[ \t])+(?=\S)')
_NUMBER_LIKE_RE = re.compile(r'^[\dIVXLCDM]+(?:-?[а-яё]{1,2})?$')
_SENTENCE_END_RE = re.compile(r'[.!?…]\s*["»)\]]?$')


def _opens_new_sentence(next_char: str) -> bool:
    """Начинается ли следующий токен так, как начинают предложение."""
    return not next_char.islower()


def _should_split(text: str, m: "re.Match", sent_start: int = 0) -> bool:
    punct = m.group(1)
    before = text[: m.start(1)]
    # Слишком короткий кусок («В XIX в.») — не самостоятельное предложение.
    piece_is_short = len(text[sent_start : m.start(1)].split()) < 4
    next_char = text[m.end()] if m.end() < len(text) else ""
    if not next_char:
        return True
    if "." not in punct or punct.startswith(("!", "?", "…")):
        # «!», «?», «…» — конец предложения, если дальше не продолжение со строчной.
        return _opens_new_sentence(next_char)
    if "..." in punct:
        return _opens_new_sentence(next_char)

    tokens = before.split()
    if not tokens:
        return _opens_new_sentence(next_char)
    raw_tok = tokens[-1]
    tok = raw_tok.lstrip('«("[\'"„')
    low = tok.lower().replace("ё", "е")
    if not tok:
        return _opens_new_sentence(next_char)

    # Инициал: «Л. Н. Толстой», «С. Капица».
    if len(tok) == 1 and tok.isalpha() and tok.isupper():
        return False

    prev = tokens[-2].lower().replace("ё", "е") if len(tokens) >= 2 else ""

    # Однобуквенные сокращения со своим контекстом (проверяются до общего
    # словаря: «д.» — это и «дом», и «т. д.»).
    if len(tok) == 1 and tok.isalpha() and tok.islower():
        if prev == "т.":
            # «т. д.», «т. п.» могут завершать предложение; «т. е.», «т. к.» — нет.
            if low in ("д", "п"):
                return _opens_new_sentence(next_char) and not piece_is_short
            return False
        if prev == "н." and low == "э":
            # «до н. э.» — часто конец предложения.
            return _opens_new_sentence(next_char) and not piece_is_short
        if low in ("г", "в"):
            # «2022 г.», «XIX в.» — год/век; «г. Москва» — город.
            if _NUMBER_LIKE_RE.match(prev.upper()):
                return _opens_new_sentence(next_char) and not piece_is_short
            return False
        return False

    if low in NON_CLOSING_ABBREVIATIONS:
        return False

    if low in CLOSING_ABBREVIATIONS:
        return _opens_new_sentence(next_char) and not piece_is_short

    if tok.isdigit():
        # «1.» в начале строки — нумерация списка, не конец предложения.
        line_start = before.rfind("\n") + 1
        if before[line_start:].strip() == raw_tok:
            return False
        return _opens_new_sentence(next_char)

    return _opens_new_sentence(next_char)


def split_sentences(text: str) -> List[str]:
    """Разбить абзац на предложения с учётом сокращений («г.», «т.к.», инициалы)."""
    text = (text or "").strip()
    if not text:
        return []
    sentences: List[str] = []
    start = 0
    for m in _SENTENCE_CANDIDATE_RE.finditer(text):
        if _should_split(text, m, start):
            piece = text[start : m.end(2)].strip()
            if piece:
                sentences.append(piece)
            start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


_CAPTION_PREFIX_RE = re.compile(
    r'^\s*(?:Рис\.|Рисунок|Илл\.|Иллюстрация|Табл\.|Таблица|Схема|Диаграмма|Карта|Фото|Фотография)\s*[\d.,–-]*\s*[.:]?\s*',
    re.IGNORECASE,
)


def caption_sentence_count(text: str) -> int:
    """Число предложений подписи без номера рисунка («Рис. 14.» — не предложение)."""
    return count_sentences(_CAPTION_PREFIX_RE.sub("", text or "", count=1))


def ends_like_sentence(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search((text or "").rstrip()))


_BOUNDARY_LOWER_START_RE = re.compile(r'^[«"„(\[\s]*[а-яёa-z]')


def boundary_flags(text: str) -> Dict[str, bool]:
    """Детерминированные признаки обрыва чанка: начало/конец на середине предложения.

    Начало — со строчной буквы (не маркер списка); конец — без знака конца
    предложения и не двоеточие перед перечнем. Та же логика продублирована в
    agent_api (chunk_gate._boundary_flags) — gate получает только текст чанка.
    """
    t = (text or "").strip()
    if not t:
        return {"starts_mid_sentence": False, "ends_mid_sentence": False}
    starts = bool(_BOUNDARY_LOWER_START_RE.match(t)) and not LIST_ITEM_RE.match(t)
    ends = not ends_like_sentence(t) and not t.endswith(":")
    return {"starts_mid_sentence": starts, "ends_mid_sentence": ends}


# ---------------------------------------------------------------------------
# Списки и определения
# ---------------------------------------------------------------------------

LIST_ITEM_RE = re.compile(
    r'^\s*(?:\(?\d{1,2}[.)]|\(?[а-яa-z][.)]|[•●▪■◆*–—-])\s+\S'
)
# «Термин — определение …» (термин с заглавной, не длиннее 80 символов).
DEFINITION_RE = re.compile(r'^[«"„]?[А-ЯЁA-Z][^\n—–]{1,80}[»"“]?\s+[—–]\s+\S')
LIST_INTRO_MAX_CHARS = 300


def is_list_item(paragraph: str) -> bool:
    return bool(LIST_ITEM_RE.match(paragraph or ""))


def is_definition_paragraph(paragraph: str) -> bool:
    p = (paragraph or "").strip()
    return bool(DEFINITION_RE.match(p)) and not is_list_item(p)


def is_list_intro(paragraph: str) -> bool:
    """Вводный абзац перед списком: заканчивается двоеточием или короткий."""
    p = (paragraph or "").strip()
    if not p or is_list_item(p):
        return False
    return p.endswith(":") or len(p) <= LIST_INTRO_MAX_CHARS


# ---------------------------------------------------------------------------
# Задания и вопросы, разбросанные по тексту
# ---------------------------------------------------------------------------

_IMPERATIVES = (
    "выясните|узнайте|вспомните|подумайте|обсудите|составьте|приведите|объясните|"
    "найдите|сравните|проанализируйте|прочитайте|назовите|опишите|охарактеризуйте|"
    "докажите|предположите|перечислите|определите|расскажите|запишите|заполните|"
    "подготовьте|изучите|рассмотрите|ответьте|выполните|выскажите|аргументируйте|"
    "оцените|проверьте|соотнесите|установите|напишите|нарисуйте|посмотрите|"
    "обоснуйте|подберите|сформулируйте|укажите|выберите|продолжите|дополните|"
    "вставьте|расположите|распределите|разделите|предложите|представьте|"
    "придумайте|проведите|выпишите|отметьте|поразмышляйте|поразмышляй|"
    "попробуйте|попытайтесь|обратитесь|воспользуйтесь|прокомментируйте|"
    "проиллюстрируйте|раскройте|докажи|объясни|назови|вспомни|подумай|выясни|узнай"
)
_QUESTION_OPENERS = "как вы думаете|как вы считаете|согласны ли вы|как вы понимаете|что вы знаете"
_GERUND_OPENERS = "используя|опираясь|обратившись|исходя|основываясь|пользуясь|применяя"

_MARKER = r'(?:[⬤●•▪■◆*]|\d{1,2}\*?[.)]|\d{1,2}[.)]\*)?\s*'
EXERCISE_START_RE = re.compile(
    r'^\s*' + _MARKER + r'(?:' + _IMPERATIVES + r'|' + _QUESTION_OPENERS + r')\b',
    re.IGNORECASE,
)
_GERUND_START_RE = re.compile(r'^\s*' + _MARKER + r'(?:' + _GERUND_OPENERS + r')\b', re.IGNORECASE)
_IMPERATIVE_ANYWHERE_RE = re.compile(r'\b(?:' + _IMPERATIVES + r')\b', re.IGNORECASE)
_TASK_BULLET_RE = re.compile(r'^\s*[⬤●]\s*\S')
EXERCISE_MAX_QUESTION_SENTENCES = 4


def is_exercise_paragraph(paragraph: str) -> bool:
    """Абзац — учебное задание/вопрос, а не изложение.

    Ловит поручения в начале абзаца («Выясните…», «⬤ Узнайте…», «1. Составьте…»),
    деепричастные вводы с императивом дальше («Используя карту, объясните…»)
    и короткие абзацы, целиком состоящие из вопросов.
    """
    p = (paragraph or "").strip()
    if not p:
        return False
    if EXERCISE_START_RE.match(p):
        return True
    first_line = p.split("\n", 1)[0].strip()
    if len(first_line) <= 60 and heading_kind(first_line) == "exercise":
        # «Вопросы и задания» строчными не стало заголовком дерева, но абзац
        # под ним — те же задания.
        return True
    if _GERUND_START_RE.match(p) and (_IMPERATIVE_ANYWHERE_RE.search(p) or p.endswith("?")):
        return True
    if _TASK_BULLET_RE.match(p) and (p.endswith("?") or _IMPERATIVE_ANYWHERE_RE.search(p)):
        return True
    sentences = split_sentences(p)
    if sentences and len(sentences) <= EXERCISE_MAX_QUESTION_SENTENCES and all(
        s.rstrip('"»)').endswith("?") for s in sentences
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Разделы-аппарат по заголовкам
# ---------------------------------------------------------------------------

_EXERCISE_HEADING_RE = re.compile(
    r'^(?:вопросы\s+и\s+задания|вопросы\s+и\s+упражнения|вопросы\s+для\s+(?:самоконтроля|повторения|самопроверки|обсуждения)|'
    r'проверяем\s+себя|проверь(?:те)?\s+себя|исследуем|рассуждаем|практикум|работаем\s+с\s+.*|'
    r'учителю\s+и\s+ученику|подумайте|обсудите|обсуждаем|думаем|сравниваем|анализируем|'
    r'повторяем|повторение|задания|упражнения|вопросы|итоговые\s+задания|проектные\s+задания|'
    r'проекты|темы\s+(?:проектов|докладов|рефератов|сообщений)|задания\s+для\s+самоконтроля|'
    r'контрольные\s+вопросы|проверочные\s+задания|тест(?:ы|овые\s+задания)?|самостоятельная\s+работа|'
    r'лабораторная\s+работа|практическая\s+работа|домашнее\s+задание)'
    r'(?:\s*[.:!]?\s*$|\s+(?:к|по|для|на|с|со)\s+.*$)'
)
_FRONT_BACK_HEADING_RE = re.compile(
    r'^(?:содержание|оглавление|введение|предисловие|вступление|как\s+работать\s+с\s+(?:учебником|книгой|пособием)|'
    r'дорогие\s+(?:ребята|друзья|учащиеся|школьники|старшеклассники)|юные\s+друзья|от\s+авторов?|от\s+редакци[иа]|'
    r'обращение\s+к\s+.*|список\s+сокращений|сокращения|принятые\s+сокращения|условные\s+обозначения|'
    r'ответы(?:\s+к\s+заданиям)?|литература|список\s+литературы|рекомендуемая\s+литература|'
    r'дополнительная\s+литература|интернет-ресурсы|источники|библиография|'
    r'ключевые\s+(?:понятия|слова|термины)(?:\s+и\s+(?:понятия|термины))?|'
    r'основные\s+(?:понятия|термины)\s+(?:параграфа|главы|темы|урока)|список\s+(?:иллюстраций|таблиц|карт))'
    r'\s*[.:!]?\s*$'
)
# Рубрика в конце параграфа (список фильмов, ссылка-QR на материалы) — не
# изложение: выбрасывается целиком вместе с тем, что под ней.
_APPARATUS_HEADING_RE = re.compile(
    r'^(?:дополнительн(?:ые|ый)\s+материал(?:ы)?|материалы\s+для\s+дополнительного\s+(?:чтения|изучения))'
    r'(?:\s*[.:!]?\s*$|\s+(?:к|по|для)\s+.*$)'
)
_GLOSSARY_HEADING_RE = re.compile(
    r'^(?:словарь(?:\s+(?:терминов|понятий|основных\s+понятий)(?:\s+и\s+(?:терминов|понятий))?)?|краткий\s+словарь(?:\s+терминов)?|'
    r'основные\s+понятия(?:\s+и\s+термины)?|термины(?:\s+и\s+понятия)?|понятия\s+и\s+термины|'
    r'глоссарий|основные\s+термины|терминологический\s+словарь|словарь-справочник)\s*[.:!]?\s*$'
)
_HEADING_PREFIX_RE = re.compile(r'^(?:§\s*[\d–—-]+\.?|глава\s+[ivxlcdm\d]+\.?|раздел\s+[ivxlcdm\d]+\.?|\d{1,3}\.)\s*', re.IGNORECASE)


def normalize_heading(title: str) -> str:
    s = re.sub(r'\s+', ' ', (title or "").replace("\xa0", " ")).strip().lower().replace("ё", "е")
    s = _HEADING_PREFIX_RE.sub("", s).strip()
    return s


def _heading_set(raw: str) -> frozenset:
    return frozenset(normalize_heading(x) for x in (raw or "").split(",") if x.strip())


def heading_kind(title: str) -> Optional[str]:
    """Тип раздела по заголовку: exercise | front_matter | apparatus | glossary | None.

    apparatus — «Дополнительные материалы» (фильмы, QR-ссылки): выбрасывается
    так же, как exercise/front_matter, но со своим счётчиком.

    Переопределения через env: CHUNKER_TEXTBOOK_DROP_HEADINGS (точные заголовки,
    которые выбрасывать) и CHUNKER_TEXTBOOK_KEEP_HEADINGS (никогда не выбрасывать).
    """
    s = normalize_heading(title)
    if not s:
        return None
    if s in _heading_set(os.getenv("CHUNKER_TEXTBOOK_KEEP_HEADINGS", "")):
        return None
    if s in _heading_set(os.getenv("CHUNKER_TEXTBOOK_DROP_HEADINGS", "")):
        return "front_matter"
    if _GLOSSARY_HEADING_RE.match(s):
        return "glossary"
    if _EXERCISE_HEADING_RE.match(s):
        return "exercise"
    if _FRONT_BACK_HEADING_RE.match(s):
        return "front_matter"
    if _APPARATUS_HEADING_RE.match(s):
        return "apparatus"
    return None


# Виды разделов, которые выбрасываются целиком (поддерево, врезка, абзац).
DROP_HEADING_KINDS = frozenset({"exercise", "front_matter", "apparatus"})

# Блочная рубрика в начале параграфа («КЛЮЧЕВЫЕ ПОНЯТИЯ» + список терминов):
# выбрасывается только вместе со списком под ней, а изложение после неё —
# это текст параграфа, а не раздел-аппарат. По heading_kind она остаётся
# front_matter (DOCX/правила и переопределения env работают как раньше).
_BLOCK_RUBRIC_RE = re.compile(
    r'^(?:ключевые\s+(?:понятия|слова|термины)(?:\s+и\s+(?:понятия|термины))?|'
    r'основные\s+(?:понятия|термины)\s+(?:параграфа|главы|темы|урока))\s*[.:!]?\s*$'
)
BLOCK_RUBRIC_KIND = "key_terms"


def block_rubric_kind(title: str) -> Optional[str]:
    """«Ключевые понятия», «Ключевые слова» → key_terms, иначе None.

    Рубрика, которую CHUNKER_TEXTBOOK_KEEP_HEADINGS оставляет, — не рубрика
    (обычный заголовок); явно перечисленная в CHUNKER_TEXTBOOK_DROP_HEADINGS —
    секционная: выбрасывает всё под собой, как остальной front_matter.
    """
    if heading_kind(title) != "front_matter":
        return None
    s = normalize_heading(title)
    if s in _heading_set(os.getenv("CHUNKER_TEXTBOOK_DROP_HEADINGS", "")):
        return None
    return BLOCK_RUBRIC_KIND if _BLOCK_RUBRIC_RE.match(s) else None


# ---------------------------------------------------------------------------
# Оглавление, выходные данные, список сокращений — структурно
# ---------------------------------------------------------------------------

_TOC_LINE_RE = re.compile(r'(?:(?:\.\s*){3,}|\s)\d{1,3}\s*$')
_IMPRINT_RE = re.compile(
    r'(?:\bУДК\b|\bББК\b|\bISBN\b|©|Рецензент|Издательство|Издательский\s+(?:дом|центр)|'
    r'Учебник\s+для|Учебное\s+пособие\s+для|ФГОС|Министерств[аом]+\s+(?:просвещения|образования)|'
    r'Все\s+права\s+защищены|Подписано\s+в\s+печать|Тираж\b|Формат\s+\d|Печ\.\s*л\.|Уч\.-изд\.|'
    r'Оригинал-макет|Художественный\s+редактор|Технический\s+редактор|Корректор|Компьютерная\s+вёрстка|'
    r'Компьютерная\s+верстка|Авторский\s+коллектив|Ответственный\s+редактор|Научный\s+редактор|'
    r'Рекомендовано\s+Министерством|Допущено\s+Министерством|экспертн\w+\s+заключени|Знак\s+информационной\s+продукции)',
    re.IGNORECASE,
)
_ABBREV_LINE_RE = re.compile(r'^[А-ЯЁA-Z][А-ЯЁA-Z.\-]{1,11}\s*[—–-]\s+\S')
IMPRINT_MAX_CHARS = 1500


def is_toc_block(paragraph: str) -> bool:
    lines = [l.strip() for l in (paragraph or "").splitlines() if l.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for l in lines if _TOC_LINE_RE.search(l))
    return hits / len(lines) >= 0.6


def is_imprint_block(paragraph: str) -> bool:
    p = (paragraph or "").strip()
    if not p or len(p) > IMPRINT_MAX_CHARS:
        return False
    return bool(_IMPRINT_RE.search(p))


def is_abbreviation_list(paragraph: str) -> bool:
    lines = [l.strip() for l in (paragraph or "").splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    hits = sum(1 for l in lines if _ABBREV_LINE_RE.match(l))
    return hits / len(lines) >= 0.7


_LABELS = (
    r'(?:ЭТО ИНТЕРЕСНО|ИНТЕРЕСНО|ВАЖНО|СПРАВКА|СПРАВОЧНИК|ДОКУМЕНТ|'
    r'МНЕНИЕ|ЛИЧНОСТЬ|ИЗ ИСТОРИИ|ФАКТ|ПОДУМАЙТЕ|ВНИМАНИЕ)'
)
# «Сайт ИНТЕРЕСНО\n…» — ярлык в конце строки.
_INLINE_LABEL_RE = re.compile(r'(?<=\S)[ \t]+' + _LABELS + r'(?=[ \t]*\n)')
# «ИНТЕРЕСНО Мышление — это…» — ярлык в начале строки перед текстом врезки.
_LEADING_LABEL_RE = re.compile(r'(?m)^[ \t]*' + _LABELS + r'[ \t]+(?=[А-ЯЁA-Z«"„(\d])')
# «…усилиями. ИНТЕРЕСНО Мышление» — ярлык между предложениями внутри строки.
_MIDLINE_LABEL_RE = re.compile(r'(?<=[.!?…»)])[ \t]+' + _LABELS + r'[ \t]+(?=[А-ЯЁA-Z«"„(\d])')


def strip_inline_labels(text: str) -> str:
    """Убрать ярлык врезки, вклеенный в строку при извлечении PDF.

    Ярлык рубрики стоит рядом с плашкой и попадает внутрь строки: в конец
    («Сайт ИНТЕРЕСНО\nносил…» → «Сайт\nносил…»; перенос остаётся, строки
    склеит логика продолжения абзаца), в начало («ИНТЕРЕСНО Мышление — …»)
    или между предложениями. Ярлык, стоящий отдельной строкой, — настоящий
    заголовок, его не трогаем.
    """
    t = _INLINE_LABEL_RE.sub("", text or "")
    t = _LEADING_LABEL_RE.sub("", t)
    return _MIDLINE_LABEL_RE.sub(" ", t)


def _is_task_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if _TASK_BULLET_RE.match(s) or EXERCISE_START_RE.match(s):
        return True
    return len(s) <= 60 and heading_kind(s) == "exercise"


def strip_exercise_lines(paragraph: str) -> tuple:
    """Вырезать из абзаца строки-задания, оставив изложение.

    В тексте из PDF задание часто стоит внутри абзаца отдельной строкой:
    «…120 млн человек.\nВыясните, какие услуги…\nгосударственными услугами вы
    уже пользовались?». Строка, начинающаяся с маркера «⬤», императива или
    заголовка «Вопросы и задания», вырезается вместе с продолжением до конца
    предложения; заголовок-рубрика забирает весь остаток абзаца.

    Возвращает (очищенный абзац, число вырезанных символов).
    """
    # Маркер задания «⬤» посреди строки (после подписи к фото) — начало
    # отдельной строки-задания.
    normalized = re.sub(r'(?<=\S)[ \t]+(?=⬤)', "\n", paragraph or "")
    lines = normalized.split("\n")
    if len(lines) < 2:
        return paragraph, 0
    out: List[str] = []
    dropped = 0
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if _is_task_line(line):
            if not (_TASK_BULLET_RE.match(line) or EXERCISE_START_RE.match(line)):
                # Рубрика «Вопросы и задания» посреди абзаца: дальше только задания.
                dropped += sum(len(l) for l in lines[i:])
                break
            buf = line
            j = i
            while not ends_like_sentence(buf) and j + 1 < n:
                j += 1
                buf = f"{buf} {lines[j].strip()}"
            dropped += len(buf)
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    cleaned = "\n".join(out).strip()
    return cleaned, dropped


# Строка таблицы: начинается с «|» либо содержит две и более вертикальные
# черты (markitdown вклеивает текст перед первой ячейкой).
_TABLE_ROW_RE = re.compile(r'^\s*\||^.*\|.*\|')
_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{3,}|^.*-{3,}\s*\|')
# Остатки markdown-таблицы в заголовке: «§ 18. | | Хозяйственная | | система | --- |».
_TABLE_MARKS_RE = re.compile(r'\s*\|\s*|\s*-{3,}\s*')


def strip_table_marks(text: str) -> str:
    """Убрать вертикальные черты и отточия markdown-таблицы из строки заголовка."""
    return re.sub(r'\s+', ' ', _TABLE_MARKS_RE.sub(' ', text or '')).strip()
_LONE_MARKER_RE = re.compile(r'^\s*[⬤●•▪■]\s*$')


def strip_table_lines(paragraph: str) -> tuple:
    """Убрать строки markdown-таблиц и одиночные маркеры из абзаца.

    markitdown превращает схемы, таблицы и колонки в строки «| … | … |» —
    по требованию графику выбрасываем. Одиночная строка-маркер («⬤») —
    остаток разметки задания, текста в ней нет.

    Возвращает (очищенный абзац, число вырезанных символов).
    """
    lines = (paragraph or "").split("\n")
    out: List[str] = []
    dropped = 0
    for line in lines:
        if _TABLE_ROW_RE.match(line) or _TABLE_SEP_RE.match(line) or _LONE_MARKER_RE.match(line):
            dropped += len(line)
            continue
        out.append(line)
    if not dropped:
        return paragraph, 0
    return "\n".join(out).strip(), dropped


# Номер задания в начале строки или после конца предложения: «4. О чём…»,
# «2) Какие…», «6*. В исторической науке…» (звёздочка — задание повышенной
# сложности).
_NUMBERED_TASK_RE = re.compile(r'(?:^|(?<=\n)|(?<=[.!?…»)]\s))(\d{1,2})(\*?)[.)]\s+(?=[А-ЯЁ«])')
# Вопросительный знак должен стоять в первых символах пункта — иначе это
# нумерованный список изложения («1. «БАЛЛАДА О СОЛДАТЕ». Худ. фильм…»).
NUMBERED_TASK_QUESTION_WINDOW = 300


def find_numbered_task(text: str) -> Optional[int]:
    """Позиция первого номерного задания в тексте или None.

    Пункт считается заданием, если до следующего номера (или в первых
    NUMBERED_TASK_QUESTION_WINDOW символах) есть «?», либо номер со звёздочкой.
    Маркированные списки дат («• 1946 г. — …») не затрагиваются.
    """
    t = text or ""
    starts = list(_NUMBERED_TASK_RE.finditer(t))
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(t)
        item = t[m.start():min(end, m.start() + NUMBERED_TASK_QUESTION_WINDOW)]
        if m.group(2) or "?" in item:
            return m.start()
    return None


def strip_numbered_tasks(text: str) -> tuple:
    """Отрезать хвост с первого номерного задания («4. Какие последствия…?»).

    В вёрстке задания к параграфу затекают в хвост врезки на той же странице.
    Возвращает (текст без хвоста, вырезано символов).
    """
    pos = find_numbered_task(text)
    if pos is None:
        return text, 0
    head = (text or "")[:pos].rstrip()
    return head, len(text) - len(head)


# Строка-рубрика аппарата короче этого — заголовок, а не фраза из текста.
APPARATUS_LINE_MAX_CHARS = 60
# Подпись к QR-коду «Тема параграфа. ⏎ Дополнительные материалы» короче этого
# выбрасывается целиком; длиннее — режется только сама рубрика и хвост.
APPARATUS_CAPTION_MAX_CHARS = 300


def is_apparatus_line(line: str) -> bool:
    """Строка — рубрика «Дополнительные материалы» (с учётом KEEP/DROP env).

    С заглавной буквы: перенос строки «…можно найти ⏎ дополнительные
    материалы по теме» — фраза текста, а не рубрика.
    """
    s = (line or "").strip()
    return (
        bool(s) and s[:1].isupper() and len(s) <= APPARATUS_LINE_MAX_CHARS
        and heading_kind(s) == "apparatus"
    )


def is_apparatus_paragraph(paragraph: str) -> bool:
    """Абзац начинается рубрикой «Дополнительные материалы» или это подпись к QR.

    В истории рубрика стоит над списком фильмов («1. «БАЛЛАДА О СОЛДАТЕ». Худ.
    фильм…»), в обществознании — последней строкой подписи к QR-коду.
    """
    lines = [l for l in (paragraph or "").split("\n") if l.strip()]
    if not lines:
        return False
    if is_apparatus_line(lines[0]):
        return True
    return is_apparatus_line(lines[-1]) and len((paragraph or "").strip()) <= APPARATUS_CAPTION_MAX_CHARS


# Пункт списка «Дополнительных материалов»: «3. «ЛЕОНИД БРЕЖНЕВ…». Статья на
# сайте История.РФ.», «1. «ВЕСНА». Худ. фильм 1947 г.».
_MATERIAL_MARK_RE = re.compile(
    r'(?:Худ\.\s*фильм|Док\.\s*фильм|Мультфильм|Телесериал|Статья\s+на\s+сайте|на\s+сайте\s+История\.РФ)'
)
_MATERIAL_ITEM_RE = re.compile(r'(?:^|\n)\s*\d{1,2}\.\s+«')


def is_materials_list(text: str) -> bool:
    """Текст — список фильмов/статей из «Дополнительных материалов» без рубрики.

    Бывает, когда рубрика и первые пункты остались на одной колонке, а
    продолжение списка layout отдал отдельной врезкой.
    """
    t = text or ""
    marks = len(_MATERIAL_MARK_RE.findall(t))
    items = len(_MATERIAL_ITEM_RE.findall(t))
    return marks >= 1 and items >= 1 and marks * 2 >= items and len(t) <= 1500


def strip_apparatus_lines(paragraph: str) -> tuple:
    """Вырезать рубрику «Дополнительные материалы» посреди абзаца вместе с остатком.

    Как рубрика «Вопросы и задания» в strip_exercise_lines: после неё до конца
    абзаца идёт только список материалов. Возвращает (абзац, вырезано символов).
    """
    lines = (paragraph or "").split("\n")
    for i, line in enumerate(lines):
        if i and is_apparatus_line(line) and ends_like_sentence(lines[i - 1]):
            dropped = sum(len(l) for l in lines[i:])
            return "\n".join(lines[:i]).strip(), dropped
    return paragraph, 0


def classify_dropped_paragraph(paragraph: str) -> Optional[str]:
    """Тип выбрасываемого абзаца (exercise | apparatus | toc | imprint | abbreviations) или None."""
    if is_apparatus_paragraph(paragraph):
        return "apparatus"
    if is_exercise_paragraph(paragraph):
        return "exercise"
    if is_toc_block(paragraph):
        return "toc"
    if is_abbreviation_list(paragraph):
        return "abbreviations"
    if is_imprint_block(paragraph):
        return "imprint"
    return None
