"""Детерминированный генератор синтетических документов для тестов чанкера.

Документы вымышленные (Зелёная долина, сады на террасах) и воспроизводят
приёмы вёрстки, которые разбирает чанкер: главы и параграфы учебника, врезки на
заливке, подписи, рубрики-аппарат, колонтитулы, статьи энциклопедии в две
колонки, главы и статьи нормативного акта.

    python tests/fixtures/synthetic/build.py            # → tests/data/synthetic/
    python tests/fixtures/synthetic/build.py --out DIR

Один и тот же вход даёт байт-в-байт тот же выход: даты метаданных
фиксированы, случайностей нет, zip-контейнер DOCX пересобирается с
постоянными отметками времени.
"""

from __future__ import annotations

import argparse
import datetime
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
DEFAULT_OUT = HERE.parent.parent / "data" / "synthetic"

TEXTBOOK = "textbook_valley_gardens.pdf"
ENCYCLOPEDIA = "encyclopedia_valley.pdf"
NORMATIVE = "normative_terraces_law.docx"
OUTPUTS = (TEXTBOOK, ENCYCLOPEDIA, NORMATIVE)

FIXED_DATE = datetime.datetime(2024, 1, 1, 0, 0, 0)
ZIP_DATE = (2024, 1, 1, 0, 0, 0)

SERIF = "DejaVuSerif"
SANS_BOLD = "DejaVuSans-Bold"

# A4 в пунктах; координата top — от верхнего края, как у pdfplumber.
PAGE_W, PAGE_H = 595.0, 842.0
LEFT = 57.0
TEXT_W = 481.0
INDENT = 17.0
TOP_MARGIN = 80.0
BOTTOM_MARGIN = 70.0
LEADING = 1.3
BOX_PAD = 8.0
BOX_GAP = 12.0


# ---------------------------------------------------------------------------
# Стили и набор строк
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Style:
    font: str
    size: float
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0)


BODY = Style(SERIF, 11.0)
SMALL = Style(SERIF, 9.5)
CAPTION = Style(SERIF, 9.0)
RUNIN = Style(SANS_BOLD, 11.0, (0.75, 0.2, 0.2))
RUBRIC = Style(SANS_BOLD, 11.0, (0.15, 0.35, 0.55))
SECTION = Style(SANS_BOLD, 16.0, (0.15, 0.35, 0.55))
SECTION_NO = Style(SANS_BOLD, 26.0, (0.15, 0.35, 0.55))
CHAPTER = Style(SANS_BOLD, 24.0, (0.15, 0.35, 0.55))
CHAPTER_NO = Style(SANS_BOLD, 60.0, (0.15, 0.35, 0.55))
INSET_HEAD = Style(SANS_BOLD, 14.0, (0.15, 0.35, 0.55))
COLUMN_HEAD = Style(SANS_BOLD, 13.0, (0.15, 0.35, 0.55))
RUNNING = Style(SANS_BOLD, 9.0, (0.4, 0.4, 0.4))
ENTRY_HEAD = Style(SANS_BOLD, 10.5)
ENTRY = Style(SERIF, 10.5)
TITLE = Style(SANS_BOLD, 14.0)

FILL_INSET = (0.93, 0.95, 0.86)
FILL_LEFT = (0.9, 0.94, 0.97)
FILL_RIGHT = (0.97, 0.92, 0.88)
FILL_SUMMARY = (0.95, 0.93, 0.85)

Run = Tuple[Style, str]
Word = Tuple[Style, str]


