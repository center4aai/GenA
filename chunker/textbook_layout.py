"""Layout-извлечение PDF учебника (метод chunking_method=layout).

Плоский текст markitdown не знает шрифтов и заливок, поэтому врезки, подписи
и задания склеиваются с изложением. Здесь документ читается через pdfplumber:
каждая строка получает шрифт, кегль, координаты, заливку под ней и близость к
картинке. По профилю документа (доминирующий шрифт = основной текст) строки
классифицируются, группируются в блоки и собираются в то же дерево
``{level, title, content, children}``, что и `docx2json_outline`, только
врезки и подписи становятся дочерними узлами с ``block_type``, а задания,
колонтитулы, текст схем и оглавление выбрасываются (счётчики в stats).

Все пороги относительные (к кеглю и полям основного текста), чтобы работать
с любым издательством. Если текстового слоя нет или профиль неуверенный —
``LayoutUnavailable``: вызывающий откатывается на правила.
"""

from __future__ import annotations

import logging
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from docx2json_outline import (
    _CAPS_HYPHEN_JOIN_RE,
    _TEXTBOOK_CHAPTER_RE,
    _TEXTBOOK_CID_BULLET_RE,
    _TEXTBOOK_HYPHEN_JOIN_RE,
    _TEXTBOOK_SECTION_BARE_RE,
    _TEXTBOOK_SECTION_FULL_RE,
    _strip_stress_digits,
    _textbook_heading_text,
    _textbook_is_junk_line,
)
from text_utils import (
    BLOCK_RUBRIC_KIND,
    DROP_HEADING_KINDS,
    EXERCISE_START_RE,
    block_rubric_kind,
    caption_sentence_count,
    count_sentences,
    ends_like_sentence,
    heading_kind,
    is_exercise_paragraph,
    normalize_heading,
    strip_exercise_lines,
)

logger = logging.getLogger(__name__)


class LayoutUnavailable(Exception):
    """PDF не годится для layout-пути (нет текстового слоя, неясный профиль)."""


# ---------------------------------------------------------------------------
# Константы (относительные пороги)
# ---------------------------------------------------------------------------

MIN_CHARS_PER_PAGE = 200          # меньше — скан без текстового слоя
MIN_BODY_SHARE = 0.35             # доля символов основного шрифта
BODY_CANDIDATE_SHARE = 0.15       # шрифт с такой долей символов может быть основным
BODY_CANDIDATE_MAX_RATIO = 1.35   # …если не крупнее самого частого более чем в 1.35 раза
SMALL_RATIO = 0.92                # кегль ≤ body*0.92 → мелкий (врезки, подписи)
HEADING_RATIO = 1.15              # кегль ≥ body*1.15 → заголовок
CHAPTER_RATIO = 1.8               # очень крупный заголовок без регэкспа → глава
SECTION_RATIO = 1.3
INDENT_MIN_PT = 6.0               # отступ первой строки абзаца относительно соседней
INDENT_MAX_PT = 40.0
PARAGRAPH_GAP_RATIO = 1.6         # вертикальный зазор > 1.6 высоты строки — новый абзац
BLOCK_GAP_RATIO = 2.5             # разрыв внутри врезки/подписи
TOP_BAND_RATIO = 0.07             # верхняя полоса страницы — колонтитул
IMAGE_NEAR_PT = 14.0              # подпись стоит не дальше этого от картинки
FIGURE_SHORT_CHARS = 40           # короткий текст внутри картинки/схемы
INSET_MIN_CHARS = 160             # мелкий блок без картинки короче — огрызок
INSET_SHORT_MIN_CHARS = 80        # врезка короче — подпись/обрывок, в чанки не идёт
FRONT_MATTER_MAX_SHARE = 0.05     # текст до первой главы короче 5 % документа — титул/оглавление
FRONT_MATTER_MAX_PAGE_SHARE = 0.12  # первая «Глава»/«§» не дальше 12 % страниц → всё до неё титул/оглавление
FRONT_MATTER_MIN_PAGES = 20       # в выдержке короче титула нет — правило не применяем
FRONT_MATTER_MAX_TEXT_SHARE = 0.10  # и текст до первой главы не длиннее 10 % документа
THIN_IMAGE_PT = 40.0              # картинка тоньше — декоративная полоса, не рамка для текста
LABEL_MAX_CHARS = 24

_CAPTION_RE = re.compile(
    r'^(?:Рис\.|Рисунок|Илл\.|Схема|Табл\.|Таблица|Фото|Худ\.|Портрет|Карта|Диаграмма|Марка|Плакат|Гравюра|Открытка)\b',
    re.IGNORECASE,
)
_CAPTION_TAIL_RE = re.compile(r'\b(?:Фотография|Худ\.|Художник|Гравюра|Плакат|Марка)\b')
_RUNNING_HEADER_RE = re.compile(r'(?:§|Глава|ГЛАВА)')
_PAGE_NUMBER_TAIL_RE = re.compile(r'\s\d{1,3}$|^\d{1,3}\s')
_PUA_RE = re.compile('[\ue000-\uf8ff]')
_ALLCAPS_RE = re.compile(r'^[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9\s\-–—«»"“”.,:()]{2,80}$')
# Колонтитул с номером страницы: «8 ГЛАВА I», «ГЛАВА I 9», «§ 1. Название 7».
# Шрифт тут не важен: у некоторых издательств колонтитул набран основным.
_RUNNING_HEADER_DECORATED_RE = re.compile(
    r'^(?:\d{1,3}\s+(?:ГЛАВА|Глава)\s+(?:[IVXLCDM]+|\d+)\.?'
    r'|(?:ГЛАВА|Глава)\s+(?:[IVXLCDM]+|\d+)\.?\s+\d{1,3}'
    r'|\d{1,3}\s+§\s*\d+(?:-\d+)?\.\s+\S.{2,120}'
    r'|§\s*\d+(?:-\d+)?\.\s+\S.{2,120}?\s+\d{1,3})$'
)
# Нумерованный подзаголовок в начале абзаца: «1 Влияние Победы.» — набран
# выделенным (жирным / цветным / другим шрифтом), дальше идёт обычный текст.
# Внутри названия бывают точка, двоеточие и вопрос («2 Потери. Демография.»,
# «3 Начало холодной войны: её причины…», «10 Минские соглашения. Что это
# было?»), номер — с точкой или без («1. Название»), точки в конце может не быть.
_RUNIN_HEADING_RE = re.compile(r'^\d{1,2}\.?\s+[А-ЯЁA-Z«"(]\S*(?:\s+\S+)*$')
RUNIN_TITLE_MAX_CHARS = 160       # подзаголовок длиннее — это выделенный абзац, а не название
RUNIN_TITLE_MIN_LETTERS = 4       # «2 млн» и подобное — не подзаголовок
RUNIN_MAX_SHARE = 0.9             # выделенная голова длиннее 90 % строки — это не подзаголовок, а цитата
# Подзаголовок, перенесённый на вторую строку: следующая строка абзаца не дальше
# этого (в высотах строки) и начинается с продолжения выделения.
RUNIN_WRAP_GAP_RATIO = 1.6
COLUMN_GUTTER_MIN_PT = 12.0       # пустой жёлоб между колонками внутри врезки, не меньше этого
COLUMN_MIN_LINES = 1              # колонка — хотя бы одна строка (подпись рядом с текстом), если спаны не пересекаются
COLUMN_X_TOL_PT = 25.0            # строки с x0 ближе этого — одна колонка
COLUMN_GUTTER_TOL_PT = 8.0        # середины разрывов ближе этого — один жёлоб
COLUMN_GUTTER_SUPPORT = 2         # жёлоб считается настоящим, если его дают хотя бы 2 строки заливки
# Заголовок врезки (имя в биографии, «МНЕНИЕ УЧЁНОГО») крупнее текста своей
# заливки хотя бы на столько пунктов; такие строки не участвуют в разборе колонок.
FILL_HEAD_MIN_PT = 0.4
# Смешанная врезка: строки двух x0-кластеров стоят рядом по высоте и в порядке
# чтения чередуются — текст колонок перемешан построчно, такой блок выбрасываем.
MIXED_MIN_ROW_PAIRS = 2           # пар «строка слева ↔ строка справа на той же высоте» не меньше
MIXED_MIN_SWITCHES = 3            # переходов между кластерами в порядке строк не меньше
# Номер главы отдельной строкой («I», «12») на странице со строкой «ГЛАВА <название>».
_CHAPTER_WORD_RE = re.compile(r'^(?:ГЛАВА|Глава)\b\s*(.*)$')
_CHAPTER_NUMBER_RE = re.compile(r'^(?:[IVXLCDM]+|\d{1,2})$')
# «Глава I.» без названия — название набрано отдельной строкой рядом.
_CHAPTER_BARE_RE = re.compile(r'^(?:ГЛАВА|Глава)\s+(?:[IVXLCDM]+|\d{1,2})\.?$')
CHAPTER_TITLE_MAX_LINES = 3
# «§ 1 Восстановление и развитие…» — параграф без точки после номера
# (docx2json_outline ждёт «§ 1. …»).
_SECTION_LOOSE_RE = re.compile(r'^§\s*\d+(?:\s*[–—\-]\s*\d+)?\.?\s+[А-ЯЁA-Z«"(]')