def register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, file in ((SERIF, "DejaVuSerif.ttf"), (SANS_BOLD, "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))


def words_of(runs: Sequence[Run]) -> List[Word]:
    return [(style, w) for style, text in runs for w in text.split()]


class Setter:
    """Перенос и отрисовка строк.

    spaced=False — слова ставятся по координатам без глифов пробела (так
    свёрстаны учебники, на которых настраивался layout-разбор); spaced=True —
    строка рисуется с пробелами, как её выводит текстовый редактор.
    """

    def __init__(self, spaced: bool):
        self.spaced = spaced

    def width(self, word: Word) -> float:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        style, text = word
        return stringWidth(text, style.font, style.size)

    def gap(self, style: Style) -> float:
        if self.spaced:
            return self.width((style, " "))
        return max(3.6, 0.4 * style.size)

    def wrap(self, runs: Sequence[Run], width: float, indent: float = 0.0) -> List[List[Word]]:
        lines: List[List[Word]] = [[]]
        used, limit = 0.0, width - indent
        for word in words_of(runs):
            need = self.width(word) + (self.gap(lines[-1][-1][0]) if lines[-1] else 0.0)
            if lines[-1] and used + need > limit:
                lines.append([])
                used, limit, need = 0.0, width, self.width(word)
            lines[-1].append(word)
            used += need
        return _no_orphan(lines)

    def draw(self, c, x: float, top: float, line: Sequence[Word]) -> None:
        if self.spaced:
            self._draw_spaced(c, x, top, line)
            return
        for word in line:
            _draw_text(c, x, top, word)
            x += self.width(word) + self.gap(word[0])

    def _draw_spaced(self, c, x: float, top: float, line: Sequence[Word]) -> None:
        for style, text in _style_groups(line):
            _draw_text(c, x, top, (style, text))
            x += self.width((style, text))


def _no_orphan(lines: List[List[Word]]) -> List[List[Word]]:
    """Последняя строка абзаца — не короче двух слов (колонки во врезках делятся по словам)."""
    if len(lines) > 1 and len(lines[-1]) == 1 and len(lines[-2]) > 2:
        lines[-1].insert(0, lines[-2].pop())
    return lines


def _style_groups(line: Sequence[Word]) -> List[Run]:
    groups: List[Run] = []
    for style, text in line:
        if groups and groups[-1][0] == style:
            groups[-1] = (style, f"{groups[-1][1]} {text}")
        else:
            if groups:
                groups[-1] = (groups[-1][0], groups[-1][1] + " ")
            groups.append((style, text))
    return groups


def _draw_text(c, x: float, top: float, word: Word) -> None:
    style, text = word
    c.setFont(style.font, style.size)
    c.setFillColorRGB(*style.color)
    c.drawString(x, PAGE_H - top - style.size, text)


def new_canvas(path: Path, title: str):
    from reportlab.pdfgen import canvas

    register_fonts()
    c = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H), invariant=1, pageCompression=1)
    c.setTitle(title)
    c.setAuthor("GenA synthetic fixtures")
    c.setSubject("Synthetic test document")
    return c


# ---------------------------------------------------------------------------
# Поток страниц
# ---------------------------------------------------------------------------

@dataclass
class Part:
    """Абзац внутри врезки: строки, стиль (для интерлиньяжа) и отступ первой строки."""

    runs: Sequence[Run]
    style: Style
    indent: float = 0.0