def _flag(raw: Optional[str], default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _columns_enabled() -> bool:
    return _flag(os.getenv("CHUNKER_LAYOUT_COLUMNS"), True)


def _runin_enabled() -> bool:
    return _flag(os.getenv("CHUNKER_LAYOUT_RUNIN_HEADINGS"), True)


# ---------------------------------------------------------------------------
# Структуры
# ---------------------------------------------------------------------------

@dataclass
class Line:
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    text: str
    family: str
    size: float
    bold: bool
    italic: bool
    runs: List[Tuple[str, float, str]]
    fill_id: Optional[int] = None
    in_band: bool = False
    near_image: bool = False
    in_image: bool = False
    cls: str = ""
    label: str = ""
    # Доминирующий цвет символов и раскладка по стилю (семейство, кегль,
    # жирность, цвет, текст без пробелов) — для подзаголовков в начале абзаца.
    color: str = ""
    styled_runs: List[Tuple[str, float, bool, str, str]] = field(default_factory=list)
    runin: str = ""
    # Крупная строка внутри заливки — заголовок врезки, а не раздела.
    inset_title: bool = False

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass
class Profile:
    body_family: str
    body_size: float
    body_share: float
    body_x0: float
    indent_x0: Optional[float]
    heading_families: set
    body_color: str = ""

    @property
    def small_max(self) -> float:
        return self.body_size * SMALL_RATIO

    @property
    def heading_min(self) -> float:
        return self.body_size * HEADING_RATIO


@dataclass
class Block:
    kind: str                      # heading|body|inset|caption|exercise|junk|figure_text|debris|caption_short|inset_short|inset_mixed
    lines: List[Line] = field(default_factory=list)
    title: str = ""
    level: int = 0
    text: str = ""
    # Подзаголовок внутри абзаца: заголовок без собственных строк, текст
    # абзаца остаётся в следующем body-блоке целиком.
    inline: bool = False

    @property
    def page_start(self) -> int:
        return self.lines[0].page if self.lines else 0

    @property
    def page_end(self) -> int:
        return self.lines[-1].page if self.lines else 0


# ---------------------------------------------------------------------------
# Шрифты и цвета
# ---------------------------------------------------------------------------

def font_family(fontname: str) -> str:
    name = (fontname or "").split("+")[-1]
    for sep in ("-", ",", "_"):
        name = name.split(sep)[0]
    for suffix in ("Bold", "Italic", "Demi", "Regular", "Light", "Medium", "Black", "Heavy", "Oblique", "Book"):
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            name = name[: -len(suffix)]
    return name or "unknown"


def _is_bold(fontname: str) -> bool:
    n = (fontname or "").lower()
    return any(k in n for k in ("bold", "demi", "black", "heavy", "semibold"))


def _is_italic(fontname: str) -> bool:
    n = (fontname or "").lower()
    return "italic" in n or "oblique" in n


def is_non_white(color: Any) -> bool:
    """Заливка «видимая»: не белая ни в gray, ни в RGB, ни в CMYK."""
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return float(color) < 0.98
    try:
        vals = [float(c) for c in color]
    except (TypeError, ValueError):
        return False
    if len(vals) == 1:
        return vals[0] < 0.98
    if len(vals) == 3:
        return any(v < 0.98 for v in vals)
    if len(vals) == 4:
        return any(v > 0.02 for v in vals)
    return False


# ---------------------------------------------------------------------------
# Чтение строк
# ---------------------------------------------------------------------------

def _read_lines(path: str) -> Tuple[List[Line], int, Dict[int, Tuple[float, float]]]:
    import pdfplumber

    columns_enabled = _columns_enabled()
    lines: List[Line] = []
    page_sizes: Dict[int, Tuple[float, float]] = {}
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        if n_pages == 0:
            raise LayoutUnavailable("PDF has no pages")
        for pno, page in enumerate(pdf.pages, start=1):
            w, h = float(page.width), float(page.height)
            page_sizes[pno] = (w, h)
            fills = []
            band = None
            for r in page.rects:
                if not r.get("fill"):
                    continue
                rw, rh = r["x1"] - r["x0"], r["bottom"] - r["top"]
                if rw < 40 or rh < 12 or not is_non_white(r.get("non_stroking_color")):
                    continue
                if rw > 0.95 * w and rh > 0.9 * h:
                    continue  # фон всей страницы
                if r["top"] < TOP_BAND_RATIO * h and rw > 0.8 * w and rh < 0.12 * h:
                    band = r  # полоса колонтитула
                    continue
                fills.append(r)
            images = []
            for im in page.images:
                iw, ih = im["x1"] - im["x0"], im["bottom"] - im["top"]
                if iw > 0.9 * w and ih > 0.9 * h:
                    continue  # фон
                if im["top"] < TOP_BAND_RATIO * h and iw > 0.8 * w and ih < 0.12 * h:
                    continue  # декоративная полоса сверху
                images.append((im["x0"], im["top"], im["x1"], im["bottom"], iw * ih))
            try:
                text_lines = page.extract_text_lines(layout=False, return_chars=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pdfplumber failed on page %s: %s", pno, exc)
                text_lines = []
            page_lines: List[Line] = []
            gutters = _page_gutters(text_lines, fills) if columns_enabled else {}
            for tl in text_lines:
                parts = _split_line_columns(tl, fills, gutters) if columns_enabled else [tl]
                for part in parts:
                    page_lines.extend(_build_line(part, pno, fills, images, band))
            if columns_enabled:
                page_lines = _order_columns(page_lines)
            lines.extend(page_lines)
            page.flush_cache()
    total_chars = sum(len(l.text) for l in lines)
    if total_chars / max(n_pages, 1) < MIN_CHARS_PER_PAGE:
        raise LayoutUnavailable(
            f"no usable text layer ({total_chars} chars on {n_pages} pages)"
        )
    return lines, n_pages, page_sizes


def _fill_index(fills: List[Dict[str, Any]], x: float, y: float) -> Optional[int]:
    """Самая маленькая (вложенная) заливка под точкой: плашка внутри серого поля
    страницы важнее самого поля — именно она отделяет колонку от колонки."""
    best: Optional[int] = None
    best_area = float("inf")
    for i, r in enumerate(fills):
        if r["x0"] - 2 <= x <= r["x1"] + 2 and r["top"] - 2 <= y <= r["bottom"] + 2:
            area = (r["x1"] - r["x0"]) * (r["bottom"] - r["top"])
            if area < best_area:
                best, best_area = i, area
    return best


def _chars_to_text(chars: List[Dict[str, Any]], x_tolerance: float = 3.0) -> str:
    """Символы → строка; пробел там, где зазор между символами больше допуска."""
    out: List[str] = []
    prev_x1: Optional[float] = None
    for c in chars:
        x0 = float(c.get("x0", 0.0))
        if prev_x1 is not None and x0 - prev_x1 > x_tolerance:
            out.append(" ")
        out.append(c.get("text", ""))
        prev_x1 = float(c.get("x1", x0))
    return "".join(out)


def _raw_column_groups(tl: Dict[str, Any], fills: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Символы строки → группы по разрывам (смена заливки или жёлоб внутри заливки)."""
    chars = sorted(tl.get("chars") or [], key=lambda c: float(c.get("x0", 0.0)))
    if len(chars) < 6:
        return []
    cy = (float(tl["top"]) + float(tl["bottom"])) / 2
    groups: List[List[Dict[str, Any]]] = [[chars[0]]]
    prev = chars[0]
    prev_fill = _fill_index(fills, (float(prev["x0"]) + float(prev["x1"])) / 2, cy)
    for c in chars[1:]:
        cur_fill = _fill_index(fills, (float(c["x0"]) + float(c["x1"])) / 2, cy)
        gap = float(c.get("x0", 0.0)) - float(prev.get("x1", 0.0))
        size = float(c.get("size") or 10.0)
        split = (
            (cur_fill != prev_fill and (cur_fill is not None or prev_fill is not None) and gap > 0)
            or (cur_fill is not None and cur_fill == prev_fill and gap >= max(COLUMN_GUTTER_MIN_PT, 1.5 * size))
        )
        if split:
            groups.append([c])
        else:
            groups[-1].append(c)
        prev, prev_fill = c, cur_fill
    return [g for g in groups if any(ch.get("text", "").strip() for ch in g)]


def _gap_fill(a: List[Dict[str, Any]], b: List[Dict[str, Any]], fills: List[Dict[str, Any]], cy: float) -> Tuple[float, float, Optional[int]]:
    lo, hi = float(a[-1]["x1"]), float(b[0]["x0"])
    return lo, hi, _fill_index(fills, (lo + hi) / 2, cy)


def _page_gutters(text_lines: List[Dict[str, Any]], fills: List[Dict[str, Any]]) -> Dict[int, List[float]]:
    """Жёлобы колонок в заливках страницы: середины разрывов, которые дают
    чистое деление (по ≥ 2 слова с обеих сторон) хотя бы у COLUMN_GUTTER_SUPPORT строк."""
    mids: Dict[int, List[float]] = {}
    for tl in text_lines:
        groups = _raw_column_groups(tl, fills)
        if len(groups) < 2 or any(len(_chars_to_text(g).split()) < 2 for g in groups):
            continue
        cy = (float(tl["top"]) + float(tl["bottom"])) / 2
        for a, b in zip(groups, groups[1:]):
            lo, hi, fid = _gap_fill(a, b, fills, cy)
            if fid is not None:
                mids.setdefault(fid, []).append((lo + hi) / 2)
    out: Dict[int, List[float]] = {}
    for fid, ms in mids.items():
        for m in ms:
            if sum(1 for x in ms if abs(x - m) <= COLUMN_GUTTER_TOL_PT) >= COLUMN_GUTTER_SUPPORT:
                out.setdefault(fid, []).append(m)
    return out


def _split_line_columns(
    tl: Dict[str, Any], fills: List[Dict[str, Any]], gutters: Optional[Dict[int, List[float]]] = None,
) -> List[Dict[str, Any]]:
    """Строка pdfplumber, собранная по высоте через всю страницу, → части по колонкам.

    Разрыв ставится там, где соседние символы попадают в разные заливки (две
    плашки «РОССИЯ | МИР» рядом) или где внутри одной заливки между символами
    пустой жёлоб. Текст вне заливок не трогаем: выключка основного текста даёт
    широкие пробелы, но это одна строка.
    """
    groups = _raw_column_groups(tl, fills)
    if not groups:
        return [tl]
    cy = (float(tl["top"]) + float(tl["bottom"])) / 2
    # Ярлык рубрики («ИНТЕРЕСНО») рядом с текстом — не колонка: его вырезает
    # _cut_label_runs. Одиночное слово делит строку, только если это не ярлык
    # и видно, что оно из соседней колонки: другой кегль/шрифт («Вознесенский»
    # 15 pt | «ной войны…» 9.5 pt) или слева перенос («Отечествен- | тирован.»).
    # Или разрыв приходится на жёлоб, который на этой же заливке видно по
    # другим строкам (стихи в две колонки: «Я люблю тебя, Жизнь, | причалы.»,
    # «покоя! | В трубных звуках…»). Иначе это растянутый пробел выключки
    # в узкой колонке («хологическое      манипулирование»).
    if len(groups) < 2 or any(_label_like_group(g) for g in groups):
        return [tl]

    def _on_gutter(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
        lo, hi, fid = _gap_fill(a, b, fills, cy)
        return any(lo - 2 <= m <= hi + 2 for m in (gutters or {}).get(fid, [])) if fid is not None else False

    for k, g in enumerate(groups):
        if len(_chars_to_text(g).split()) >= 2:
            continue
        neighbours = [groups[j] for j in (k - 1, k + 1) if 0 <= j < len(groups)]
        styled_apart = any(_group_style(g) != _group_style(nb) for nb in neighbours)
        after_hyphen = k > 0 and _chars_to_text(groups[k - 1]).rstrip().endswith("-")
        before_hyphen_word = _chars_to_text(g).rstrip().endswith("-") and k + 1 < len(groups)
        on_gutter = (k > 0 and _on_gutter(groups[k - 1], g)) or (k + 1 < len(groups) and _on_gutter(g, groups[k + 1]))
        if not (styled_apart or after_hyphen or before_hyphen_word or on_gutter):
            return [tl]
    parts: List[Dict[str, Any]] = []
    for g in groups:
        parts.append({
            "x0": float(g[0]["x0"]), "x1": float(g[-1]["x1"]),
            "top": min(float(c["top"]) for c in g), "bottom": max(float(c["bottom"]) for c in g),
            "text": _chars_to_text(g), "chars": g,
        })
    return parts


def _group_style(chars: List[Dict[str, Any]]) -> Tuple[str, float]:
    """Доминирующие семейство шрифта и кегль (с точностью до 1 pt) части строки."""
    cnt: Counter = Counter()
    for c in chars:
        if c.get("text", "").strip():
            cnt[(font_family(c.get("fontname", "")), round(float(c.get("size") or 0.0)))] += 1
    return cnt.most_common(1)[0][0] if cnt else ("", 0.0)


def _label_like_group(chars: List[Dict[str, Any]]) -> bool:
    """Часть строки из одного слова, похожая на ярлык рубрики: капс или 1–2 знака."""
    words = _chars_to_text(chars).split()
    if len(words) >= 2:
        return False
    word = words[0] if words else ""
    letters = [c for c in word if c.isalpha()]
    return len(word) <= 2 or (len(letters) >= 2 and all(c.isupper() for c in letters))


def _x0_clusters(lines: List[Line]) -> List[List[Line]]:
    clusters: List[List[Line]] = []
    for line in sorted(lines, key=lambda l: l.x0):
        if clusters and line.x0 - clusters[-1][0].x0 <= COLUMN_X_TOL_PT:
            clusters[-1].append(line)
        else:
            clusters.append([line])
    return clusters


def _fill_heads(members: List[Line]) -> List[Line]:
    """Строки заметно крупнее основного текста заливки — заголовок врезки."""
    weights: Counter = Counter()
    for l in members:
        weights[l.size] += len(l.text)
    if not weights:
        return []
    main = weights.most_common(1)[0][0]
    return [l for l in members if l.size >= main + FILL_HEAD_MIN_PT]


def _columns_in_fill(members: List[Line]) -> List[List[Line]]:
    """Строки одной заливки → колонки (по левому краю), если они не накладываются.

    Крупные строки (имя в биографической врезке, рубрика) в разбор колонок не
    входят: стоят они выше середины врезки — идут первыми, ниже — последними.
    Иначе одна строка имени между колонками ломает весь разбор.
    """
    key = lambda l: (l.top, l.x0)  # noqa: E731
    by_top = sorted(members, key=key)
    heads = _fill_heads(members)
    head_ids = {id(h) for h in heads}
    rest = [l for l in members if id(l) not in head_ids]
    if not rest:
        return [by_top]
    # Шапка — крупные строки с верхней половины врезки и те, что идут за ними
    # подряд (имя, отчество и годы жизни столбиком).
    mid = statistics.median(l.top for l in rest)
    lead: List[Line] = []
    trail: List[Line] = []
    for h in sorted(heads, key=key):
        if h.top <= mid or (lead and h.top - lead[-1].bottom <= 1.5 * lead[-1].height):
            lead.append(h)
        else:
            trail.append(h)
    lead_ids = {id(h) for h in lead}
    # Колонок нет — строки по высоте, но шапка (имя) всё равно идёт первой:
    # иначе строки имени перемежаются с текстом и рвут врезку на куски.
    fallback = [lead + [l for l in by_top if id(l) not in lead_ids]]
    clusters = _x0_clusters(rest)
    if len(clusters) < 2:
        return fallback
    spans = [(min(l.x0 for l in c), max(l.x1 for l in c)) for c in clusters]
    if not any(spans[k][1] > spans[k + 1][0] + 2 for k in range(len(spans) - 1)):
        cols = [sorted(c, key=key) for c in clusters]
        return [c for c in [lead] + cols + [trail] if c]
    # Спаны пересекаются из-за строк во всю ширину (заголовок стихотворения,
    # общий текст под двумя подписями). Такие строки делят врезку на полосы,
    # внутри полосы — колонка за колонкой. Колонки — кластеры из ≥ 2 строк;
    # одиночная строка либо пересекает жёлоб (полоса), либо относится к
    # колонке, в которой начинается («покоя!» со сдвигом вправо).
    majors = [c for c in clusters if len(c) >= 2]
    if len(majors) < 2:
        return fallback
    starts = [min(l.x0 for l in c) for c in majors]
    crossing: set = set()
    narrow: List[List[Line]] = [[] for _ in majors]
    for l in rest:
        k = max(i for i in range(len(starts)) if starts[i] <= l.x0 + COLUMN_X_TOL_PT) if l.x0 + COLUMN_X_TOL_PT >= starts[0] else 0
        if k + 1 < len(starts) and l.x1 > starts[k + 1] + 2:
            crossing.add(id(l))
        else:
            narrow[k].append(l)
    narrow = [c for c in narrow if c]
    if len(narrow) < 2 or not crossing:
        return fallback
    nspans = [(min(l.x0 for l in c), max(l.x1 for l in c)) for c in narrow]
    if any(nspans[k][1] > nspans[k + 1][0] + 2 for k in range(len(nspans) - 1)):
        return fallback
    which = {id(l): k for k, c in enumerate(narrow) for l in c}
    out: List[Line] = list(lead)
    segment: List[Line] = []

    def _flush() -> None:
        segment.sort(key=lambda l: (which[id(l)], l.top, l.x0))
        out.extend(segment)
        segment.clear()

    for l in sorted(rest, key=key):
        if id(l) in crossing:
            _flush()
            out.append(l)
        else:
            segment.append(l)
    _flush()
    out.extend(trail)
    return [out]


def _order_columns(page_lines: List[Line]) -> List[Line]:
    """Строки врезок — по порядку чтения: колонка за колонкой, плашка за плашкой.

    Два случая вёрстки: одна заливка с двумя колонками (строки кластеризуются по
    левому краю) и две плашки рядом («РОССИЯ | МИР»: заливки с общей высотой,
    не накладывающиеся по горизонтали). Во втором случае все строки левой
    плашки идут раньше строк правой. Строки вне заливок не трогаем.
    """
    by_fill: Dict[int, List[Line]] = {}
    for line in page_lines:
        if line.fill_id is not None:
            by_fill.setdefault(line.fill_id, []).append(line)
    if not by_fill:
        return page_lines
    ordered_by_fill: Dict[int, List[Line]] = {
        fid: [l for col in _columns_in_fill(members) for l in col] for fid, members in by_fill.items()
    }
    extents = {
        fid: (min(l.x0 for l in ms), min(l.top for l in ms), max(l.x1 for l in ms), max(l.bottom for l in ms))
        for fid, ms in by_fill.items()
    }
    # Плашки рядом → одна группа (union-find по парам).
    parent = {fid: fid for fid in by_fill}

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    fids = list(by_fill)
    for i, a in enumerate(fids):
        ax0, atop, ax1, abot = extents[a]
        for b in fids[i + 1:]:
            bx0, btop, bx1, bbot = extents[b]
            overlap = min(abot, bbot) - max(atop, btop)
            if overlap <= 0.5 * min(abot - atop, bbot - btop, 1e9) or overlap <= 0:
                continue
            if ax1 <= bx0 + 2 or bx1 <= ax0 + 2:
                parent[_find(a)] = _find(b)
    groups: Dict[int, List[int]] = {}
    for fid in fids:
        groups.setdefault(_find(fid), []).append(fid)
    group_lines: Dict[int, List[Line]] = {}
    for root, members in groups.items():
        seq: List[Line] = []
        for fid in sorted(members, key=lambda f: extents[f][0]):
            seq.extend(ordered_by_fill[fid])
        group_lines[root] = seq
    out: List[Line] = []
    done: set = set()
    for line in page_lines:
        if line.fill_id is None:
            out.append(line)
            continue
        root = _find(line.fill_id)
        if root in done:
            continue
        done.add(root)
        out.extend(group_lines[root])
    return out


def _build_line(
    tl: Dict[str, Any], pno: int, fills: List[Dict[str, Any]],
    images: List[Tuple[float, float, float, float, float]], band: Optional[Dict[str, Any]],
) -> List[Line]:
    chars = tl.get("chars") or []
    text = (tl.get("text") or "").strip()
    if not chars or not text:
        return []
    runs: List[Tuple[str, float, str]] = []
    styled: List[Tuple[str, float, bool, str, str]] = []
    colors: Counter = Counter()
    for c in chars:
        fam = font_family(c.get("fontname", ""))
        size = round(float(c.get("size") or 0.0), 1)
        ch = c.get("text", "")
        bold = _is_bold(c.get("fontname", ""))
        color = _color_key(c.get("non_stroking_color"))
        if ch.strip():
            colors[color] += 1
        if runs and runs[-1][0] == fam and runs[-1][1] == size:
            runs[-1] = (fam, size, runs[-1][2] + ch)
        else:
            runs.append((fam, size, ch))
        if styled and styled[-1][:4] == (fam, size, bold, color):
            styled[-1] = (fam, size, bold, color, styled[-1][4] + ch)
        else:
            styled.append((fam, size, bold, color, ch))
    weights = Counter()
    for fam, size, t in runs:
        weights[(fam, size)] += len(t.strip())
    (family, size), _n = weights.most_common(1)[0]
    names = [c.get("fontname", "") for c in chars]
    dominant_name = Counter(names).most_common(1)[0][0]
    x0, x1 = float(tl["x0"]), float(tl["x1"])
    top, bottom = float(tl["top"]), float(tl["bottom"])
    cx, cy = (x0 + x1) / 2, (top + bottom) / 2
    fill_id = _fill_index(fills, cx, cy)
    in_band = bool(band) and band["top"] - 2 <= cy <= band["bottom"] + 2
    near_image = in_image = False
    for ix0, itop, ix1, ibot, area in images:
        h_overlap = min(x1, ix1) - max(x0, ix0)
        if h_overlap <= 0:
            continue
        # «Внутри картинки» — центр строки в её рамке; тонкие декоративные
        # полосы и рамки за текст не считаются.
        thin = min(ix1 - ix0, ibot - itop) < THIN_IMAGE_PT
        if not thin and ix0 <= cx <= ix1 and itop - 2 <= cy <= ibot + 2:
            in_image = True
            near_image = True
            break
        if h_overlap >= 0.3 * (x1 - x0) and (
            0 < top - ibot <= IMAGE_NEAR_PT or 0 < itop - bottom <= IMAGE_NEAR_PT
        ):
            near_image = True
    return [Line(
        page=pno, x0=x0, x1=x1, top=top, bottom=bottom, text=text,
        family=family, size=size, bold=_is_bold(dominant_name),
        italic=_is_italic(dominant_name), runs=runs, fill_id=fill_id,
        in_band=in_band, near_image=near_image, in_image=in_image,
        color=colors.most_common(1)[0][0] if colors else "", styled_runs=styled,
    )]


def _color_key(color: Any) -> str:
    if color is None:
        return ""
    if isinstance(color, (int, float)):
        return f"{float(color):.2f}"
    try:
        return ",".join(f"{float(c):.2f}" for c in color)
    except (TypeError, ValueError):
        return str(color)


# ---------------------------------------------------------------------------
# Профиль документа
# ---------------------------------------------------------------------------

def profile_document(lines: List[Line], n_pages: int) -> Profile:
    skip = max(1, int(n_pages * 0.03)) if n_pages >= 20 else 0
    core = [l for l in lines if skip < l.page <= n_pages - skip] or lines
    weights: Counter = Counter()
    for l in core:
        weights[(l.family, l.size)] += len(l.text)
    if not weights:
        raise LayoutUnavailable("no text lines")
    total = sum(weights.values())
    (top_family, top_size), top_chars = weights.most_common(1)[0]
    # Основной текст — самый КРУПНЫЙ из частых шрифтов, а не просто самый
    # частый: в выдержке с заданиями и врезками мелкий шрифт может набрать
    # больше символов, чем изложение.
    candidates = [
        (fam, size, n) for (fam, size), n in weights.items()
        if n / total >= BODY_CANDIDATE_SHARE and size <= top_size * BODY_CANDIDATE_MAX_RATIO
    ]
    body_family, body_size, body_chars = max(candidates, key=lambda c: (c[1], c[2]))
    share = body_chars / max(total, 1)
    combined_share = sum(n for (_f, _s), n in weights.items()) and (body_chars + (top_chars if (top_family, top_size) != (body_family, body_size) else 0)) / total
    if share < MIN_BODY_SHARE and combined_share < MIN_BODY_SHARE:
        raise LayoutUnavailable(
            f"ambiguous body font: {body_family} {body_size} covers only {share:.0%} of text"
        )
    body_lines = [l for l in core if l.family == body_family and abs(l.size - body_size) <= 0.6]
    x0s = Counter(round(l.x0) for l in body_lines)
    body_x0 = float(x0s.most_common(1)[0][0]) if x0s else 0.0
    indent_candidates = Counter(
        x for x, n in x0s.items() if INDENT_MIN_PT <= x - body_x0 <= INDENT_MAX_PT
    )
    indent_x0 = float(indent_candidates.most_common(1)[0][0]) if indent_candidates else None
    heading_families = {
        l.family for l in core
        if l.size >= body_size * HEADING_RATIO and l.family != body_family
    }
    color_weights: Counter = Counter()
    for l in body_lines:
        if l.color:
            color_weights[l.color] += len(l.text)
    body_color = color_weights.most_common(1)[0][0] if color_weights else ""
    return Profile(body_family, body_size, share, body_x0, indent_x0, heading_families, body_color)


# ---------------------------------------------------------------------------
# Классификация строк
# ---------------------------------------------------------------------------

def _looks_allcaps_heading(text: str) -> bool:
    s = text.strip()
    if not (4 <= len(s) <= 80) or not _ALLCAPS_RE.match(s):
        return False
    if s.endswith(('.', '!', '?', ',', ';')):
        return False
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _looks_caps_question(text: str) -> bool:
    """«ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?» — капс-заголовок раздела в форме вопроса.

    _looks_allcaps_heading вопросительный знак не пускает (так кончаются
    вопросы заданий); здесь — хотя бы два слова, весь текст до «?» капсом.
    """
    s = text.strip()
    if not s.endswith("?") or len(s) > 80 or len(s.split()) < 2:
        return False
    head = s.rstrip("?").strip()
    return bool(head) and not head.endswith(('.', '!', ',', ';')) and _looks_allcaps_heading(head)


def _cut_label_runs(line: Line, prof: Profile) -> None:
    """Ярлык рубрики («ИНТЕРЕСНО») другим шрифтом внутри строки — в line.label."""
    if len(line.runs) < 2:
        return
    kept: List[str] = []
    for fam, size, text in line.runs:
        t = text.strip()
        is_label_font = fam in prof.heading_families or (fam != prof.body_family and size > line.size + 0.4)
        if (
            is_label_font
            and t
            and len(t) <= LABEL_MAX_CHARS
            and _looks_allcaps_heading(t)
            and fam != line.family
        ):
            line.label = t
            continue
        kept.append(text)
    if line.label:
        # Пробелы в runs не сохраняются (pdfplumber отдаёт их только в text),
        # поэтому ярлык вырезаем из готовой строки.
        line.text = re.sub(r'\s+', ' ', line.text.replace(line.label, " ", 1)).strip()


def _styled_lead(line: Line, prof: Profile) -> Tuple[int, bool, bool]:
    """Выделенная голова строки: (символов без пробелов, есть ли обычный хвост,
    выделена ли голова шрифтом/цветом, а не только жирностью).

    Хвост — первый run основным шрифтом без жирности и цветом основного текста.
    """
    lead_chars = 0
    distinct = False
    for fam, size, bold, color, text in line.styled_runs:
        plain = fam == prof.body_family and not bold and (not prof.body_color or color == prof.body_color)
        if plain and text.strip():
            return lead_chars, abs(size - prof.body_size) <= 0.6, distinct
        lead_chars += len(text)
        if text.strip() and (fam != prof.body_family or (prof.body_color and color != prof.body_color)):
            distinct = True
    return lead_chars, False, distinct


def _cut_by_chars(text: str, n_chars: int) -> str:
    """Первые n_chars непробельных символов строки — вместе с пробелами между ними."""
    seen = 0
    for idx, ch in enumerate(text):
        if not ch.isspace():
            seen += 1
        if seen >= n_chars:
            return text[:idx + 1].strip()
    return text.strip()


def _runin_title(lead: str) -> str:
    """Голова строки → название подзаголовка или «», если не похоже на «N Название»."""
    lead = re.sub(r'\s+', ' ', lead).strip()
    if len(lead) > RUNIN_TITLE_MAX_CHARS or not _RUNIN_HEADING_RE.match(lead):
        return ""
    if lead.endswith((',', ';', '-', '–', '—')):
        return ""
    body = re.sub(r'^\d{1,2}\.?\s+', '', lead)
    if sum(1 for c in body if c.isalpha()) < RUNIN_TITLE_MIN_LETTERS:
        return ""
    return lead.rstrip('.:').strip()


def _runin_heading(line: Line, prof: Profile) -> str:
    """«1 Влияние Победы.» в начале строки основного текста → текст подзаголовка.

    Голова строки набрана выделенно (жирным, цветом или другим шрифтом), затем
    без разрыва идёт обычный текст абзаца. Границу находим по стилевым run'ам
    (в них нет пробелов), а текст берём из строки по числу символов.
    """
    if not line.styled_runs:
        return ""
    lead_chars, has_tail, _distinct = _styled_lead(line, prof)
    # Хвост — обычный текст абзаца основным шрифтом и кеглем; без него это
    # заголовок целой строкой или перенесённый подзаголовок (см. mark_runin_headings).
    if not has_tail:
        return ""
    if lead_chars == 0 or lead_chars >= len(line.text.replace(" ", "")) * RUNIN_MAX_SHARE:
        return ""
    return _runin_title(_cut_by_chars(line.text, lead_chars))


def _runin_wrapped(line: Line, nxt: Optional[Line], prof: Profile) -> str:
    """Подзаголовок на всю строку с продолжением на следующей.

    «2 Союзный Центр и национальные регионы: проблемы взаимо-» +
    «отношений. В национальной политике…» — первая строка выделена целиком,
    вторая начинается с выделенного продолжения (или сразу с обычного текста,
    если название уместилось в первую строку и кончается точкой). Голова должна
    отличаться шрифтом или цветом, одной жирности мало: так набирают цитаты.
    """
    if nxt is None or not line.styled_runs or not nxt.styled_runs:
        return ""
    lead_chars, has_tail, distinct = _styled_lead(line, prof)
    if has_tail or not distinct or lead_chars < len(line.text.replace(" ", "")):
        return ""
    same_page = nxt.page == line.page and 0 <= nxt.top - line.bottom <= RUNIN_WRAP_GAP_RATIO * line.height
    if not (same_page or nxt.page == line.page + 1):
        return ""
    lead2, tail2, _d2 = _styled_lead(nxt, prof)
    if not tail2:
        return ""
    first = line.text.strip()
    if lead2 == 0:
        if not first.endswith(('.', '?', ':')):
            return ""
        return _runin_title(first)
    cont = _cut_by_chars(nxt.text, lead2)
    if re.search(r'[а-яёa-z]-$', first):
        joined = first[:-1] + cont
    else:
        joined = f"{first} {cont}"
    if not joined.endswith(('.', '?', ':', '!')):
        return ""
    return _runin_title(joined)


def mark_runin_headings(lines: List[Line], prof: Profile) -> int:
    """Проставить ``line.runin`` строкам с нумерованным подзаголовком в начале."""
    if not _runin_enabled():
        return 0
    found = 0
    for i, line in enumerate(lines):
        if line.in_band or line.fill_id is not None or line.in_image:
            continue
        runin = _runin_heading(line, prof)
        if not runin:
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt is not None and (nxt.in_band or nxt.fill_id is not None or nxt.in_image):
                nxt = None
            runin = _runin_wrapped(line, nxt, prof)
        if runin:
            line.runin = runin
            found += 1
    return found


def classify_line(line: Line, prof: Profile, page_size: Tuple[float, float]) -> str:
    w, h = page_size
    t = line.text.strip()
    if not t:
        return "junk"
    if line.in_band:
        # Заголовок главы крупным кеглем на плашке у верхнего края страницы —
        # не колонтитул (Обществознание 10, «Глава III. ЭКОНОМИЧЕСКАЯ ЖИЗНЬ»).
        if not (_TEXTBOOK_CHAPTER_RE.match(t) and line.size >= prof.body_size * CHAPTER_RATIO):
            return "junk"
    if _textbook_is_junk_line(t):
        return "junk"
    if (
        (line.top < TOP_BAND_RATIO * h or line.bottom > (1 - TOP_BAND_RATIO) * h)
        and _RUNNING_HEADER_DECORATED_RE.match(t)
    ):
        return "junk"
    if (
        line.top < TOP_BAND_RATIO * h
        and line.family != prof.body_family
        and (_RUNNING_HEADER_RE.search(t) or _PAGE_NUMBER_TAIL_RE.search(t))
        and line.size < prof.body_size * CHAPTER_RATIO
    ):
        return "junk"
    known = {prof.body_family} | prof.heading_families
    if line.family not in known and len(t) <= 8 and not t[0].isalpha():
        return "junk"  # типографские метки чужим шрифтом
    if line.in_image and (line.size < prof.body_size - 0.6 or len(t) < FIGURE_SHORT_CHARS):
        return "figure_text"
    if line.size >= prof.heading_min:
        return "heading"
    _cut_label_runs(line, prof)
    if not line.text.strip():
        return "junk"
    if _looks_allcaps_heading(line.text) and (line.bold or line.family != prof.body_family) and line.fill_id is None:
        return "heading"
    # Дефис/тире глифом из частной области Unicode («ГОСУДАРСТВО\uf6baЦИВИЛИЗАЦИЯ»,
    # «ВЗАИМОПОМОЩЬ \uf6ba ОСНОВА ОБЩЕСТВА») ломает проверку капса — для
    # заголовка подменяем его: между пробелами тире, внутри слова дефис.
    caps_text = _PUA_RE.sub("-", re.sub(r'(?<=\s)[\ue000-\uf8ff](?=\s)', "—", line.text))
    styled = line.bold or line.family != prof.body_family or bool(prof.body_color and line.color != prof.body_color)
    if caps_text != line.text and line.fill_id is None and styled and _looks_allcaps_heading(caps_text):
        line.text = caps_text
        return "heading"
    if line.fill_id is None and line.size > prof.small_max and styled and _looks_caps_question(caps_text):
        # Заголовок раздела вопросом отдельной строкой, выделенный жирностью/
        # цветом/шрифтом (Обществознание: «ЧТО ТАКОЕ ДЕЯТЕЛЬНОСТЬ?»), — как
        # «ВИДЫ ДЕЯТЕЛЬНОСТИ» без вопроса. Рубрики заданий узнаёт heading_kind.
        line.text = caps_text
        return "heading"
    if line.fill_id is not None:
        return "bg"
    if line.size <= prof.small_max:
        return "small"
    return "body"


def _is_structural_text(text: str) -> bool:
    t = (text or "").strip()
    return bool(
        _TEXTBOOK_CHAPTER_RE.match(t) or _TEXTBOOK_SECTION_BARE_RE.match(t)
        or _TEXTBOOK_SECTION_FULL_RE.match(t) or _SECTION_LOOSE_RE.match(t)
    )


def _continues_heading(prev: Optional[Line], line: Line) -> bool:
    """Строка — продолжение многострочного заголовка над ней (тот же кегль, малый зазор)."""
    return (
        prev is not None and prev.page == line.page and abs(prev.size - line.size) <= 0.6
        and -0.5 * line.height <= line.top - prev.bottom <= 1.2 * line.height
    )


def _synthesize_chapters(lines: List[Line], prof: Profile) -> int:
    """Страница главы: «ГЛАВА СССР В 1945—1991 гг.» + отдельная крупная строка «I».

    В двухколоночной шапке слово «Глава» и номер стоят друг под другом слева,
    название — справа, и всё это лежит на картинке. Из пары собираем заголовок
    «Глава I СССР В 1945—1991 гг.» (дальше его узнаёт _TEXTBOOK_CHAPTER_RE),
    строки номера и продолжения названия того же кегля гасим как мусор.
    """
    by_page: Dict[int, List[int]] = {}
    for i, l in enumerate(lines):
        by_page.setdefault(l.page, []).append(i)
    made = 0
    for _page, idxs in by_page.items():
        word_idx = next(
            (i for i in idxs
             if lines[i].size >= prof.heading_min and lines[i].cls != "junk"
             and _CHAPTER_WORD_RE.match(lines[i].text.strip())
             and not _TEXTBOOK_CHAPTER_RE.match(lines[i].text.strip())),
            None,
        )
        if word_idx is None:
            continue
        word = lines[word_idx]
        num_idx = next(
            (i for i in idxs
             if i != word_idx and _CHAPTER_NUMBER_RE.match(lines[i].text.strip())
             and lines[i].size >= max(prof.heading_min, word.size - 0.6)),
            None,
        )
        if num_idx is None:
            continue
        parts = [_CHAPTER_WORD_RE.match(word.text.strip()).group(1).strip()]
        prev = word
        for i in idxs:
            l = lines[i]
            if i in (word_idx, num_idx) or l.top <= word.top:
                continue
            if not _continues_heading(prev, l):
                continue
            parts.append(l.text.strip())
            l.cls = "junk"
            prev = l
        number = lines[num_idx].text.strip()
        lines[num_idx].cls = "junk"
        title = " ".join(p for p in parts if p)
        word.text = f"Глава {number} {title}".strip()
        word.cls = "heading"
        word.inset_title = False
        made += 1
    return made


def _merge_chapter_titles(lines: List[Line], prof: Profile) -> int:
    """Страница главы: «Глава I.» и рядом отдельной строкой «ЧЕЛОВЕК И ОБЩЕСТВО».

    Обществознание 9: номер (33pt) и название (28pt) стоят в одной плашке
    на одной высоте, но кегль разный — многострочным заголовком их не
    склеить, а название в заливке уходило бы в шапку врезки. В порядке
    чтения название может оказаться и до, и после номера. Собираем
    «Глава I. ЧЕЛОВЕК И ОБЩЕСТВО» (до трёх строк названия того же кегля),
    строки названия гасим как мусор.
    """

    def title_like(c: Line) -> bool:
        t = _PUA_RE.sub("-", c.text.strip())
        return (
            c.cls != "junk" and c.size >= prof.body_size * SECTION_RATIO
            and not _is_structural_text(t) and heading_kind(t) is None
            and any(ch.isalpha() for ch in t)
        )

    merged = 0
    for i, l in enumerate(lines):
        if l.cls != "heading" or not _CHAPTER_BARE_RE.match(l.text.strip()):
            continue
        near = [
            j for j in range(max(0, i - 2 * CHAPTER_TITLE_MAX_LINES), min(len(lines), i + 1 + 2 * CHAPTER_TITLE_MAX_LINES))
            if j != i and lines[j].page == l.page
        ]
        # Первая строка названия — на высоте номера (рядом с ним или сразу под ним).
        start = next(
            (j for j in near if title_like(lines[j])
             and lines[j].top <= l.bottom + 0.5 * lines[j].height and lines[j].bottom >= l.top),
            None,
        )
        if start is None:
            continue
        taken = [start]
        for j in near:
            if j <= taken[-1] or len(taken) >= CHAPTER_TITLE_MAX_LINES:
                continue
            c = lines[j]
            if c.cls == "junk":
                continue
            if not (title_like(c) and _continues_heading(lines[taken[-1]], c)):
                break
            taken.append(j)
        parts = [_PUA_RE.sub("-", lines[j].text.strip()) for j in taken]
        for j in taken:
            lines[j].cls = "junk"
        l.text = f"{l.text.strip()} {' '.join(parts)}"
        l.inset_title = False
        merged += 1
    return merged


def _demote_fill_headings(lines: List[Line], prof: Profile) -> int:
    """Крупная строка в заливке — заголовок врезки, а не раздела.

    «РОССИЯ МИР» над хронологией, имя в «ПОРТРЕТЕ НА ФОНЕ ЭПОХИ», «МНЕНИЕ
    УЧЁНОГО» не должны вставать в стек заголовков: иначе путь всех следующих
    абзацев параграфа получает «> РОССИЯ МИР». Заголовком остаются:
    глава/§ на цветной плашке и продолжение такого заголовка; плашка, в которой
    кроме заголовка ничего нет («ВОПРОСЫ И ЗАДАНИЯ К ГЛАВЕ»); рубрика-аппарат
    (задания, словарь — от неё зависит, что выбрасывается следом); капс над
    текстом основного кегля на подложке («ПОДВЕДЁМ ИТОГИ» — раздел, не врезка).
    """
    fill_lines: Dict[Tuple[int, int], List[int]] = {}
    for i, l in enumerate(lines):
        if l.fill_id is not None and l.cls not in ("junk",):
            fill_lines.setdefault((l.page, l.fill_id), []).append(i)
    demoted = 0
    prev_heading: Optional[Line] = None
    for i, l in enumerate(lines):
        if l.cls != "heading":
            if l.cls != "junk":
                prev_heading = None
            continue
        if l.fill_id is None:
            prev_heading = l
            continue
        t = l.text.strip()
        siblings = [j for j in fill_lines.get((l.page, l.fill_id), []) if j != i]
        nxt = next((lines[j] for j in siblings if j > i), None)
        letters = sum(1 for c in t if c.isalpha())
        keep = (
            _is_structural_text(t)
            or _continues_heading(prev_heading, l)
            or (not siblings and letters >= 3)
            or heading_kind(t) is not None
            or (
                _looks_allcaps_heading(t) and nxt is not None
                and nxt.family == prof.body_family and abs(nxt.size - prof.body_size) <= 0.6
            )
        )
        if keep:
            prev_heading = l
            continue
        l.cls = "bg"
        l.inset_title = True
        demoted += 1
        prev_heading = None
    return demoted


def refine_line_classes(lines: List[Line], prof: Profile) -> Dict[str, int]:
    """Поправки классов с учётом соседних строк (после classify_line)."""
    chapters = _synthesize_chapters(lines, prof)
    chapters += _merge_chapter_titles(lines, prof)
    demoted = _demote_fill_headings(lines, prof)
    return {"chapters_synthesized": chapters, "inset_titles": demoted}


# ---------------------------------------------------------------------------
# Блоки
# ---------------------------------------------------------------------------

def _join_lines(lines: List[Line]) -> str:
    text = "\n".join(l.text.strip() for l in lines if l.text.strip())
    text = _CAPS_HYPHEN_JOIN_RE.sub(r'\1\2', text)
    text = _TEXTBOOK_HYPHEN_JOIN_RE.sub(r'\1\2', text)
    text = _strip_stress_digits(text)
    text = _TEXTBOOK_CID_BULLET_RE.sub('•', text)
    return text.strip()


def _starts_new_paragraph(prev: Optional[Line], cur: Line, prof: Profile, after_break: bool) -> bool:
    t = cur.text.strip()
    if prev is None:
        return True
    if after_break:
        # После заголовка, врезки или страницы: строчная буква — продолжение.
        if t[:1].islower():
            return False
        if cur.x0 - prof.body_x0 >= INDENT_MIN_PT:
            return True
        return ends_like_sentence(prev.text)
    if cur.page != prev.page:
        return not t[:1].islower() and (cur.x0 - prof.body_x0 >= INDENT_MIN_PT or ends_like_sentence(prev.text))
    if cur.x0 - prev.x0 >= INDENT_MIN_PT and cur.x0 - prof.body_x0 >= INDENT_MIN_PT:
        return True
    gap = cur.top - prev.bottom
    if gap > PARAGRAPH_GAP_RATIO * prev.height and ends_like_sentence(prev.text) and not t[:1].islower():
        return True
    return False


def group_blocks(lines: List[Line], prof: Profile) -> List[Block]:
    blocks: List[Block] = []
    body: Optional[Block] = None
    prev_body: Optional[Line] = None
    after_break = True
    aux: Optional[Block] = None   # текущий small/bg блок
    aux_prev: Optional[Line] = None

    def close_aux():
        nonlocal aux, aux_prev
        if aux and aux.lines:
            blocks.append(aux)
        aux, aux_prev = None, None

    for line in lines:
        cls = line.cls
        if cls == "body":
            close_aux()
            if line.runin:
                # Подзаголовок внутри абзаца: заголовок 3 уровня без своих строк,
                # сама строка целиком остаётся в новом абзаце.
                blocks.append(Block("heading", [line], title=line.runin, level=3, text=line.runin, inline=True))
                body = Block("body", [line])
                blocks.append(body)
            elif body is None or _starts_new_paragraph(prev_body, line, prof, after_break):
                body = Block("body", [line])
                blocks.append(body)
            else:
                body.lines.append(line)
            prev_body = line
            after_break = False
            continue
        if cls == "heading":
            close_aux()
            # Многострочный заголовок: те же кегль/страница, малый зазор.
            last = blocks[-1] if blocks else None
            if (
                last is not None and last.kind == "heading" and last.lines[-1].page == line.page
                and abs(last.lines[-1].size - line.size) <= 0.6
                and line.top - last.lines[-1].bottom <= 1.2 * line.height
                and not _is_structural_text(line.text)
            ):
                last.lines.append(line)
            else:
                blocks.append(Block("heading", [line]))
            body, prev_body, after_break = None, prev_body, True
            continue
        if cls in ("small", "bg"):
            near = (
                aux is not None and aux_prev is not None
                and aux_prev.page == line.page
                and line.top - aux_prev.bottom <= BLOCK_GAP_RATIO * line.height
            )
            same = near and aux_prev.fill_id == line.fill_id
            # Заголовок врезки открывает новый блок, даже в той же заливке
            # («РОССИЯ МИР» под списком понятий — шапка следующей хронологии).
            if same and line.inset_title and not all(_is_title_line(l) for l in aux.lines):
                same = False
            # Блок из одних заголовков («ПОРТРЕТ НА ФОНЕ ЭПОХИ» на своей плашке,
            # «РОССИЯ МИР», имя и годы жизни в биографии) забирает следующую
            # врезку: из соседней заливки — если она рядом, из своей — при любом
            # зазоре (колонка текста может начинаться ниже имени).
            if (
                not same and aux is not None and aux_prev is not None and aux_prev.page == line.page
                and cls == "bg" and line.fill_id is not None
                and all(_is_title_line(l) or l.size >= line.size + FILL_HEAD_MIN_PT for l in aux.lines)
                and (near or aux_prev.fill_id == line.fill_id)
            ):
                same = True
            if not same:
                close_aux()
                aux = Block("aux", [line])
            else:
                aux.lines.append(line)
            aux_prev = line
            after_break = True
            continue
        # junk / figure_text
        close_aux()
        blocks.append(Block(cls, [line]))
        if cls == "figure_text":
            after_break = True
    close_aux()
    return blocks


def _is_title_line(line: Line) -> bool:
    """Строка-шапка врезки: крупный заголовок в заливке или жирный ярлык капсом."""
    if line.inset_title:
        return True
    t = line.text.strip()
    return line.fill_id is not None and line.bold and len(t) <= 60 and _looks_allcaps_heading(t)


def _heading_level(text: str, size: float, prof: Profile) -> int:
    if _TEXTBOOK_CHAPTER_RE.match(text):
        return 1
    if _TEXTBOOK_SECTION_BARE_RE.match(text) or _TEXTBOOK_SECTION_FULL_RE.match(text) or _SECTION_LOOSE_RE.match(text):
        return 2
    if size >= prof.body_size * CHAPTER_RATIO:
        return 1
    if size >= prof.body_size * SECTION_RATIO:
        return 2
    return 3


def _is_mixed_columns(lines: List[Line]) -> bool:
    """Врезка, где две колонки прочитаны построчно вперемешку.

    Признак: есть два x0-кластера по ≥ 2 строки, строки разных кластеров стоят
    на одной высоте (≥ MIXED_MIN_ROW_PAIRS пар), а в порядке чтения блок
    прыгает между кластерами (≥ MIXED_MIN_SWITCHES раз). Если _order_columns
    разобрал колонки, переход между ними один и блок не смешанный.
    """
    body = [l for l in lines if not _is_title_line(l)]
    clusters = [c for c in _x0_clusters(body) if len(c) >= 2]
    if len(clusters) < 2:
        return False
    which = {id(l): k for k, c in enumerate(clusters) for l in c}
    seq = [which[id(l)] for l in body if id(l) in which]
    switches = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    if switches < MIXED_MIN_SWITCHES:
        return False
    pairs = 0
    for a, b in zip(body, body[1:]):
        ka, kb = which.get(id(a)), which.get(id(b))
        if ka is None or kb is None or ka == kb or a.page != b.page:
            continue
        if min(a.bottom, b.bottom) - max(a.top, b.top) > 0.5 * min(a.height, b.height):
            pairs += 1
    return pairs >= MIXED_MIN_ROW_PAIRS


def _inset_title(block: Block, label: str, first: str) -> str:
    """Название врезки: крупная шапка в заливке > ярлык рубрики > жирная первая строка."""
    heads = [l.text.strip() for l in block.lines if l.inset_title and l.text.strip()]
    if heads:
        return re.sub(r'\s+', ' ', " ".join(heads)).strip()
    if not label:
        # Ярлык отдельной строкой на своей плашке («МУДРЫЕ МЫСЛИ»).
        label = next((l.text.strip() for l in block.lines if _is_title_line(l)), "")
    if label:
        return label.capitalize() if label.isupper() else label
    line0 = block.lines[0]
    # Жирный термин в начале первой строки: «Социальная инженерия — пси-…».
    bold_chars = 0
    for _fam, _size, bold, _color, text in line0.styled_runs:
        if not bold and text.strip():
            break
        bold_chars += len(text)
    total_chars = len(first.replace(" ", ""))
    if 0 < bold_chars < total_chars:
        term = _cut_by_chars(first, bold_chars).rstrip(" —–-:,").strip()
        if sum(1 for c in term if c.isalpha()) >= 3 and len(term) <= 80:
            return term
    # Жирная первая строка — название, если это не пункт списка и не обрыв слова.
    if line0.bold and len(first) <= 80 and len(block.lines) > 1 and not first.startswith(("•", "-", "–", "—")) and not first.endswith("-"):
        return first
    return "Врезка"


def classify_block(block: Block, prof: Profile) -> Block:
    if block.kind == "heading" and block.inline:
        return block
    if block.kind == "heading":
        block.text = _textbook_heading_text(" ".join(l.text for l in block.lines))
        block.level = _heading_level(block.lines[0].text, block.lines[0].size, prof)
        block.title = block.text
        return block
    if block.kind == "body":
        block.text = _join_lines(block.lines)
        return block
    if block.kind != "aux":
        return block
    text = _join_lines(block.lines)
    block.text = text
    label = next((l.label for l in block.lines if l.label), "")
    first = block.lines[0].text.strip()
    # Врезка под рубрикой-аппаратом (задания, «Дополнительные материалы») —
    # выбрасывается целиком со счётчиком своего вида.
    drop_kind = next((k for k in (heading_kind(label), heading_kind(first)) if k in DROP_HEADING_KINDS), None)
    if drop_kind:
        # «КЛЮЧЕВЫЕ ПОНЯТИЯ» на плашке со списком — блочная рубрика.
        rubric = block_rubric_kind(label) or block_rubric_kind(first)
        block.kind = rubric or drop_kind
        return block
    cleaned, _cut = strip_exercise_lines(text)
    if not cleaned or is_exercise_paragraph(text):
        block.kind = "exercise"
        return block
    block.text = cleaned
    if _is_mixed_columns(block.lines):
        # Колонки врезки перемешаны построчно — читать нельзя, выбрасываем.
        block.kind = "inset_mixed"
        return block
    # Шапка-ярлык у значка/картинки не делает врезку подписью.
    near_image = any(l.near_image for l in block.lines if not _is_title_line(l))
    sentences = count_sentences(cleaned)
    is_caption = near_image or bool(_CAPTION_RE.match(first)) or bool(_CAPTION_TAIL_RE.search(first))
    # Врезка с крупной шапкой (имя в биографии у портрета) — не подпись,
    # даже если короткая и стоит у картинки.
    has_head = any(_is_title_line(l) for l in block.lines)
    if is_caption and not has_head and len(cleaned) < INSET_MIN_CHARS * 2:
        block.kind = "caption" if caption_sentence_count(cleaned) >= 2 else "caption_short"
        block.title = label or "Подпись к иллюстрации"
        return block
    if len(cleaned) < INSET_SHORT_MIN_CHARS:
        # «На пепелище родного дома. БССР.» — подпись без факта, не врезка.
        block.kind = "inset_short"
        return block
    if sentences >= 2 or len(cleaned) >= INSET_MIN_CHARS:
        block.kind = "inset"
        block.title = _inset_title(block, label, first)
        return block
    block.kind = "debris"
    return block


# ---------------------------------------------------------------------------
# Дерево
# ---------------------------------------------------------------------------

def _stats_add(stats: Optional[Dict[str, Any]], bucket: str, key: str, chars: int) -> None:
    if stats is None:
        return
    entry = stats.setdefault(bucket, {}).setdefault(key, {"count": 0, "chars": 0})
    entry["count"] += 1
    entry["chars"] += chars


def _is_structural_heading(block: Block) -> bool:
    t = block.title or block.text or " ".join(l.text for l in block.lines)
    return _is_structural_text(t)


SECTION_TITLE_MAX_LINES = 3       # строк названия после голого «§ N», не больше


def _section_title_part(block: Block, page: int, prof: Profile) -> bool:
    """Строка названия параграфа рядом с голым «§ N»: крупный нестр. заголовок той же страницы."""
    return (
        block.kind == "heading" and not block.inline and bool(block.lines)
        and block.page_start == page and block.page_end == page
        and block.lines[0].size >= prof.body_size * SECTION_RATIO
        and not _is_structural_heading(block)
        # Рубрики («ВСПОМНИТЕ», «КЛЮЧЕВЫЕ ПОНЯТИЯ», задания) — не название параграфа.
        and heading_kind(block.title) is None
        and block_rubric_kind(block.title) is None
        and not EXERCISE_START_RE.match(block.title)
    )


def _join_title_parts(parts: List[str]) -> str:
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.search(r'[а-яёa-z]-$', out) and p[:1].islower():
            out = out[:-1] + p
        else:
            out = f"{out} {p}" if out else p
    return out


def _merge_section_titles(blocks: List[Block], prof: Profile) -> Tuple[List[Block], int]:
    """Голый «§ N» и строки названия вокруг него → один заголовок уровня 2.

    Обществознание: крупный номер «§ 4» стоит слева, название справа в одну-две
    строки — «Деятельность человека» на уровне номера и «как способ
    существования личности» под ним, или «§ 7» + «Как музеи сохраняют
    историю». По отдельности каждая строка становилась заголовком своего
    уровня, и в пути оставалась только последняя. Сшиваем: строку названия
    прямо перед «§ N» (та же страница, по высоте рядом с номером) и
    следующие за номером строки того же кегля. «§ 1 Название» одной строкой
    (История) сюда не попадает.
    """
    out: List[Block] = []
    merged = 0
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        bare = (
            b.kind == "heading" and not b.inline and len(b.lines) == 1
            and _TEXTBOOK_SECTION_BARE_RE.match(b.lines[0].text.strip())
        )
        if not bare:
            out.append(b)
            i += 1
            continue
        num = b.lines[0]
        page = num.page
        before: Optional[Block] = None
        k = len(out) - 1
        while k >= 0 and out[k].kind == "junk":
            k -= 1
        if k >= 0 and _section_title_part(out[k], page, prof):
            last = out[k].lines[-1]
            # Рядом с номером по высоте: перекрывается с ним или примыкает.
            if last.bottom >= num.top - 0.5 * num.height and last.top <= num.bottom:
                before = out[k]
        size = before.lines[0].size if before else None
        after: List[Block] = []
        skipped: List[Block] = []   # колонтитулы после последней взятой строки названия
        tail: List[Block] = []      # колонтитулы между строками названия — после заголовка
        prev_line = before.lines[-1] if before else num
        j = i + 1
        while j < n and len(after) < SECTION_TITLE_MAX_LINES:
            c = blocks[j]
            if c.kind == "junk":
                skipped.append(c)
                j += 1
                continue
            if not _section_title_part(c, page, prof):
                break
            first = c.lines[0]
            if size is not None and abs(first.size - size) > 0.6:
                break
            # Первая строка после номера — не ниже его низа (с запасом в строку),
            # следующие — с обычным междустрочным зазором.
            ref = num if not after and before is None else prev_line
            limit = num.bottom + first.height if ref is num else ref.bottom + 1.2 * first.height
            if first.top > limit:
                break
            after.append(c)
            size = first.size
            prev_line = c.lines[-1]
            j += 1
            tail.extend(skipped)
            skipped = []
        if before is None and not after:
            out.append(b)
            i += 1
            continue
        parts = ([before.title] if before else []) + [a.title for a in after]
        number = re.sub(r'\s+', ' ', num.text.strip()).rstrip('.')
        title = _textbook_heading_text(_PUA_RE.sub("-", f"{number} {_join_title_parts(parts)}"))
        lines = (before.lines if before else []) + b.lines + [l for a in after for l in a.lines]
        new = Block("heading", lines, title=title, level=2, text=title)
        if before is not None:
            del out[k]
        out.append(new)
        if after:
            out.extend(tail)
        merged += 1
        # Мусор после последней строки названия не съеден — вернётся в цикл.
        i = j - len(skipped)
    return out, merged


def _normalize_heading_levels(blocks: List[Block]) -> int:
    """Нестр. заголовки не вытесняют главу и § из стека.

    Глава — уровень 1, § — 2. Крупная рубрика внутри главы («ИТОГИ ГЛАВЫ»,
    «ВОПРОСЫ И ЗАДАНИЯ К ГЛАВЕ», кегль ≥ CHAPTER_RATIO) получила бы уровень 1
    и вытолкнула бы главу — после первой главы она уровень 2, как §. Рубрика
    внутри параграфа («ПОДВЕДЁМ ИТОГИ», кегль ≥ SECTION_RATIO) после первого
    § — уровень 3, иначе путь следующих абзацев потерял бы параграф.
    """
    seen_chapter = False
    seen_section = False
    changed = 0
    for b in blocks:
        if b.kind != "heading" or b.inline:
            continue
        if _is_structural_heading(b):
            seen_chapter = seen_chapter or b.level == 1
            seen_section = seen_section or b.level == 2
            continue
        if seen_chapter and b.level == 1:
            b.level = 2
            changed += 1
        elif seen_section and b.level == 2:
            b.level = 3
            changed += 1
    return changed


def _front_matter_cutoff(blocks: List[Block]) -> int:
    """Индекс первого блока «Глава»/«§»; всё до него — титул, аннотация, оглавление.

    Срабатывает только для настоящей книги (≥ 20 страниц), когда такая глава
    стоит в начале (первые 12 % страниц) и текст до неё короче 10 % документа:
    у короткой выдержки или документа без структуры терять нечего нельзя.
    """
    if not blocks:
        return 0
    last_page = max((b.page_end for b in blocks if b.lines), default=1) or 1
    if last_page < FRONT_MATTER_MIN_PAGES:
        return 0
    total = sum(len(b.text) for b in blocks if b.kind in ("body", "inset", "caption")) or 1
    for i, b in enumerate(blocks):
        if b.kind == "heading" and _is_structural_heading(b):
            before = sum(len(x.text) for x in blocks[:i] if x.kind in ("body", "inset", "caption"))
            if b.page_start <= last_page * FRONT_MATTER_MAX_PAGE_SHARE and before / total <= FRONT_MATTER_MAX_TEXT_SHARE:
                return i
            return 0
    return 0


_TOC_HEADINGS = frozenset({"оглавление", "содержание"})
# Чем может быть список терминов под блочной рубрикой.
_RUBRIC_LIST_KINDS = ("inset", "inset_short", "debris", "caption", "caption_short", "exercise", BLOCK_RUBRIC_KIND)


def build_tree(blocks: List[Block], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    root: Dict[str, Any] = {"level": 0, "title": "", "content": "", "children": []}
    stack: List[Dict[str, Any]] = [root]
    preface_chars = 0
    total_chars = 0
    seen_heading = False
    skip_kind: Optional[str] = None  # exercise/front_matter/apparatus под заголовком-аппаратом
    skip_page = 0                    # страница рубрики, поставившей skip_kind
    skip_capped = False              # front_matter-пропуск исчерпан предохранителем
    skip_cap_ok = False              # рубрика допускает предохранитель (не оглавление)
    seen_section = False             # был заголовок «§»
    # Блочная рубрика («КЛЮЧЕВЫЕ ПОНЯТИЯ»): страница и сколько блоков-списков под ней уже выброшено.
    rubric_page: Optional[int] = None
    rubric_taken = 0
    cutoff = _front_matter_cutoff(blocks)
    if cutoff:
        for b in blocks[:cutoff]:
            if b.kind in ("body", "inset", "caption", "heading"):
                _stats_add(stats, "dropped_blocks", "front_matter", len(b.text))
        blocks = blocks[cutoff:]

    def _append_content(node: Dict[str, Any], text: str, page_start: int, page_end: int) -> None:
        node["content"] = f"{node['content']}\n\n{text}" if node.get("content") else text
        if node.get("page_start") is None:
            node["page_start"] = page_start
        node["page_end"] = page_end
        # Страницы по абзацам: упаковщик ставит чанку страницы его абзацев,
        # а не всего узла (иначе у всех чанков параграфа один диапазон).
        node.setdefault("para_pages", []).append(
            {"head": re.sub(r'\s+', '', text)[:40], "page_start": page_start, "page_end": page_end}
        )

    def _count_capped() -> None:
        if stats is None:
            return
        layout = stats.setdefault("layout", {})
        layout["front_matter_skip_capped"] = layout.get("front_matter_skip_capped", 0) + 1

    def _capped(b: Block) -> bool:
        """Предохранитель: front_matter-рубрика внутри параграфа не съедает
        изложение дальше своей страницы (нераспознанная рубрика, а за ней
        нераспознанный заголовок раздела — и терялся весь раздел)."""
        nonlocal skip_kind, skip_capped
        if skip_capped:
            return True
        if (
            skip_kind == "front_matter" and skip_cap_ok and seen_section and b.kind == "body"
            and b.page_start > skip_page
            # Спасаем изложение, а не титульные строки и выходные данные.
            and len(b.text) >= INSET_MIN_CHARS and count_sentences(b.text) >= 2
        ):
            skip_capped = True
            skip_kind = None
            # Считаем срабатывания (спасённый body), а не всё, что после него осталось.
            _count_capped()
            return True
        return False

    for b in blocks:
        if rubric_page is not None:
            # Под блочной рубрикой — список терминов (inset/inset_short/…) на той же
            # странице: первый блок забираем любой, следующие — если это продолжение
            # списка («• брак • ценность»). Изложение и заголовок рубрику закрывают.
            text = (b.text or _join_lines(b.lines)).lstrip()
            if b.kind in ("junk", "figure_text"):
                pass
            elif (
                b.kind in _RUBRIC_LIST_KINDS and b.page_start == rubric_page
                and (rubric_taken == 0 or text.startswith("•"))
            ):
                _stats_add(stats, "dropped_blocks", BLOCK_RUBRIC_KIND, len(text))
                rubric_taken += 1
                continue
            else:
                rubric_page = None
        if b.kind == BLOCK_RUBRIC_KIND:
            # Рубрика вместе со списком на плашке (Обществознание 9).
            _stats_add(stats, "dropped_blocks", BLOCK_RUBRIC_KIND, len(b.text or ""))
            rubric_page, rubric_taken = b.page_end, 1
            continue
        if b.kind == "heading" and block_rubric_kind(b.title):
            # Узел не заводим и стек не трогаем: изложение после рубрики
            # остаётся в текущем параграфе/разделе.
            _stats_add(stats, "dropped_blocks", BLOCK_RUBRIC_KIND, len(b.title))
            rubric_page, rubric_taken = b.page_end, 0
            skip_kind, skip_capped = None, False
            continue
        if b.kind == "heading":
            seen_heading = True
            kind = heading_kind(b.title)
            skip_kind = kind if kind in DROP_HEADING_KINDS else None
            skip_page, skip_capped = b.page_end, False
            # Оглавление в конце книги идёт перед выходными данными — его
            # пропуск предохранитель не обрывает.
            skip_cap_ok = normalize_heading(b.title) not in _TOC_HEADINGS
            if b.level == 2 and _is_structural_heading(b):
                seen_section = True
            while len(stack) > 1 and stack[-1]["level"] >= b.level:
                stack.pop()
            node = {
                "level": b.level, "title": b.title, "content": "", "children": [],
                "page_start": b.page_start, "page_end": b.page_end,
            }
            if kind == "glossary":
                node["block_type"] = "glossary"
            stack[-1]["children"].append(node)
            stack.append(node)
            continue
        if b.kind in ("junk", "figure_text", "debris", "caption_short", "inset_short", "inset_mixed") or b.kind in DROP_HEADING_KINDS:
            _stats_add(stats, "dropped_blocks", b.kind, len(b.text or "".join(l.text for l in b.lines)))
            continue
        if (skip_kind or skip_capped) and b.kind in ("body", "inset", "caption"):
            if not _capped(b):
                _stats_add(stats, "dropped_blocks", skip_kind, len(b.text))
                continue
        total_chars += len(b.text)
        if b.kind == "body":
            if not seen_heading:
                preface_chars += len(b.text)
            _append_content(stack[-1], b.text, b.page_start, b.page_end)
            continue
        child = {
            "level": stack[-1]["level"] + 1, "title": b.title, "content": b.text, "children": [],
            "block_type": b.kind, "page_start": b.page_start, "page_end": b.page_end,
        }
        stack[-1]["children"].append(child)

    # Титул/оглавление до первой главы: короткий преамбульный текст без заголовка.
    if seen_heading and root.get("content") and total_chars and preface_chars / total_chars < FRONT_MATTER_MAX_SHARE:
        _stats_add(stats, "dropped_blocks", "front_matter", len(root["content"]))
        root["content"] = ""
        root.pop("page_start", None)
        root.pop("page_end", None)
    return root


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def _merge_sentence_across_heading(blocks: List[Block], stats: Optional[Dict[str, Any]] = None) -> List[Block]:
    """Заголовок, вклинившийся в предложение, — колонтитул или рубрика вёрстки.

    body без конца предложения → нестр. заголовок (не глава/§/подзаголовок) →
    (мусор/врезки) → body со строчной буквы: заголовок выбрасывается, а второй
    абзац приклеивается к первому. Иначе gate видит два обрывка.
    """
    out: List[Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if (
            b.kind == "body" and b.lines and not ends_like_sentence(b.lines[-1].text)
            and i + 1 < n and blocks[i + 1].kind == "heading"
            and not blocks[i + 1].inline and not _is_structural_heading(blocks[i + 1])
        ):
            j = i + 2
            while j < n and blocks[j].kind in ("junk", "figure_text", "debris", "aux", "caption_short", "inset_short", "inset_mixed"):
                j += 1
            if j < n and blocks[j].kind == "body" and blocks[j].lines and blocks[j].lines[0].text.strip()[:1].islower():
                heading = blocks[i + 1]
                _stats_add(stats, "dropped_blocks", "junk", len(heading.text or " ".join(l.text for l in heading.lines)))
                b.lines.extend(blocks[j].lines)
                out.append(b)
                out.extend(blocks[i + 2:j])
                i = j + 1
                continue
        out.append(b)
        i += 1
    return out


def extract_layout_blocks(path: str, stats: Optional[Dict[str, Any]] = None) -> Tuple[List[Block], Profile]:
    lines, n_pages, page_sizes = _read_lines(path)
    prof = profile_document(lines, n_pages)
    counts: Counter = Counter()
    for line in lines:
        line.cls = classify_line(line, prof, page_sizes[line.page])
    refined = refine_line_classes(lines, prof)
    for line in lines:
        counts[line.cls] += 1
    runin_count = mark_runin_headings([l for l in lines if l.cls == "body"], prof)
    blocks = _merge_sentence_across_heading(group_blocks(lines, prof), stats)
    blocks = [classify_block(b, prof) for b in blocks]
    blocks, section_titles = _merge_section_titles(blocks, prof)
    demoted_levels = _normalize_heading_levels(blocks)
    if stats is not None:
        stats["layout"] = {
            "pages": n_pages,
            "body_font": [prof.body_family, prof.body_size],
            "body_share": round(prof.body_share, 3),
            "body_x0": prof.body_x0,
            "indent_x0": prof.indent_x0,
            "lines": dict(counts),
            "blocks": dict(Counter(b.kind for b in blocks)),
            "runin_headings": runin_count,
            "chapters_synthesized": refined["chapters_synthesized"],
            "inset_titles": refined["inset_titles"],
            "heading_levels_demoted": demoted_levels,
            "section_titles_merged": section_titles,
            # Заполняет build_tree: body, спасённые от front_matter-пропуска.
            "front_matter_skip_capped": 0,
            "body_color": prof.body_color,
        }
    logger.info(
        "Layout profile: body=%s %.1f (%.0f%%), x0=%.0f indent=%s, lines=%s, blocks=%s",
        prof.body_family, prof.body_size, prof.body_share * 100, prof.body_x0, prof.indent_x0,
        dict(counts), dict(Counter(b.kind for b in blocks)),
    )
    return blocks, prof


def extract_layout_tree(path: str, kind: str = "textbook", stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """PDF → дерево с врезками/подписями как дочерними узлами (block_type)."""
    if not str(path).lower().endswith(".pdf"):
        raise LayoutUnavailable("layout extraction supports PDF only")
    blocks, _prof = extract_layout_blocks(path, stats)
    return build_tree(blocks, stats)


def layout_blocks_to_paragraphs(blocks: List[Block]) -> List[Dict[str, Any]]:
    """Блоки → абзацы с подсказками для LLM-сегментации (метод llm поверх layout)."""
    out: List[Dict[str, Any]] = []
    pending_runin = ""
    for b in blocks:
        if b.kind in ("junk", "figure_text", "debris", "inset_short", "inset_mixed", "front_matter", "apparatus", BLOCK_RUBRIC_KIND):
            continue
        if b.kind == "heading" and b.inline:
            # Подзаголовок без своих строк: текст уже в следующем абзаце,
            # которому и достаётся runin.
            pending_runin = b.title
            continue
        hint = {"heading": "заголовок", "inset": "мелкий шрифт/заливка", "caption": "подпись у картинки",
                "exercise": "задание", "caption_short": "подпись"}.get(b.kind)
        for para in [p for p in (b.text or "").split("\n\n") if p.strip()]:
            item = {"text": para.strip(), "page": b.page_start, "hint": hint, "kind": b.kind}
            if pending_runin and b.kind == "body":
                item["runin"] = pending_runin
                item["hint"] = "начинается с подзаголовка"
                pending_runin = ""
            out.append(item)
    return out