class Flow:
    """Вёрстка сверху вниз: курсор top, колонтитул, номер страницы внизу."""

    def __init__(self, c, setter: Setter, header: Callable[[int, str], Optional[str]]):
        self.c, self.s, self.header = c, setter, header
        self.page, self.top, self.running = 1, TOP_MARGIN, ""

    def new_page(self) -> None:
        self._footer()
        self.c.showPage()
        self.page += 1
        self.top = TOP_MARGIN
        text = self.header(self.page, self.running)
        if text:
            self.s.draw(self.c, LEFT, 30.0, words_of([(RUNNING, text)]))

    def finish(self) -> None:
        self._footer()
        self.c.save()

    def _footer(self) -> None:
        self.s.draw(self.c, PAGE_W / 2, PAGE_H - 45.0, [(RUNNING, str(self.page))])

    def fits(self, height: float) -> bool:
        return self.top + height <= PAGE_H - BOTTOM_MARGIN

    def text_at(self, x: float, top: float, style: Style, text: str) -> None:
        self.s.draw(self.c, x, top, words_of([(style, text)]))

    def paragraph(self, runs: Sequence[Run], style: Style = BODY, indent: float = INDENT,
                  break_before: Optional[int] = None) -> None:
        lead = style.size * LEADING
        for i, line in enumerate(self.s.wrap(runs, TEXT_W, indent)):
            if i == break_before or not self.fits(lead):
                self.new_page()
            self.s.draw(self.c, LEFT + (indent if i == 0 else 0.0), self.top, line)
            self.top += lead

    def heading(self, style: Style, text: str) -> None:
        self.top += style.size * 0.6
        self.text_at(LEFT, self.top, style, text)
        self.top += style.size * LEADING + 2.0

    def _layout(self, parts: Sequence[Part], width: float) -> Tuple[List[Tuple[float, List[Word], float]], float]:
        placed = []
        for part in parts:
            lead = part.style.size * LEADING
            for i, line in enumerate(self.s.wrap(part.runs, width - 2 * BOX_PAD, part.indent)):
                placed.append((part.indent if i == 0 else 0.0, line, lead))
        return placed, sum(p[2] for p in placed) + 2 * BOX_PAD

    def boxes(self, columns: Sequence[Tuple[Sequence[Part], Tuple[float, float, float]]], gutter: float = 20.0) -> None:
        """Врезки на заливке рядом (одна — на всю ширину), одной высоты."""
        width = (TEXT_W - gutter * (len(columns) - 1)) / len(columns)
        laid = [self._layout(parts, width) for parts, _fill in columns]
        height = max(h for _p, h in laid)
        self.top += BOX_GAP
        if not self.fits(height):
            self.new_page()
        for k, ((placed, _h), (_parts, fill)) in enumerate(zip(laid, columns)):
            self._draw_box(LEFT + k * (width + gutter), width, height, fill, placed)
        self.top += height + BOX_GAP

    def _draw_box(self, x: float, width: float, height: float, fill, placed) -> None:
        self.c.setFillColorRGB(*fill)
        self.c.rect(x, PAGE_H - self.top - height, width, height, stroke=0, fill=1)
        top = self.top + BOX_PAD
        for indent, line, lead in placed:
            self.s.draw(self.c, x + BOX_PAD + indent, top, line)
            top += lead

    def figure(self, caption: str, width: float = 220.0, height: float = 110.0) -> None:
        from reportlab.lib.utils import ImageReader

        self.top += BOX_GAP
        if not self.fits(height + 40.0):
            self.new_page()
        self.c.drawImage(ImageReader(_terrace_picture()), LEFT, PAGE_H - self.top - height, width, height)
        self.top += height + 6.0
        lead = CAPTION.size * LEADING
        for line in self.s.wrap([(CAPTION, caption)], 300.0):
            self.s.draw(self.c, LEFT, self.top, line)
            self.top += lead
        self.top += BOX_GAP


def _terrace_picture():
    """Картинка «террасы на склоне»: ступени и солнце, без случайностей."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (220, 110), (225, 238, 250))
    draw = ImageDraw.Draw(img)
    for k in range(5):
        y = 30 + k * 16
        draw.rectangle([k * 40, y, 220, y + 16], fill=(120 + k * 20, 170, 90))
    draw.ellipse([180, 8, 206, 34], fill=(240, 200, 60))
    return img


# ---------------------------------------------------------------------------
# Учебник
# ---------------------------------------------------------------------------

CHAPTER_WORD = "ГЛАВА"
CHAPTER_TITLE = ("САДЫ ЗЕЛЁНОЙ", "ДОЛИНЫ")
SECTION_1 = ("Как устроен", "сад на склоне", "холма")
SECTION_1_RUNNING = "§ 1. Как устроен сад на склоне холма"
SECTION_2 = "§ 2. Вода в саду"

CHAPTER_INTRO = (
    "В этой главе речь идёт о садах Зелёной долины. Долина лежит между двумя холмами. "
    "Её жители давно выращивают яблони, груши и травы. Сады стоят прямо на склонах, "
    "поэтому земля не пропадает зря, а вода не уходит вниз.",
    "Мы узнаем, как устроен сад на склоне, кто его строил и как в нём хранят воду. "
    "Каждый параграф заканчивается итогами и вопросами для повторения.",
)
KEY_TERMS = "• терраса • подпорная стенка • полив • почва"
SECTION_1_LEAD = (
    "Сад на склоне не похож на сад на равнине. Земля здесь лежит под углом. Дождь быстро "
    "стекает вниз и уносит с собой верхний слой почвы. Поэтому садоводы долины придумали "
    "особый способ работы со склоном. Они делят склон на ровные ступени.",
    "Каждая ступень называется террасой. На террасе растут деревья и травы. Ступени идут "
    "одна над другой, как лестница. Сверху сад похож на зелёные полосы.",
)
RUNIN_1 = ("1 Почва и вода.", "Почва на склоне тонкая. Под ней лежит камень. Садоводы приносят землю "
           "снизу и насыпают её ровным слоем. Сверху кладут сухую траву. Трава держит влагу и не "
           "даёт земле пересохнуть летом.")
RUNIN_2 = ("2 Террасы на склонах.", "Террасу строят от нижнего края склона к верхнему. Сначала выкладывают "
           "стенку из камня. Потом за стенку насыпают землю. Ширина террасы зависит от крутизны "
           "склона. На пологом склоне терраса широкая, на крутом склоне она узкая.")
FIGURE_CAPTION = "Рис. 1. Террасы на южном склоне. Каждая ступень держит воду после дождя."
INSET_PLAIN_HEAD = "ЛЕТОПИСЬ САДА"
INSET_PLAIN = (
    "Первые записи о садах долины сделал хранитель общины. Он отмечал, когда цвели яблони "
    "и когда созревали груши. Записи велись каждый год без перерыва. По ним видно, что весна "
    "в долине наступает в разное время. Летопись хранится в доме общины."
)
QUESTION_HEADING = "ЗАЧЕМ САДУ ТЕРРАСЫ?"
QUESTION_BODY = (
    "Терраса задерживает дождевую воду. Вода не скатывается вниз, а медленно уходит в землю. "
    "Корни деревьев получают влагу даже в сухие недели.",
    "Терраса защищает почву. Ровная ступень не даёт земле сползать. Садовод может работать "
    "на террасе так же удобно, как на поле.",
)
RUNIN_3 = ("3 Каменные стенки.", "Стенку кладут из плоских камней без раствора. Между камнями остаются "
           "щели. Через щели уходит лишняя вода, поэтому стенка стоит долго. Камни для стенок "
           "собирают на вершине холма.")
BIO_NAME = "Лесович Мирон Остапович"
BIO_TEXT = (
    "(1801—1870) — садовод из верхней деревни. Он первым построил террасы на северном склоне. "
    "Мирон Остапович записывал, сколько воды получает каждая ступень. Его заметки помогли "
    "соседям выбрать место для новых садов. В старости он учил детей общины ухаживать за "
    "яблонями."
)
CROSSING = (
    "Сады на террасах требуют постоянной заботы. Весной садоводы проверяют стенки и "
    "поправляют камни, которые сдвинулись за зиму. Летом они поливают молодые деревья и "
    "убирают сухую траву. Осенью собирают урожай и укрывают корни листьями. Так работа "
    "в саду идёт круглый год, и каждое время года приносит свои заботы, о которых "
    "садоводы долины рассказывают детям с ранних лет."
)
CROSSING_BREAK_LINE = 3
COLUMN_LEFT_HEAD = "ВЕРХНИЙ СКЛОН"
COLUMN_LEFT = (
    "На верхнем склоне мало почвы и много камня. Здесь растут травы и низкие кусты. "
    "Террасы узкие, их ширина не больше двух шагов. Воду сюда носят вручную."
)
COLUMN_RIGHT_HEAD = "НИЖНИЙ СКЛОН"
COLUMN_RIGHT = (
    "На нижнем склоне почва толстая и влажная. Здесь растут яблони и груши. Террасы "
    "широкие, по ним проходит тропа. Воду сюда приводит канава от пруда."
)
SUMMARY_HEAD = "ПОДВЕДЁМ ИТОГИ"
SUMMARY = (
    "Сад на склоне состоит из террас. Террасы держат воду и почву. Стенки из камня "
    "стоят долго, потому что пропускают лишнюю воду. Верхний и нижний склоны устроены "
    "по-разному."
)
EXERCISES_HEAD = "ВОПРОСЫ И ЗАДАНИЯ"
EXERCISES = (
    "1. Чем сад на склоне отличается от сада на равнине?",
    "2. Зачем садоводы насыпают землю на террасы?",
    "3. Объясните, как каменная стенка удерживает почву.",
)
MATERIALS_HEAD = "ДОПОЛНИТЕЛЬНЫЕ МАТЕРИАЛЫ"
MATERIALS = (
    "1. «Сады на холмах». Учебный фильм о террасах долины.",
    "2. «Год садовода». Книга для чтения о работе в саду.",
)
SECTION_2_LEAD = (
    "Воды на склоне всегда не хватает. Летом дожди идут редко, а солнце светит долго. "
    "Поэтому в каждом саду долины есть свой запас воды. Садоводы берегут его и делят "
    "между террасами поровну.",
)
RUNIN_4 = ("1 Колодцы и пруды.", "У подножия холма вырыты колодцы. Выше по склону стоят небольшие пруды. "
           "Весной пруды наполняются талой водой. Летом из них берут воду для полива.")
RUNIN_5 = ("2 Полив по каплям.", "Из пруда к деревьям тянутся тонкие трубки. Вода идёт по ним медленно, "
           "капля за каплей. Так она попадает прямо к корням и не растекается по тропам.")


def _textbook_header(page: int, running: str) -> Optional[str]:
    if page == 1:
        return None
    return f"{page} ГЛАВА I" if page % 2 == 0 else f"{running} {page}"


def _chapter_page(flow: Flow) -> None:
    flow.text_at(LEFT, 150.0, CHAPTER, CHAPTER_WORD)
    flow.text_at(200.0, 150.0, CHAPTER, CHAPTER_TITLE[0])
    flow.text_at(200.0, 180.0, CHAPTER, CHAPTER_TITLE[1])
    flow.text_at(LEFT + 15.0, 215.0, CHAPTER_NO, "I")
    flow.top = 330.0
    for para in CHAPTER_INTRO:
        flow.paragraph([(BODY, para)])


def _section_one_title(flow: Flow) -> None:
    """Название в три строки справа, крупный «§ 1» слева на уровне второй строки."""
    for k, line in enumerate(SECTION_1):
        flow.text_at(130.0, 80.0 + 20.0 * k, SECTION, line)
    flow.text_at(LEFT, 90.0, SECTION_NO, "§ 1")
    flow.top = 160.0


def _runin(flow: Flow, item: Tuple[str, str]) -> None:
    flow.paragraph([(RUNIN, item[0]), (BODY, item[1])])


def _section_one(flow: Flow) -> None:
    flow.running = SECTION_1_RUNNING
    flow.new_page()
    _section_one_title(flow)
    flow.heading(RUBRIC, "КЛЮЧЕВЫЕ ПОНЯТИЯ")
    flow.paragraph([(SMALL, KEY_TERMS)], style=SMALL, indent=0.0)
    flow.top += 10.0
    for para in SECTION_1_LEAD:
        flow.paragraph([(BODY, para)])
    _runin(flow, RUNIN_1)
    flow.figure(FIGURE_CAPTION)
    _runin(flow, RUNIN_2)
    flow.boxes([([Part([(INSET_HEAD, INSET_PLAIN_HEAD)], INSET_HEAD), Part([(SMALL, INSET_PLAIN)], SMALL)], FILL_INSET)])
    flow.new_page()
    flow.heading(RUBRIC, QUESTION_HEADING)
    for para in QUESTION_BODY:
        flow.paragraph([(BODY, para)])
    _runin(flow, RUNIN_3)
    flow.boxes([([Part([(INSET_HEAD, BIO_NAME)], INSET_HEAD), Part([(SMALL, BIO_TEXT)], SMALL)], FILL_INSET)])
    flow.top = max(flow.top, PAGE_H - BOTTOM_MARGIN - BODY.size * LEADING * CROSSING_BREAK_LINE)
    flow.paragraph([(BODY, CROSSING)], break_before=CROSSING_BREAK_LINE)


def _section_one_apparatus(flow: Flow) -> None:
    flow.boxes([
        ([Part([(COLUMN_HEAD, COLUMN_LEFT_HEAD)], COLUMN_HEAD), Part([(SMALL, COLUMN_LEFT)], SMALL)], FILL_LEFT),
        ([Part([(COLUMN_HEAD, COLUMN_RIGHT_HEAD)], COLUMN_HEAD), Part([(SMALL, COLUMN_RIGHT)], SMALL)], FILL_RIGHT),
    ])
    flow.boxes([([Part([(SECTION, SUMMARY_HEAD)], SECTION), Part([(BODY, SUMMARY)], BODY, INDENT)], FILL_SUMMARY)])
    flow.heading(RUBRIC, EXERCISES_HEAD)
    for item in EXERCISES:
        flow.paragraph([(BODY, item)])
    flow.heading(RUBRIC, MATERIALS_HEAD)
    for item in MATERIALS:
        flow.paragraph([(BODY, item)])


def _section_two(flow: Flow) -> None:
    flow.running = SECTION_2
    flow.new_page()
    flow.heading(SECTION, SECTION_2)
    for para in SECTION_2_LEAD:
        flow.paragraph([(BODY, para)])
    _runin(flow, RUNIN_4)
    _runin(flow, RUNIN_5)


def build_textbook(path: Path, spaced: bool = False) -> Path:
    flow = Flow(new_canvas(path, "Сады Зелёной долины"), Setter(spaced), _textbook_header)
    _chapter_page(flow)
    _section_one(flow)
    _section_one_apparatus(flow)
    _section_two(flow)
    flow.finish()
    return path


# ---------------------------------------------------------------------------
# Энциклопедия
# ---------------------------------------------------------------------------

ENCYCLOPEDIA_TITLE = "МАЛАЯ ЭНЦИКЛОПЕДИЯ ЗЕЛЁНОЙ ДОЛИНЫ"
ENTRY_SIGNATURE = "А. Б. Садовая."
ENTRIES: Tuple[Tuple[str, str], ...] = (
    ("ВЕРХНЯЯ ДЕРЕВНЯ,", "поселение в северной части Зелёной долины. Стоит на вершине холма у "
     "старой дороги. В деревне около сорока домов. Жители занимаются садами и пасут овец. "
     "Отсюда начинается тропа к северному склону."),
    ("ГРУШЕВАЯ АЛЛЕЯ,", "дорога вдоль нижнего пруда, обсаженная грушами. Длина аллеи около "
     "одной версты. Деревья посадили жители нижней деревни. Осенью на аллее проходит "
     "праздник урожая."),
    ("ДОЛИННЫЙ ЯБЛОНЕВЫЙ САД,", "самый большой сад долины. Занимает весь южный склон. В саду "
     "растёт около трёхсот яблонь разных сортов. За садом ухаживает община. Урожай делят "
     "между всеми семьями долины."),
    ("КАМЕННАЯ СТЕНКА,", "ограда террасы из плоских камней. Камни кладут без раствора, "
     "поэтому вода свободно проходит через щели. Высота стенки обычно до пояса. Хорошая "
     "стенка стоит больше ста лет."),
    ("КАПЕЛЬНЫЙ ПОЛИВ,", "способ полива, при котором вода подаётся к корням по каплям. Воду "
     "берут из пруда и ведут по тонким трубкам. Способ экономит воду в сухое лето."),
    ("ЛЕСОВИЧ Мирон (1801—1870),", "садовод из верхней деревни. Первым построил "
     "террасы на северном склоне. Вёл записи о поливе каждой ступени. Учил детей общины "
     "ухаживать за яблонями."),
    ("ЛЕТОПИСЬ САДА,", "книга записей о жизни садов долины. Её ведёт хранитель общины. В "
     "летописи отмечают сроки цветения и сбора урожая. Самые старые записи сделаны почти "
     "двести лет назад."),
    ("НИЖНИЙ ПРУД,", "водоём у подножия южного склона. Наполняется талой водой весной. Из "
     "пруда берут воду для полива нижних террас. Берега пруда укреплены камнем."),
    ("ОБЩЕСТВО САДОВОДОВ ДОЛИНЫ,", "объединение жителей, которые ухаживают за садами. "
     "Собирается раз в месяц в доме общины. Общество решает, где строить новые террасы. "
     "Оно же хранит семена и саженцы."),
    ("ПОДПОРНАЯ СТЕНКА,", "стенка, которая удерживает землю террасы от сползания. Строится "
     "из камня или дерева. Ставится с небольшим наклоном к склону. Без такой стенки "
     "терраса размывается за одну зиму."),
    ("СЕВЕРНЫЙ СКЛОН,", "склон холма, обращённый к северу. Солнца здесь меньше, а снег "
     "лежит дольше. На северном склоне растут травы и ягодные кусты. Террасы здесь узкие "
     "и короткие."),
    ("ТЕРРАСА,", "ровная ступень на склоне холма. Удерживает воду и почву. Террасы идут "
     "одна над другой, как лестница. Каждая терраса укреплена подпорной стенкой."),
    ("ТРАВЯНОЙ ЛУГ,", "открытое место на вершине холма. На лугу растут клевер и мята. "
     "Траву с луга кладут на террасы, чтобы земля не пересыхала. Летом на лугу пасут овец."),
    ("ТРОПА САДОВОДОВ,", "дорожка вдоль верхних террас. Выложена плоским камнем. По тропе носят "
     "воду и корзины с урожаем. Ширина тропы позволяет разойтись двум людям."),
    ("ЦВЕТЕНИЕ ЯБЛОНЬ,", "время года, когда в садах долины распускаются цветы яблонь. Обычно "
     "приходится на середину весны. Сроки цветения записывают в летопись сада. По ним "
     "садоводы судят о будущем урожае."),
    ("ЧАЙНЫЙ ЛУГ,", "поляна у восточного края долины. Здесь собирают травы для настоев. "
     "Сбор трав идёт в начале лета. Луг не распахивают и не засаживают деревьями."),
    ("ШИРОКАЯ ТЕРРАСА,", "терраса на пологом участке склона. Ширина такой террасы больше "
     "десяти шагов. На широкой террасе растут яблони и груши в два ряда. Между рядами "
     "оставляют проход для тележки."),
    ("ЮЖНЫЙ СКЛОН,", "склон холма, обращённый к югу. Здесь тепло и много солнца. На "
     "южном склоне стоит самый большой сад долины. Весна здесь наступает раньше, чем на "
     "северном склоне."),
    ("ЯБЛОНЕВЫЙ ПИТОМНИК,", "участок, где выращивают молодые яблони. Находится у нижнего "
     "пруда. Саженцы растут в питомнике три года. Потом их пересаживают на террасы."),
)
COLUMN_W = 230.0
COLUMN_GUTTER = 21.0


# Подпись автора отдельной строкой под статьёй — чанкер её вырезает.
SIGNED_ENTRY = "ПОДПОРНАЯ СТЕНКА,"


def _entry_lines(setter: Setter) -> List[List[Word]]:
    lines: List[List[Word]] = []
    for head, text in ENTRIES:
        lines.extend(setter.wrap([(ENTRY_HEAD, head), (ENTRY, text)], COLUMN_W))
        if head == SIGNED_ENTRY:
            lines.append(words_of([(ENTRY, ENTRY_SIGNATURE)]))
        lines.append([])
    return lines


def _column_slots(first_top: float) -> Tuple[int, int]:
    lead = ENTRY.size * LEADING
    usable = PAGE_H - BOTTOM_MARGIN
    return int((usable - first_top) // lead), int((usable - TOP_MARGIN) // lead)


def build_encyclopedia(path: Path) -> Path:
    """Две колонки; в потоке содержимого сначала вся левая колонка, затем правая."""
    setter = Setter(spaced=True)
    flow = Flow(new_canvas(path, "Малая энциклопедия Зелёной долины"), setter, lambda _p, _r: None)
    flow.text_at(LEFT + 60.0, 60.0, TITLE, ENCYCLOPEDIA_TITLE)
    lines = _entry_lines(setter)
    first, later = _column_slots(110.0)
    top0, column = 110.0, 0
    lead = ENTRY.size * LEADING
    while lines:
        slots = first if flow.page == 1 else later
        chunk, lines = lines[:slots], lines[slots:]
        x = LEFT + column * (COLUMN_W + COLUMN_GUTTER)
        for k, line in enumerate(chunk):
            setter.draw(flow.c, x, top0 + k * lead, line)
        column = 1 - column
        if column == 0 and lines:
            flow.new_page()
            top0 = TOP_MARGIN
    flow.finish()
    return path


# ---------------------------------------------------------------------------
# Нормативный акт (DOCX)
# ---------------------------------------------------------------------------

LAW_TITLE = ("ЗАКОН ЗЕЛЁНОЙ ДОЛИНЫ", "О садовых террасах")
# (заголовок главы, [(заголовок статьи, [абзацы])])
LAW: Tuple[Tuple[str, Tuple[Tuple[str, Tuple[str, ...]], ...]], ...] = (
    ("Глава 1. Общие положения", (
        ("Статья 1. Предмет настоящего Закона", (
            "1. Настоящий Закон определяет порядок устройства и содержания садовых террас в Зелёной долине.",
            "2. Действие настоящего Закона распространяется на все склоны долины, включая земли общины.",
        )),
        ("Статья 2. Основные понятия", (
            "Для целей настоящего Закона используются следующие основные понятия:",
            "1) терраса — ровная ступень на склоне, укреплённая подпорной стенкой;",
            "2) подпорная стенка — стенка из камня или дерева, которая удерживает почву террасы;",
            "3) садовод — житель долины, который возделывает террасу.",
        )),
    )),
    ("Глава 2. Устройство террас", (
        ("Статья 3. Разрешение на устройство террасы", (
            "1. Новая терраса устраивается с согласия общества садоводов долины.",
            "2. Для получения согласия садовод представляет:",
            "1) описание места на склоне;",
            "2) план стенки и ширину будущей террасы;",
            "3) расчёт воды для полива.",
            "3. Общество садоводов рассматривает обращение в течение тридцати дней.",
        )),
        ("Статья 4. Требования к подпорной стенке", (
            "1. Подпорная стенка выкладывается из камня без раствора.",
            "2. Высота стенки не должна превышать одного метра.",
            "3. Стенка ставится с наклоном к склону не менее одной десятой её высоты.",
        )),
        ("Статья 5. Полив террас", (
            "1. Вода для полива берётся из прудов общины.",
            "2. Вода распределяется между террасами поровну по графику, который утверждает общество садоводов.",
        )),
    )),
    ("Глава 3. Ответственность и заключительные положения", (
        ("Статья 6. Обязанности садовода", (
            "1. Садовод содержит стенку своей террасы в исправном состоянии.",
            "2. Садовод, по вине которого разрушена стенка соседней террасы, восстанавливает её за свой счёт.",
        )),
        ("Статья 7. Вступление настоящего Закона в силу", (
            "Настоящий Закон вступает в силу со дня его принятия общим собранием жителей долины.",
        )),
    )),
)


def _law_document():
    from docx import Document

    doc = Document()
    for line in LAW_TITLE:
        doc.add_paragraph().add_run(line).bold = True
    for chapter, articles in LAW:
        doc.add_heading(chapter, level=1)
        for article, paragraphs in articles:
            doc.add_heading(article, level=2)
            for text in paragraphs:
                doc.add_paragraph(text)
    props = doc.core_properties
    props.author = props.last_modified_by = "GenA synthetic fixtures"
    props.title = " ".join(LAW_TITLE)
    props.created = props.modified = props.last_printed = FIXED_DATE
    props.revision = 1
    return doc


def _normalize_zip(raw: bytes) -> bytes:
    """Тот же zip, но с постоянными отметками времени и атрибутами записей."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            dst.writestr(info, src.read(name))
    return out.getvalue()


def build_normative(path: Path) -> Path:
    buf = io.BytesIO()
    _law_document().save(buf)
    path.write_bytes(_normalize_zip(buf.getvalue()))
    return path


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def build_all(out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        build_textbook(out_dir / TEXTBOOK),
        build_encyclopedia(out_dir / ENCYCLOPEDIA),
        build_normative(out_dir / NORMATIVE),
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Сгенерировать синтетические документы для тестов чанкера.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"каталог вывода (по умолчанию {DEFAULT_OUT})")
    args = ap.parse_args(argv)
    for path in build_all(args.out):
        print(f"{path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
