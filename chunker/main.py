"""
FastAPI сервис для создания чанков из документов для передачи в LLM.

Процесс обработки:
1. Документ (DOCX, PDF и т.д.) → docx2json_outline.py → JSON дерево структуры
2. JSON дерево → chunker → чанки для LLM

Endpoints:
- POST /chunk/ - обработка загруженного документа
- POST /chunk-docx/ - legacy endpoint для DOCX файлов
- GET /health - проверка состояния сервиса
- GET / - информация о сервисе
"""

import contextvars
import os
import re
import sys
import copy
import time
import json
import logging
import tempfile
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple
from fastapi import FastAPI, File, UploadFile, Query
from fastapi.responses import JSONResponse
from request_context import RequestIdMiddleware, install_error_handlers

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка путей для импорта docx2json_outline
# Файл теперь находится в той же директории
_current_dir = Path(__file__).parent.absolute()

# Добавляем текущую директорию в путь для импорта
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

try:
    from docx2json_outline import extract_outline_from_document
except ImportError:
    # Альтернативный способ импорта, если обычный не работает
    import importlib.util
    docx2json_path = _current_dir / "docx2json_outline.py"
    
    if docx2json_path.exists():
        spec = importlib.util.spec_from_file_location(
            "docx2json_outline", 
            docx2json_path
        )
        if spec and spec.loader:
            docx2json_outline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(docx2json_outline)
            extract_outline_from_document = docx2json_outline.extract_outline_from_document
        else:
            raise ImportError(
                f"Не удалось загрузить модуль docx2json_outline из {docx2json_path}"
            )
    else:
        raise ImportError(
            f"Файл docx2json_outline.py не найден в {_current_dir}. "
            "Убедитесь, что файл находится в той же директории, что и main.py"
        )

from blocks import KEPT_BLOCK_TYPES  # noqa: E402
from text_utils import (  # noqa: E402
    BLOCK_RUBRIC_KIND,
    DROP_HEADING_KINDS,
    block_rubric_kind,
    classify_dropped_paragraph,
    heading_kind,
    is_apparatus_line,
    is_materials_list,
    is_definition_paragraph,
    is_list_intro,
    is_list_item,
    split_sentences,
    strip_apparatus_lines,
    strip_exercise_lines,
    strip_numbered_tasks,
    strip_inline_labels,
    strip_table_lines,
    strip_table_marks,
    boundary_flags,
)

app = FastAPI(title="GenA Chunker Service", version="2.0.0")
app.add_middleware(RequestIdMiddleware)
install_error_handlers(app, logger)

MAX_UPLOAD_BYTES = int(os.getenv("CHUNKER_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "true").lower() not in ("0", "false", "no")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default


# Размеры чанков прозы. Дефолты прежние; переопределяются через env, чтобы
# подбирать их на стенде без пересборки образа.
TEXTBOOK_TARGET_CHARS = _env_int("CHUNKER_TEXTBOOK_TARGET_CHARS", 1500)
TEXTBOOK_MAX_CHARS = _env_int("CHUNKER_TEXTBOOK_MAX_CHARS", 2000)

# Пределы упаковки на время одного вызова create_llm_chunks (метод llm пакует
# сегменты модели крупнее, чем правила). contextvars: безопасно при
# параллельных запросах в asyncio.to_thread.
_PACK_LIMITS: contextvars.ContextVar = contextvars.ContextVar("chunker_pack_limits", default=None)


def _target_chars() -> int:
    limits = _PACK_LIMITS.get()
    return limits[0] if limits else TEXTBOOK_TARGET_CHARS


def _max_chars() -> int:
    limits = _PACK_LIMITS.get()
    return limits[1] if limits else TEXTBOOK_MAX_CHARS
# Список (вводный абзац + пункты) не дробится, даже если длиннее
# TEXTBOOK_MAX_CHARS; сверх жёсткого потолка режем только между пунктами.
TEXTBOOK_LIST_HARD_CAP = _env_int("CHUNKER_TEXTBOOK_LIST_HARD_CAP", 6000)
# Промежутки текста вне нумерованных пунктов (проза учебника в normative)
# короче этого порога считаем заголовками/мусором и пропускаем.
NORMATIVE_GAP_MIN_CHARS = 200
VALID_DOCUMENT_KINDS = frozenset({"normative", "textbook", "encyclopedia"})
# Prose kinds: semantic paragraphs, no legal article surgery, coverage guard.
PROSE_KINDS = frozenset({"textbook", "encyclopedia"})
# Методы чанкинга, выбираемые в UI (mirrors dataset_api/pipeline_constants.py
# CHUNKING_METHODS и gena_frontend ChunkingMethod):
#   rules    — заголовки + регулярные правила (текущий путь, всегда доступен);
#   layout   — PDF по шрифтам/заливкам (pdfplumber), фолбэк на rules;
#   semantic — rules/layout + границы чанков по эмбеддингам;
#   llm      — сегментация абзацев моделью (типы блоков и границы).
CHUNKING_METHODS = ("rules", "layout", "semantic", "llm")
DEFAULT_CHUNKING_METHOD = "rules"
# Нормативка режется по статьям/пунктам — там только rules.
CHUNKING_METHODS_BY_KIND = {
    "normative": ("rules",),
    "textbook": CHUNKING_METHODS,
    "encyclopedia": CHUNKING_METHODS,
}


class ChunkingMethodUnavailable(ValueError):
    """Запрошенный метод чанкинга не поддержан для вида документа или не сконфигурирован.

    ValueError, чтобы /chunk/ отдал 400 с текстом; воркер по подстроке
    «chunking method» ставит message_code chunker_method_unavailable.
    """


def normalize_chunking_method(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in CHUNKING_METHODS else DEFAULT_CHUNKING_METHOD


def chunking_method_availability() -> Dict[str, Dict[str, Any]]:
    """{method: {"available": bool, "reason": str | None}} по текущей конфигурации."""
    import importlib.util

    result: Dict[str, Dict[str, Any]] = {"rules": {"available": True, "reason": None}}
    if importlib.util.find_spec("pdfplumber") is None:
        result["layout"] = {"available": False, "reason": "pdfplumber is not installed"}
    else:
        result["layout"] = {"available": True, "reason": None}
    if os.getenv("CHUNKER_EMBEDDING_URL", "").strip():
        result["semantic"] = {"available": True, "reason": None}
    else:
        result["semantic"] = {"available": False, "reason": "CHUNKER_EMBEDDING_URL is not set"}
    if os.getenv("LLM_URL_MODEL", "").strip() or os.getenv("MODEL_ENDPOINTS", "").strip():
        result["llm"] = {"available": True, "reason": None}
    else:
        result["llm"] = {"available": False, "reason": "LLM_URL_MODEL / MODEL_ENDPOINTS are not set"}
    return result


def check_chunking_method(method: str, kind: str) -> None:
    """Бросить ChunkingMethodUnavailable, если метод нельзя применить к виду документа."""
    if method not in CHUNKING_METHODS_BY_KIND.get(kind, ("rules",)):
        raise ChunkingMethodUnavailable(
            f"chunking method '{method}' is not supported for document_kind '{kind}'"
        )
    avail = chunking_method_availability().get(method) or {"available": False, "reason": "unknown method"}
    if not avail["available"]:
        raise ChunkingMethodUnavailable(
            f"chunking method '{method}' is not configured: {avail['reason']}"
        )
# Желаемая минимальная длина чанка прозы: более короткие фрагменты
# приклеиваются к соседним фрагментам того же узла, пока влезают в
# TEXTBOOK_MAX_CHARS. Ничего не теряется — только укрупняется.
PROSE_MIN_CHUNK_CHARS = {
    "textbook": _env_int("CHUNKER_PROSE_MIN_CHARS_TEXTBOOK", 400),
    "encyclopedia": _env_int("CHUNKER_PROSE_MIN_CHARS_ENCYCLOPEDIA", 200),
}
# Огрызок вёрстки: короткий фрагмент, не заканчивающийся знаком конца
# предложения (подписи к таблицам, оборванные строки). По такому тексту
# вопрос не задать, а приклеить его не к чему.
PROSE_DEBRIS_MAX_CHARS = _env_int("CHUNKER_PROSE_DEBRIS_MAX_CHARS", 120)
# Словарная статья короче этого — не статья, а обрывок («Термин.»).
GLOSSARY_ENTRY_MIN_CHARS = 40
# Узлы дерева с таким block_type (врезка, подпись, словарь) чанкуются особо.
_NODE_BLOCK_TYPES = frozenset({"inset", "caption", "glossary", "glossary_entry"})

# Перенос слова, разорванный номером страницы или колонтитулом: к моменту
# сборки чанков мусорные строки уже вырезаны, но половинки слова остались в
# разных абзацах, поэтому склейку приходится повторять здесь.
_PROSE_HYPHEN_JOIN_RE = re.compile(
    r'([А-Яа-яЁёA-Za-z])[ \t]*[-\u2010\u2011][ \t]*\n\s*([а-яёa-z])'
)
_PROSE_SENTENCE_END_RE = re.compile(r'[.!?…:;]\s*["»)\]]?$')
_PROSE_CONTINUATION_RE = re.compile(r'^[«"(]?[а-яёa-z]')


# Декоративный заголовок с разрядкой («П О Р Т Р Е Т  Н А  Ф О Н Е  Э П О Х И»):
# пробелы между буквами уже схлопнуты, восстановить слова нельзя — выкидываем.
_LETTER_SPACED_HEADING_RE = re.compile(r'^(?:\S\s+){3,}\S$')
# Заглушка нераспознанного названия параграфа: «§ 37. ?».
_HEADING_STUB_RE = re.compile(r'\.\s*\?(?=\s|$)')
# Отбивка оглавления: «§ 37. Россия сегодня . . . . . . 390».
_HEADING_DOT_LEADER_RE = re.compile(r'(?:\s*\.){3,}\s*\d*$')
_HEADING_MAX_CHARS = 200
# Сокращение в конце заголовка — точка часть слова: «СССР в 1945—1991 гг.»,
# «Россия в XIX в.», «до н. э.»; её не снимаем, в отличие от точки после
# run-in подзаголовка («1 Влияние Победы.»).
_HEADING_ABBR_END_RE = re.compile(r'(?:^|[\s\d—–-])(?:гг|г|вв|в|н\.\s?э|т\.\s?[дп]|др)\.$', re.IGNORECASE)


def _clean_prose_heading(title: str) -> str:
    """Привести заголовок из PDF к виду, пригодному для строки контекста чанка."""
    s = strip_table_marks(re.sub(r'\s+', ' ', (title or "")).strip())
    if not s or _LETTER_SPACED_HEADING_RE.match(s):
        return ""
    s = _HEADING_DOT_LEADER_RE.sub('', s)
    s = _HEADING_STUB_RE.sub('.', s).strip()
    keep_dot = bool(_HEADING_ABBR_END_RE.search(s))
    s = s.strip(' .')
    if keep_dot and s:
        s += "."
    if not s or s.isdigit():
        return ""
    return s[:_HEADING_MAX_CHARS]


def _prose_context_line(breadcrumb_titles: List[str], title: str) -> str:
    """Собрать «Глава … > § …» для подстановки в текст чанка."""
    parts: List[str] = []
    for raw in [*breadcrumb_titles, title]:
        clean = _clean_prose_heading(raw)
        if clean and (not parts or parts[-1] != clean):
            parts.append(clean)
    return " > ".join(parts)


def _normalize_prose_content(content: str) -> str:
    """Склеить слова и абзацы, разорванные вёрсткой PDF (переносы, разрывы страниц)."""
    if not content:
        return ""
    # Ярлык врезки убираем до склейки переносов: он может стоять между
    # половинками перенесённого слова («универ- ИНТЕРЕСНО» / «ситет»).
    text = _PROSE_HYPHEN_JOIN_RE.sub(r'\1\2', strip_inline_labels(content))
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    merged: List[str] = []
    for para in paragraphs:
        if merged and _PROSE_CONTINUATION_RE.match(para):
            prev = merged[-1]
            # Абзац со строчной буквы — продолжение предыдущего: разрыв страницы
            # или врезка разорвали его. Точка на конце предыдущего куска может
            # быть сокращением («в 2022 г.» / «утверждены…») — спрашиваем у
            # сплиттера предложений, есть ли тут граница.
            if not _PROSE_SENTENCE_END_RE.search(prev):
                merged[-1] = f"{prev} {para}"
                continue
            prev_sentences = split_sentences(prev)
            tail = prev_sentences[-1] if prev_sentences else prev
            probe = split_sentences(f"{tail} {para[:80]}")
            if probe and len(probe[0]) > len(tail):
                # Граница не является концом предложения (точка — сокращение).
                merged[-1] = f"{prev} {para}"
                continue
        merged.append(para)
    return "\n\n".join(merged)


def _unit(
    text: str,
    block_type: str = "body",
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> Dict[str, Any]:
    """Единица прозы до упаковки в чанк: текст + тип блока + страницы."""
    return {
        "text": text,
        "block_type": block_type,
        "page_start": page_start,
        "page_end": page_end,
    }


def _stats_add(stats: Optional[Dict[str, Any]], bucket: str, key: str, chars: int, count: int = 1) -> None:
    """Накопить счётчик «сколько блоков/символов» в stats[bucket][key]."""
    if stats is None:
        return
    entry = stats.setdefault(bucket, {}).setdefault(key, {"count": 0, "chars": 0})
    entry["count"] += count
    entry["chars"] += chars


_MERGEABLE_BLOCK_TYPES = frozenset({"body", "list"})


def _merge_short_prose_units(
    units: List[Dict[str, Any]],
    min_chars: int,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Укрупнить короткие фрагменты прозы и выбросить огрызки вёрстки.

    Склеиваются только body/list-единицы (врезка или словарная статья —
    самостоятельные чанки). Огрызок — короткий фрагмент без знака конца
    предложения: подпись к таблице, оборванная строка.
    """
    if not units:
        return units
    merged: List[Dict[str, Any]] = []
    for unit in units:
        prev = merged[-1] if merged else None
        if (
            min_chars > 0
            and prev is not None
            and prev["block_type"] in _MERGEABLE_BLOCK_TYPES
            and unit["block_type"] in _MERGEABLE_BLOCK_TYPES
            and min(len(unit["text"]), len(prev["text"])) < min_chars
        ):
            candidate = f"{prev['text']}\n\n{unit['text']}"
            if len(candidate) <= _max_chars():
                merged[-1] = _unit(
                    candidate,
                    "list" if "list" in (prev["block_type"], unit["block_type"]) else "body",
                    prev.get("page_start") if prev.get("page_start") is not None else unit.get("page_start"),
                    unit.get("page_end") if unit.get("page_end") is not None else prev.get("page_end"),
                )
                if prev.get("topic_title") or unit.get("topic_title"):
                    merged[-1]["topic_title"] = prev.get("topic_title") or unit.get("topic_title")
                continue
        merged.append(unit)
    kept: List[Dict[str, Any]] = []
    for unit in merged:
        text = unit["text"]
        if (
            unit["block_type"] in _MERGEABLE_BLOCK_TYPES
            and len(text) < PROSE_DEBRIS_MAX_CHARS
            and not _PROSE_SENTENCE_END_RE.search(text)
        ):
            logger.debug("Dropped prose layout debris: %r", text[:80])
            _stats_add(stats, "dropped_blocks", "debris", len(text))
            continue
        kept.append(unit)
    return kept


def _merge_short_prose_parts(parts: List[str], min_chars: int) -> List[str]:
    """Строковая обёртка над _merge_short_prose_units (обратная совместимость)."""
    if not parts:
        return parts
    return [u["text"] for u in _merge_short_prose_units([_unit(p) for p in parts], min_chars)]


def _is_list_block(paragraph: str) -> bool:
    """Абзац — пункт списка или несколько пунктов, разделённых переносами строк."""
    if is_list_item(paragraph):
        return True
    lines = [l for l in paragraph.splitlines() if l.strip()]
    return len(lines) >= 2 and sum(1 for l in lines if is_list_item(l)) >= 2


def _split_list_by_hard_cap(text: str) -> List[str]:
    """Список длиннее жёсткого потолка режем только между пунктами.

    Вводная строка (если первая строка — не пункт) повторяется в каждой части,
    чтобы пункты не теряли заголовок списка.
    """
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return [text]
    intro = "" if is_list_item(lines[0]) else lines[0]
    items = lines[1:] if intro else lines
    parts: List[str] = []
    buf: List[str] = []
    buf_len = len(intro)
    for line in items:
        if buf and buf_len + len(line) + 1 > TEXTBOOK_LIST_HARD_CAP:
            parts.append("\n".join(([intro] if intro else []) + buf))
            buf, buf_len = [], len(intro)
        buf.append(line)
        buf_len += len(line) + 1
    if buf:
        parts.append("\n".join(([intro] if intro else []) + buf))
    return parts or [text]


def _split_long_paragraph(text: str) -> List[Tuple[str, str]]:
    """Абзац → части (текст, тип) не длиннее потолка.

    Список не дробится до TEXTBOOK_LIST_HARD_CAP (потом — между пунктами);
    обычный абзац режется по предложениям с учётом сокращений («г.», «т.к.»,
    инициалы), и только затем — жёстко по длине.
    """
    if _is_list_block(text):
        if len(text) <= TEXTBOOK_LIST_HARD_CAP:
            return [(text, "list")]
        return [(part, "list") for part in _split_list_by_hard_cap(text)]
    if len(text) <= _max_chars():
        return [(text, "body")]
    # Части выравниваются по длине (2300 → 1150 + 1150, а не 2000 + 300):
    # короткий хвост абзаца не к чему приклеить — сосед уже под потолком.
    n_parts = -(-len(text) // _max_chars())
    balanced = -(-len(text) // n_parts)
    parts: List[str] = []
    buf = ""
    for sent in split_sentences(text):
        if not buf:
            buf = sent
        elif len(buf) < balanced and len(buf) + 1 + len(sent) <= _max_chars():
            buf = f"{buf} {sent}"
        else:
            parts.append(buf)
            buf = sent
    if buf:
        parts.append(buf)
    hard: List[str] = []
    for part in parts:
        if len(part) <= _max_chars():
            hard.append(part)
        else:
            for i in range(0, len(part), _max_chars()):
                hard.append(part[i:i + _max_chars()])
    return [(part, "body") for part in (hard or [text])]


def _group_prose_units(paragraphs: List[str]) -> List[Tuple[str, str]]:
    """Абзацы → единицы (текст, тип): списки и ряды определений атомарны.

    Список 1,2,3 / а,б,в забирает к себе вводный абзац (короткий или с
    двоеточием на конце) и не дробится. Два и более подряд абзаца вида
    «Термин — определение» — тоже одна единица (список понятий).
    """
    groups: List[Tuple[str, str]] = []
    i, n = 0, len(paragraphs)
    while i < n:
        para = paragraphs[i]
        is_def_run = (
            is_definition_paragraph(para)
            and i + 1 < n
            and is_definition_paragraph(paragraphs[i + 1])
        )
        if _is_list_block(para) or is_def_run:
            items: List[str] = []
            j = i
            probe = is_definition_paragraph if is_def_run else _is_list_block
            while j < n and probe(paragraphs[j]):
                items.append(paragraphs[j])
                j += 1
            intro = None
            if not is_def_run and groups and groups[-1][1] == "body" and is_list_intro(groups[-1][0]):
                intro = groups.pop()[0]
            text = "\n\n".join(([intro] if intro else []) + items)
            if len(text) <= TEXTBOOK_LIST_HARD_CAP:
                groups.append((text, "list"))
            else:
                groups.extend((part, "list") for part in _split_list_by_hard_cap(text))
            i = j
            continue
        groups.extend(_split_long_paragraph(para))
        i += 1
    return groups


def _para_head(text: str) -> str:
    """Ключ абзаца для сопоставления страниц: первые 40 символов без пробелов."""
    return re.sub(r'\s+', '', (text or ""))[:40]


def _assign_unit_pages(units: List[Dict[str, Any]], para_pages: List[Dict[str, Any]]) -> None:
    """Страницы единицы = страницы её абзацев (а не всего узла).

    ``para_pages`` — список {"head", "page_start", "page_end"} по абзацам узла в
    порядке документа (его ведёт layout-извлечение). Единица без найденного
    абзаца (хвост длинного абзаца, разрезанного по предложениям) продолжает
    страницу предыдущей единицы; совсем без совпадений — остаётся диапазон узла.
    """
    cursor = 0
    last_end: Optional[int] = None
    for u in units:
        if u.get("page_start") is not None:
            continue
        key = re.sub(r'\s+', '', u.get("text") or "")
        found: List[Dict[str, Any]] = []
        j = cursor
        while j < len(para_pages):
            head = para_pages[j].get("head") or ""
            if head and head in key:
                found.append(para_pages[j])
                cursor = j + 1
                j += 1
                continue
            if found:
                break
            j += 1
        if found:
            starts = [f["page_start"] for f in found if f.get("page_start") is not None]
            ends = [f["page_end"] for f in found if f.get("page_end") is not None]
            if starts:
                u["page_start"] = min(starts)
                u["page_end"] = max(ends or starts)
                last_end = u["page_end"]
        elif last_end is not None:
            u["page_start"] = u["page_end"] = last_end


def _split_prose_units(
    content: str,
    prose_floor: int,
    stats: Optional[Dict[str, Any]] = None,
    node_block_type: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    segmenter: Optional[Any] = None,
    para_pages: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Содержимое узла прозы → единицы для чанков.

    1. Абзацы-задания («Выясните…», «⬤ Узнайте…»), оглавление, выходные
       данные и списки сокращений выбрасываются (со счётчиком в stats).
    2. Словарь (node_block_type=glossary): 1 статья = 1 единица.
    3. Врезка/подпись: одна единица (по абзацам, если длиннее потолка).
    4. Основной текст: списки атомарны, длинные абзацы режутся по
       предложениям, абзацы пакуются до _target_chars(), короткие
       единицы укрупняются до prose_floor.
    """
    if not content or not content.strip():
        return []
    content = _normalize_prose_content(content)
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', content) if p.strip()]
    if not paragraphs:
        return []

    is_glossary = node_block_type in ("glossary", "glossary_entry")
    kept: List[str] = []
    # После рубрики «Дополнительные материалы» внутри текста идёт список
    # фильмов/ссылок: его пункты выбрасываются до первого абзаца-не-пункта.
    after_apparatus = False
    for para in paragraphs:
        if after_apparatus and _is_list_block(para):
            _stats_add(stats, "dropped_blocks", "apparatus", len(para))
            continue
        after_apparatus = False
        dropped_as = None if is_glossary else classify_dropped_paragraph(para)
        if dropped_as:
            logger.info("Dropped %s paragraph: %r", dropped_as, para[:80])
            _stats_add(stats, "dropped_blocks", dropped_as, len(para))
            after_apparatus = dropped_as == "apparatus"
            continue
        if not is_glossary:
            # Строки таблиц/схем и одиночные маркеры — графика, не текст.
            para, cut = strip_table_lines(para)
            if cut:
                _stats_add(stats, "dropped_blocks", "figure_text", cut)
                if not para:
                    continue
            # Задание, вклеенное строкой внутрь абзаца («⬤ Выясните…»).
            para, cut2 = strip_exercise_lines(para)
            if cut2:
                _stats_add(stats, "dropped_blocks", "exercise", cut2)
                if not para:
                    continue
            # Рубрика «Дополнительные материалы» строкой в конце абзаца.
            para, cut3 = strip_apparatus_lines(para)
            if cut3:
                _stats_add(stats, "dropped_blocks", "apparatus", cut3)
                after_apparatus = True
                if not para:
                    continue
            if cut or cut2:
                # Вырезанные строки могли стоять между половинками перенесённого
                # слова («правитель-» / «ства») — склеиваем ещё раз.
                para = _PROSE_HYPHEN_JOIN_RE.sub(r'\1\2', para)
        kept.append(para)
    if not kept:
        return []

    def _with_pages(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if para_pages:
            _assign_unit_pages(units, para_pages)
        for u in units:
            if u.get("page_start") is None:
                u["page_start"] = page_start
            if u.get("page_end") is None:
                u["page_end"] = page_end
        return units

    if is_glossary:
        units: List[Dict[str, Any]] = []
        for para in kept:
            if len(para) < GLOSSARY_ENTRY_MIN_CHARS:
                _stats_add(stats, "dropped_blocks", "debris", len(para))
                continue
            units.extend(_unit(part, "glossary_entry") for part, _t in _split_long_paragraph(para))
        return _with_pages(units)

    if node_block_type in ("inset", "caption"):
        # Врезка — один чанк, пока не длиннее жёсткого потолка списков:
        # документ/воспоминания, разрезанные по абзацам, теряют контекст.
        text = "\n\n".join(kept)
        if len(text) <= max(TEXTBOOK_LIST_HARD_CAP, _max_chars()):
            return _with_pages([_unit(text, node_block_type)])
        packed_parts: List[str] = []
        buf = ""
        for part, _t in (piece for para in kept for piece in _split_long_paragraph(para)):
            if not buf:
                buf = part
            elif len(buf) + 2 + len(part) <= _max_chars():
                buf = f"{buf}\n\n{part}"
            else:
                packed_parts.append(buf)
                buf = part
        if buf:
            packed_parts.append(buf)
        return _with_pages([_unit(part, node_block_type) for part in packed_parts])

    groups = _group_prose_units(kept)
    # Семантические границы (метод semantic / llm): индексы единиц, с которых новый чанк,
    # и (llm) названия подтем от модели для этих границ.
    forced: set = set()
    topic_titles: Dict[int, str] = {}
    if segmenter is not None and len(groups) > 1:
        try:
            forced = set(segmenter.boundaries([g[0] for g in groups]))
            if hasattr(segmenter, "titles"):
                topic_titles = dict(segmenter.titles([g[0] for g in groups]) or {})
        except Exception as exc:  # noqa: BLE001 — эмбеддинги упали → пакуем по правилам
            logger.warning("Semantic segmenter failed, packing by rules: %s", exc)
            _stats_add(stats, "semantic_errors", type(exc).__name__, 0)

    packed: List[Dict[str, Any]] = []
    buf = ""
    topic = ""

    def _flush(text: str, utype: str = "body") -> None:
        u = _unit(text, utype)
        if topic:
            u["topic_title"] = topic
        packed.append(u)

    for gi, (text, utype) in enumerate(groups):
        if gi in forced and buf:
            _flush(buf)
            buf = ""
        if gi in topic_titles:
            topic = topic_titles[gi]
        elif gi in forced:
            topic = ""
        if utype == "list":
            if buf and len(buf) + 2 + len(text) <= _target_chars():
                # Короткий список уезжает вместе с предшествующим контекстом.
                buf = f"{buf}\n\n{text}"
                continue
            if buf:
                _flush(buf)
                buf = ""
            _flush(text, "list")
            continue
        if not buf:
            buf = text
        elif len(buf) + 2 + len(text) <= _target_chars():
            buf = f"{buf}\n\n{text}"
        else:
            _flush(buf)
            buf = text
    if buf:
        _flush(buf)
    return _with_pages(_merge_short_prose_units(packed, prose_floor, stats))


def _subtree_chars(node: Dict[str, Any]) -> int:
    """Символы content поддерева (как в подсчёте покрытия, без заголовков)."""
    total = len((node.get("content") or "").strip())
    for ch in node.get("children") or []:
        if isinstance(ch, dict):
            total += _subtree_chars(ch)
    return total


def _mark_subtree(node: Dict[str, Any], block_type: str) -> None:
    # Словарь на заливке layout отдаёт врезками; внутри словаря это статьи,
    # а не врезка-один-чанк.
    if not node.get("block_type") or (block_type == "glossary" and node.get("block_type") == "inset"):
        node["block_type"] = block_type
    for ch in node.get("children") or []:
        if isinstance(ch, dict):
            _mark_subtree(ch, block_type)


def _drop_apparatus_sections(tree: Dict[str, Any], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Удалить из дерева учебника разделы-аппарат и пометить словари.

    «Вопросы и задания», «Проверяем себя», «Практикум», «Работаем с …» →
    exercise; «Содержание», «Введение», «Список сокращений», «Условные
    обозначения», «Ключевые понятия» → front_matter; «Дополнительные
    материалы» (фильмы, QR-ссылки) → apparatus — поддеревья выбрасываются
    целиком (гейт остаётся вторым рубежом). Врезка/подпись, у которой первая
    строка — рубрика аппарата, выбрасывается так же; врезка со смешанными
    колонками (block_type=inset_mixed от layout) — со счётчиком inset_mixed.
    «Словарь терминов», «Основные понятия» → узел помечается
    block_type=glossary (врезки внутри — тоже): 1 статья = 1 чанк.
    Правило работает по заголовкам, поэтому одинаково для PDF и DOCX.

    Блочная рубрика («Ключевые понятия» первой в параграфе) выбрасывается
    только со списком терминов под ней: изложение, попавшее в её узел из-за
    нераспознанного следующего заголовка, и её дочерние узлы поднимаются в
    параграф (key_terms в dropped_blocks).
    """

    def _hoist_rubric(node: Dict[str, Any], ch: Dict[str, Any]) -> List[Dict[str, Any]]:
        paras = [p for p in re.split(r'\n\s*\n', ch.get("content") or "") if p.strip()]
        terms = 0
        dropped = 0
        # Список терминов: первый абзац всегда, следующие — пока это пункты списка.
        while terms < len(paras) and (terms == 0 or paras[terms].lstrip().startswith(("•", "-", "–", "—"))):
            dropped += len(paras[terms].strip())
            terms += 1
        _stats_add(stats, "dropped_blocks", BLOCK_RUBRIC_KIND, dropped)
        rest = "\n\n".join(p.strip() for p in paras[terms:])
        if rest:
            node["content"] = f"{node['content']}\n\n{rest}" if node.get("content") else rest
        return [c for c in (ch.get("children") or []) if isinstance(c, dict)]

    def _walk(node: Dict[str, Any]) -> None:
        kept: List[Dict[str, Any]] = []
        queue = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        while queue:
            ch = queue.pop(0)
            if (
                not kept and ch.get("block_type") not in DROP_HEADING_KINDS
                and block_rubric_kind(ch.get("title", ""))
            ):
                # Рубрика до первого подраздела: изложение под ней — текст
                # самого параграфа. Позже в узле — прежнее правило (выброс).
                queue = _hoist_rubric(node, ch) + queue
                continue
            kind = ch.get("block_type")
            if kind not in DROP_HEADING_KINDS and kind != "inset_mixed":
                kind = heading_kind(ch.get("title", ""))
                if kind not in DROP_HEADING_KINDS and ch.get("block_type") in ("inset", "caption"):
                    first = next((l for l in (ch.get("content") or "").split("\n") if l.strip()), "")
                    if is_apparatus_line(first):
                        kind = "apparatus"
            if kind in DROP_HEADING_KINDS or kind == "inset_mixed":
                logger.info(
                    "Dropped textbook apparatus section %r (%s)",
                    (ch.get("title") or "")[:80], kind,
                )
                _stats_add(stats, "dropped_blocks", kind, _subtree_chars(ch))
                continue
            if kind == "glossary":
                _mark_subtree(ch, "glossary")
            _walk(ch)
            kept.append(ch)
        node["children"] = kept

    if heading_kind(tree.get("title", "")) == "glossary":
        _mark_subtree(tree, "glossary")
    _walk(tree)
    return tree


def _title_key(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or "").replace("ё", "е").replace("Ё", "Е")).strip(' .:!?—–-').lower()


def _strip_inset_title_lines(node: Dict[str, Any]) -> None:
    """Убрать из текста врезки первую строку, если она повторяет title узла.

    Layout отдаёт врезку с её рубрикой в первой строке («РОССИЯ МИР ⏎ • 1946 г.
    — …») и та же рубрика стоит в title: в чанке она вышла бы дважды. Если
    title — лишь часть строки («Вознесенский Николай Алексеевич (1903—1950)»
    в строке «ПОРТРЕТ НА ФОНЕ ЭПОХИ Вознесенский …»), строку не трогаем.
    """
    for ch in node.get("children") or []:
        if not isinstance(ch, dict):
            continue
        if ch.get("block_type") in ("inset", "caption") and ch.get("content"):
            content = ch["content"].lstrip()
            first, sep, rest = content.partition("\n")
            key = _title_key(ch.get("title", ""))
            if key and _title_key(first) == key and rest.strip():
                ch["content"] = rest.lstrip("\n")
        _strip_inset_title_lines(ch)


# Остаток врезки после отрезания заданий короче этого — обрывок задания с
# прошлой страницы («ми.», «ния 1979 г. Данные в диаграмме отразите…»).
INSET_TASK_REMAINDER_MIN_CHARS = 160
_INSET_OPEN_END_RE = re.compile(r'[.!?…:»"]\s*$')
_GENERIC_INSET_TITLES = frozenset({"", "врезка"})


def _fix_inset_nodes(node: Dict[str, Any], stats: Optional[Dict[str, Any]] = None) -> None:
    """Почистить и склеить врезки дерева учебника.

    0. Продолжение списка «Дополнительных материалов» (фильмы, статьи)
       выбрасывается (счётчик apparatus).
    1. Хвост врезки с номерными заданиями («4. О чём свидетельствует…?»)
       отрезается (счётчик exercise); если осталось меньше
       INSET_TASK_REMAINDER_MIN_CHARS — выбрасывается вся врезка.
    2. Врезка, разрезанная стыком страниц, склеивается: соседние inset-узлы
       одного родителя, если первый не кончается концом предложения, а
       второй начинается со строчной буквы.
       Второй должен быть безымянным («Врезка»); сумма — не больше
       TEXTBOOK_LIST_HARD_CAP. Подписи (caption) не склеиваются.
    """
    children = [ch for ch in (node.get("children") or []) if isinstance(ch, dict)]
    cleaned: List[Dict[str, Any]] = []
    for ch in children:
        if ch.get("block_type") == "inset" and ch.get("content"):
            if is_materials_list(ch["content"]):
                # Хвост списка «Дополнительных материалов» отдельной врезкой.
                _stats_add(stats, "dropped_blocks", "apparatus", len(ch["content"]))
                continue
            text, cut = strip_numbered_tasks(ch["content"])
            if cut:
                if len(text.strip()) < INSET_TASK_REMAINDER_MIN_CHARS:
                    _stats_add(stats, "dropped_blocks", "exercise", len(ch["content"]))
                    continue
                _stats_add(stats, "dropped_blocks", "exercise", cut)
                ch["content"] = text
        cleaned.append(ch)
    merged: List[Dict[str, Any]] = []
    for ch in cleaned:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.get("block_type") == "inset" and ch.get("block_type") == "inset"
            and not prev.get("children") and not ch.get("children")
            and (ch.get("title") or "").strip().lower() in _GENERIC_INSET_TITLES
            and prev.get("content") and ch.get("content")
        ):
            first, second = prev["content"].rstrip(), ch["content"].lstrip()
            # Только продолжение со строчной: по одному «первый не кончается
            # точкой» склеивались хроники «• 1960 г. — …» с подписью к фото на
            # следующей странице (с. 60–61, 294–295, 378–379 истории).
            if (
                second[:1].islower()
                and not _INSET_OPEN_END_RE.search(first)
                and len(first) + 1 + len(second) <= TEXTBOOK_LIST_HARD_CAP
            ):
                joined = first[:-1] + second if first.endswith("-") else f"{first} {second}"
                prev["content"] = joined
                if ch.get("page_end") is not None:
                    prev["page_end"] = ch["page_end"]
                _stats_add(stats, "inset_merges", "page_break", len(second))
                continue
        merged.append(ch)
    if children:
        node["children"] = merged
    for ch in merged:
        _fix_inset_nodes(ch, stats)


_INTRO_SENTENCE_RE = re.compile(r'[а-яёa-z][.!?…]["»)]?\s*\n')


def _push_short_intro_down(node: Dict[str, Any]) -> None:
    """Короткий текст узла с подразделами — в начало первого подраздела.

    Эпиграф body-кеглем сразу под названием параграфа («Право есть искусство
    добра и справедливости.» / «Публий Ювентий Цельс») остаётся один в узле §:
    он короче PROSE_DEBRIS_MAX_CHARS и кончается подписью автора, без точки,
    поэтому упаковщик выбросил бы его как огрызок. Переносим его в начало
    первого раздела (там он склеится с первым абзацем). Трогаем только такой
    текст, который иначе был бы выброшен; врезки и подписи перед первым
    разделом остаются на месте.
    """
    children = [ch for ch in (node.get("children") or []) if isinstance(ch, dict)]
    content = (node.get("content") or "").strip()
    # Первый раздел; врезки и подписи перед ним — отдельные чанки, их пропускаем.
    first = next((ch for ch in children if not ch.get("block_type")), None)
    if (
        first is not None and content
        and len(content) < PROSE_DEBRIS_MAX_CHARS
        and not _PROSE_SENTENCE_END_RE.search(content)
        # Законченное предложение внутри (цитата), а за ним подпись автора;
        # обрывок вроде «? К каким результатам привели…» не переносим.
        and _INTRO_SENTENCE_RE.search(content)
        and (first.get("content") or "").strip()
    ):
        first["content"] = f"{content}\n\n{first['content'].lstrip()}"
        if isinstance(node.get("para_pages"), list):
            first["para_pages"] = node["para_pages"] + list(first.get("para_pages") or [])
        if node.get("page_start") is not None and (
            first.get("page_start") is None or node["page_start"] < first["page_start"]
        ):
            first["page_start"] = node["page_start"]
        node["content"] = ""
        node.pop("para_pages", None)
    for ch in children:
        _push_short_intro_down(ch)


def _bootstrap_env() -> None:
    """config/llm_defaults.env (репо или образ), затем .env — те же дефолты, что в agent_api."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    base = Path(__file__).resolve().parent
    for path in (base.parent / "config" / "llm_defaults.env", base / "config" / "llm_defaults.env"):
        if path.is_file():
            load_dotenv(path, override=False)
            break
    load_dotenv(override=True)


_bootstrap_env()


def extract_all_titles(node: Dict[str, Any], titles: List[str] = None) -> List[str]:
    """
    Извлекает все заголовки (title) из дерева документа.
    
    Args:
        node: Узел дерева документа
        titles: Список для накопления заголовков (используется рекурсивно)
    
    Returns:
        Список всех заголовков документа
    """
    if titles is None:
        titles = []
    if not isinstance(node, dict):
        return titles
    
    title = node.get("title", "").strip()
    # Ярлыки врезок и подписей («Это интересно») — не заголовки документа.
    if title and node.get("block_type") not in ("inset", "caption", "glossary_entry"):
        titles.append(title)
    
    # Рекурсивно обрабатываем детей
    for child in node.get("children", []):
        extract_all_titles(child, titles)
    
    return titles


def identify_document_type(titles: List[str]) -> Dict[str, Any]:
    """
    Определяет тип документа на основе заголовков через LLM.
    
    Args:
        titles: Список заголовков документа
    
    Returns:
        Словарь с информацией о типе документа
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower().strip()
        llm_model_name = os.getenv("LLM_MODEL_NAME", "")
        llm_url = os.getenv("LLM_URL_MODEL", "")
        llm_api_key = os.getenv("LLM_API_KEY", "")

        gigachat_credentials = os.getenv("GIGACHAT_CREDENTIALS", "")
        gigachat_scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        gigachat_model = os.getenv("GIGACHAT_MODEL", "GigaChat-2")

        yandex_api_key = os.getenv("YANDEX_CLOUD_API_KEY", "")
        yandex_folder = os.getenv("YANDEX_CLOUD_FOLDER", "")
        yandex_model = os.getenv("YANDEX_CLOUD_MODEL", "yandexgpt-5-lite/latest")

        has_openai = llm_model_name and llm_url
        has_gigachat = gigachat_credentials
        has_yandex = yandex_api_key and yandex_folder

        if not has_openai and not has_gigachat and not has_yandex:
            logger.warning("LLM не настроен, пропускаем определение типа документа")
            return {
                "document_type": "unknown",
                "document_name": "Название не определено",
                "confidence": 0.0,
                "description": "Тип документа не определен (LLM не настроен)",
                "titles_count": len(titles),
                "key_indicators": []
            }
        
        # Формируем промпт
        titles_text = "\n".join([f"- {title}" for title in titles[:50]])  # Берем первые 50 заголовков
        if len(titles) > 50:
            titles_text += f"\n... и еще {len(titles) - 50} заголовков"
        
        from langchain_core.output_parsers import JsonOutputParser
        from pydantic import BaseModel, Field
        
        class DocumentTypeInfo(BaseModel):
            document_type: str = Field(description="Тип документа (например: Конституция, Кодекс, Федеральный закон, ГОСТ, Постановление и т.д.)")
            document_name: str = Field(description="Полное название документа (например: 'Семейный кодекс Российской Федерации', 'Конституция Российской Федерации')")
            confidence: float = Field(description="Уверенность в определении от 0.0 до 1.0", ge=0.0, le=1.0)
            description: str = Field(description="Краткое описание документа (1-2 предложения)")
            key_indicators: list[str] = Field(description="Ключевые признаки, по которым определен тип", default_factory=list)
        
        parser = JsonOutputParser(pydantic_object=DocumentTypeInfo)
        
        system_prompt = """Ты эксперт по анализу юридических и нормативных документов. 
Проанализируй заголовки документа и определи его тип и полное название.

Верни ответ ТОЛЬКО в формате валидного JSON без дополнительного текста:
{
    "document_type": "тип документа (например: Кодекс, Конституция, Федеральный закон)",
    "document_name": "полное название документа (например: 'Семейный кодекс Российской Федерации')",
    "confidence": 0.95,
    "description": "краткое описание документа (1-2 предложения)",
    "key_indicators": ["признак1", "признак2"]
}

ВАЖНО: 
- document_name должно быть полным официальным названием документа
- Если не можешь определить тип или название, укажи "unknown" с confidence 0.0
- Ответ должен быть валидным JSON без markdown разметки и дополнительного текста"""

        human_prompt = f"""Заголовки документа:

{titles_text}

Определи тип и полное официальное название этого документа."""

        if llm_provider == "gigachat" and has_gigachat:
            from langchain_gigachat import GigaChat
            llm = GigaChat(
                credentials=gigachat_credentials,
                scope=gigachat_scope,
                model=gigachat_model,
                temperature=0.0,
                max_tokens=256,
                timeout=15,
                verify_ssl_certs=GIGACHAT_VERIFY_SSL,
            )
        elif llm_provider == "yandex" and has_yandex:
            full_model = yandex_model if yandex_model.startswith("gpt://") else f"gpt://{yandex_folder}/{yandex_model}"
            llm = ChatOpenAI(
                model=full_model,
                openai_api_base="https://ai.api.cloud.yandex.net/v1",
                openai_api_key=yandex_api_key,
                temperature=0.0,
                max_retries=2,
                timeout=15,
                max_tokens=256,
                default_headers={"x-folder-id": yandex_folder},
            )
        else:
            llm = ChatOpenAI(
                model=llm_model_name,
                temperature=0.0,
                openai_api_base=llm_url,
                openai_api_key=llm_api_key,
                max_retries=2,
                timeout=15,
                max_tokens=256,
            )
        
        # Отправляем запрос с использованием парсера
        try:
            # Используем JsonOutputParser для более надежного парсинга
            from langchain_core.prompts import ChatPromptTemplate
            
            # Экранируем фигурные скобки во всех частях промпта (system_prompt содержит
            # пример JSON, human_prompt — заголовки), чтобы LangChain не интерпретировал их
            # как переменные шаблона. chain.invoke({}) не передаёт переменных.
            def _escape_braces(text: str) -> str:
                return text.replace("{", "{{").replace("}", "}}")

            format_instructions = _escape_braces(parser.get_format_instructions())

            prompt = ChatPromptTemplate.from_messages([
                ("system", _escape_braces(system_prompt) + "\n\n" + format_instructions),
                ("human", _escape_braces(human_prompt))
            ])
            
            chain = prompt | llm | parser
            result = chain.invoke({})
            
            # Преобразуем Pydantic модель в словарь
            if hasattr(result, 'model_dump'):
                result = result.model_dump()
            elif hasattr(result, 'dict'):
                result = result.dict()
            elif not isinstance(result, dict):
                raise TypeError(f"LLM вернул {type(result).__name__}, ожидался dict")
            
            result["titles_count"] = len(titles)
            
            logger.info(f"Определен тип документа: {result.get('document_type', 'unknown')}")
            logger.info(f"Название документа: {result.get('document_name', 'Название не определено')}")
            logger.info(f"Уверенность: {result.get('confidence', 0.0):.2f}")
            return result
            
        except Exception as parse_error:
            # Если парсер не сработал, пытаемся распарсить вручную
            logger.warning(f"JsonOutputParser не сработал, пытаемся парсить вручную: {parse_error}")
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt)
            ]
            
            response = llm.invoke(messages)
            response_text = (getattr(response, "content", None) or "").strip()
            if not response_text:
                raise ValueError("LLM вернул пустой ответ")
            
            # Парсим JSON ответ вручную
            import re
            
            # Логируем исходный ответ для отладки
            logger.debug(f"Ответ LLM (первые 500 символов): {response_text[:500]}")
            
            # Улучшенная очистка ответа
            # Убираем markdown код блоки
            if "```" in response_text:
                # Ищем JSON между ``` или ```json
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1).strip()
                else:
                    # Если не нашли между ```, убираем все до первого {
                    first_brace = response_text.find('{')
                    if first_brace != -1:
                        response_text = response_text[first_brace:]
                    # Убираем все после последнего }
                    last_brace = response_text.rfind('}')
                    if last_brace != -1:
                        response_text = response_text[:last_brace + 1]
            else:
                # Если нет markdown блоков, ищем JSON объект
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(0).strip()
            
            # Очищаем от возможных лишних символов в начале/конце
            response_text = response_text.strip()
            if not response_text.startswith('{'):
                # Ищем первую открывающую скобку
                first_brace = response_text.find('{')
                if first_brace != -1:
                    response_text = response_text[first_brace:]
            if not response_text.endswith('}'):
                # Ищем последнюю закрывающую скобку
                last_brace = response_text.rfind('}')
                if last_brace != -1:
                    response_text = response_text[:last_brace + 1]
            
            # Пытаемся исправить распространенные ошибки JSON
            # Убираем trailing commas перед закрывающими скобками
            response_text = re.sub(r',\s*}', '}', response_text)
            response_text = re.sub(r',\s*]', ']', response_text)
            
            # Парсим JSON
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as json_error:
                # Если не удалось распарсить, логируем детали
                logger.error(f"Ошибка парсинга JSON: {json_error}")
                logger.error(f"Проблемный текст (первые 1000 символов): {response_text[:1000]}")
                logger.error(f"Позиция ошибки: {json_error.pos if hasattr(json_error, 'pos') else 'unknown'}")
                # Пытаемся извлечь хотя бы частичную информацию
                result = {
                    "document_type": "unknown",
                    "document_name": "Название не определено",
                    "confidence": 0.0,
                    "description": "Не удалось распарсить ответ LLM",
                    "key_indicators": []
                }
            
            # Проверяем наличие обязательных полей
            if "document_type" not in result:
                result["document_type"] = "unknown"
            if "document_name" not in result:
                result["document_name"] = "Название не определено"
            if "confidence" not in result:
                result["confidence"] = 0.0
            if "description" not in result:
                result["description"] = "Описание не предоставлено"
            if "key_indicators" not in result:
                result["key_indicators"] = []
            
            result["titles_count"] = len(titles)
            
            logger.info(f"Определен тип документа: {result.get('document_type', 'unknown')}")
            logger.info(f"Название документа: {result.get('document_name', 'Название не определено')}")
            logger.info(f"Уверенность: {result.get('confidence', 0.0):.2f}")
            return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON ответа LLM: {e}")
        logger.error(f"Ответ LLM (первые 500 символов): {response_text[:500] if 'response_text' in locals() else 'N/A'}")
        return {
            "document_type": "unknown",
            "document_name": "Название не определено",
            "confidence": 0.0,
            "description": f"Ошибка при определении типа: {str(e)}",
            "titles_count": len(titles),
            "error": "json_decode_error",
            "key_indicators": []
        }
    except Exception as e:
        logger.error(f"Ошибка при определении типа документа: {e}")
        return {
            "document_type": "unknown",
            "document_name": "Название не определено",
            "confidence": 0.0,
            "description": f"Ошибка при определении типа: {str(e)}",
            "titles_count": len(titles),
            "error": str(e),
            "key_indicators": []
        }


def _unwrap_outline_tree(result: Any) -> Dict[str, Any]:
    """Normalize extract_outline_from_document return value to a tree dict."""
    if isinstance(result, tuple):
        if len(result) == 0:
            raise ValueError("extract_outline_from_document вернул пустой кортеж")
        if len(result) > 1:
            logger.warning(
                "extract_outline_from_document вернул кортеж из %s значений, используем первое",
                len(result),
            )
        result = result[0]
    if not isinstance(result, dict):
        raise ValueError(
            f"extract_outline_from_document вернул {type(result).__name__}, ожидался dict"
        )
    return result


def _normalize_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure hierarchy_context is present at chunk root and inside fragment_data."""
    hc = chunk.get("hierarchy_context")
    if not isinstance(hc, dict):
        hc = {}
        chunk["hierarchy_context"] = hc
    fd = chunk.get("fragment_data")
    if not isinstance(fd, dict):
        fd = {}
        chunk["fragment_data"] = fd
    if "hierarchy_context" not in fd:
        fd["hierarchy_context"] = hc
    return chunk


def create_llm_chunks(
    data_source: Union[str, Dict[str, Any]], 
    min_size: int = 50,
    document_name: str = None,
    document_type_info: Dict[str, Any] = None,
    document_kind: str = "normative",
    prose_min_chars: Optional[int] = None,
    stats: Optional[Dict[str, Any]] = None,
    segmenter: Optional[Any] = None,
    pack_limits: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """
    Создаёт чанки в формате, готовом для передачи в LLM.

    pack_limits=(target, max) — пределы упаковки прозы на время вызова
    (метод llm: сегмент модели целиком до max); по умолчанию
    TEXTBOOK_TARGET_CHARS / TEXTBOOK_MAX_CHARS.
    
    Args:
        data_source: Путь к JSON файлу (str) или словарь с данными дерева (Dict)
        min_size: Минимальный размер текста для создания чанка (по умолчанию 50)
        document_name: Имя документа для генерации fragment_id
        document_type_info: Информация о типе документа (для добавления source)
        document_kind: "normative" (legal), "textbook", or "encyclopedia"
        prose_min_chars: Целевой минимум чанка прозы; None = дефолт вида документа
            (PROSE_MIN_CHUNK_CHARS), 0 = не укрупнять короткие фрагменты
        stats: Словарь для статистики (заполняется): dropped_blocks — что и
            сколько выброшено по типам, chunks_by_block_type — чанки по типам
        segmenter: Объект с методом boundaries(paragraphs) -> set[int] (индексы
            абзацев, с которых обязан начинаться новый чанк) — семантический
            чанкинг поверх правил; None = только правила
    
    Returns:
        Список чанков в формате для LLM
    """
    token = _PACK_LIMITS.set(tuple(pack_limits)) if pack_limits else None
    try:
        return _create_llm_chunks_impl(
            data_source, min_size, document_name, document_type_info, document_kind,
            prose_min_chars, stats, segmenter,
        )
    finally:
        if token is not None:
            _PACK_LIMITS.reset(token)


def _create_llm_chunks_impl(
    data_source: Union[str, Dict[str, Any]],
    min_size: int,
    document_name: Optional[str],
    document_type_info: Optional[Dict[str, Any]],
    document_kind: str,
    prose_min_chars: Optional[int],
    stats: Optional[Dict[str, Any]],
    segmenter: Optional[Any],
) -> List[Dict[str, Any]]:
    kind = document_kind if document_kind in VALID_DOCUMENT_KINDS else "normative"
    if stats is None:
        stats = {}
    stats.setdefault("dropped_blocks", {})
    stats.setdefault("chunks_by_block_type", {})
    # Загружаем данные: либо из файла, либо используем переданный словарь
    if isinstance(data_source, str):
        # Это путь к файлу - нормализуем путь
        file_path = Path(data_source).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if document_name is None:
            document_name = file_path.stem
        
        # Проверяем, является ли загруженный файл файлом с готовыми чанками
        # Если это словарь с ключом "chunks", значит это уже готовые чанки
        if isinstance(data, dict) and "chunks" in data:
            # Это файл с готовыми чанками - возвращаем их напрямую
            chunks_list = data["chunks"]
            if isinstance(chunks_list, list):
                logger.info(f"Обнаружен файл с готовыми чанками, возвращаем {len(chunks_list)} чанков")
                return [_normalize_chunk(c) for c in chunks_list if isinstance(c, dict)]
    else:
        # Это уже словарь с данными
        data = data_source
        if document_name is None:
            document_name = "document"
        
        # Проверяем, является ли переданный словарь файлом с готовыми чанками
        if isinstance(data, dict) and "chunks" in data:
            chunks_list = data["chunks"]
            if isinstance(chunks_list, list):
                logger.info(f"Обнаружены готовые чанки, возвращаем {len(chunks_list)} чанков")
                return [_normalize_chunk(c) for c in chunks_list if isinstance(c, dict)]
    
    if not isinstance(data, dict):
        raise TypeError(
            f"Ожидался dict (дерево документа), получен {type(data).__name__}"
        )

    if kind in PROSE_KINDS:
        # Разделы-аппарат учебника выбрасываются до обхода; дерево вызывающего
        # не трогаем.
        data = _drop_apparatus_sections(copy.deepcopy(data), stats)
        _strip_inset_title_lines(data)
        _fix_inset_nodes(data, stats)
        _push_short_intro_down(data)
    
    chunks = []
    chunk_counter = 0
    
    prose_floor = (
        PROSE_MIN_CHUNK_CHARS.get(kind, 0)
        if prose_min_chars is None
        else max(0, prose_min_chars)
    )

    def split_content_for_textbook(
        content: str,
        node_block_type: Optional[str] = None,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
        para_pages: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Проза: содержимое узла → единицы {text, block_type, page_start, page_end}."""
        return _split_prose_units(
            content, prose_floor, stats, node_block_type, page_start, page_end, segmenter,
            para_pages=para_pages,
        )

    def split_content_into_items(content: str) -> List[str]:
        """
        Разбивает content на отдельные нумерованные пункты.
        Возвращает список пунктов, где каждый пункт начинается с цифры и точки/скобки.
        Формат: "1. текст", "2. текст", "1) текст", "2) текст"

        Дополнительно:
        Если внутри content затесался маркер новой статьи (например,
        "**Статья 15.** Категории преступлений"), не приклеиваем его как
        продолжение предыдущего пункта — режем по этим маркерам отдельно
        и выкидываем их сами (название статьи уйдёт в title чанка через дерево;
        здесь же мы спасаем границы между пунктами разных статей).

        Промежутки текста ВНЕ нумерованных пунктов (проза главы учебника,
        ошибочно загруженной как normative) длиной >= NORMATIVE_GAP_MIN_CHARS
        не выбрасываются — режутся абзацным сплиттером и вставляются
        в общий список в порядке документа.
        """
        if not content or not content.strip():
            return []
        
        import re
        # 1) Сначала вырезаем из content любые «жирные» маркеры статей,
        # чтобы они не приклеивались к предыдущему нумерованному пункту.
        # Случай возникает, когда GarantProcessor/ConsultantProcessor по какой-то
        # причине не отделили статью в отдельный узел дерева (например, заголовок
        # пришёл не в том формате, который мы умеем превращать в "## ...").
        article_marker_re = re.compile(
            r'(?:^|\n)\s*[*_]{0,3}Статья\s+\d+[\w\u00b9\u00b2\u00b3\u2070-\u2079]*\.?[*_]{0,3}'
            r'\s*(?P<tail>[^\n]*)',
            re.IGNORECASE,
        )
        # The article title runs to the end of the line, but in PDF/OCR layouts
        # item 1 is often glued onto that same line. Dropping the whole line took
        # the norm itself with it — and silently, since coverage below is
        # measured on the already-stripped text.
        glued_item_re = re.compile(r'(?:(?<=\s)|^)(\d+[\.\)]\s+\S.*)$')

        def _drop_article_marker(m: "re.Match") -> str:
            tail = m.group('tail') or ''
            item = glued_item_re.search(tail)
            return '\n' + item.group(1) if item else '\n'

        original_len = len(content.strip())
        content = article_marker_re.sub(_drop_article_marker, content)

        # Паттерн для нумерованных пунктов:
        # Начинается с начала строки или после двойного переноса строки
        # Затем цифра + точка/скобка + пробел + текст до следующего пункта или конца
        # Примеры: "1. текст", "2. текст\n\nеще текст", "1) текст"
        # Учитываем, что пункт может содержать несколько абзацев до следующего пункта
        item_pattern = re.compile(
            r'(?:^|\n\n|\n)(\d+[\.\)]\s+(?:[^\n]+(?:\n(?!\d+[\.\)])[^\n]*)*))',
            re.MULTILINE
        )
        
        matches = list(item_pattern.finditer(content))
        
        if not matches:
            # Если не найдены нумерованные пункты, проверяем есть ли нумерация в начале content
            # Если content начинается с цифры и точки/скобки - это один пункт
            if re.match(r'^\d+[\.\)]\s+', content.strip()):
                return [content.strip()]
            # Иначе возвращаем весь content как один пункт (если >= min_size будет проверено позже)
            return [content.strip()] if content.strip() else []
        
        items: List[str] = []
        covered_chars = 0
        cursor = 0

        def _append_gap(gap_raw: str) -> None:
            nonlocal covered_chars
            gap = gap_raw.strip()
            if len(gap) < NORMATIVE_GAP_MIN_CHARS:
                return
            packed = [u["text"] for u in split_content_for_textbook(gap)]
            for part in packed:
                part = part.strip()
                if part:
                    items.append(part)
                    covered_chars += len(part)

        for match in matches:
            _append_gap(content[cursor:match.start(1)])
            item_text = match.group(1).strip()
            if item_text:
                items.append(item_text)
                covered_chars += len(item_text)
            cursor = match.end(1)

        _append_gap(content[cursor:])

        # Если не нашли пункты через паттерн, но content не пустой, возвращаем весь content
        if not items and content.strip():
            items = [content.strip()]
            covered_chars = len(items[0])

        # Measure against the text as it arrived, not as it survived the article
        # marker strip — otherwise anything removed above is invisible here.
        total = original_len
        if total > 0:
            coverage_pct = 100.0 * covered_chars / total
            logger.info(
                "Normative split coverage: %.1f%% (%d/%d chars, %d items, gaps rescued)",
                coverage_pct,
                covered_chars,
                total,
                len(items),
            )

        return items
    
    def create_chunk(
        breadcrumb_text: str,
        title: str,
        item_content: str,
        breadcrumb_path: list,
        level: int,
        depth: int,
        block_type: str = "body",
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
        topic_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Создает один чанк из breadcrumb, title и content одного пункта.
        """
        nonlocal chunk_counter
        
        if kind == "encyclopedia":
            # Headword is the topic — prefix so continuation chunks stay grounded.
            if title and item_content:
                combined_text = f"{title}\n\n{item_content}"
            elif title:
                combined_text = title
            else:
                combined_text = item_content
        elif kind == "textbook":
            # Гейт и генератор видят только текст чанка, поэтому глава и параграф
            # должны быть в нём: иначе «В марте 2024 г. президент заявил…» повисает
            # без темы. Заголовки из PDF предварительно чистим от мусора вёрстки.
            context_line = _prose_context_line(
                [bc.get("title", "") for bc in breadcrumb_path], title
            )
            if context_line and item_content:
                combined_text = f"{context_line}\n\n{item_content}"
            else:
                combined_text = item_content or context_line
        elif breadcrumb_text and title:
            combined_text = f"{breadcrumb_text} > {title}"
            if item_content:
                combined_text += f"\n\n{item_content}"
        elif title:
            combined_text = title
            if item_content:
                combined_text += f"\n\n{item_content}"
        elif breadcrumb_text:
            combined_text = breadcrumb_text
            if item_content:
                combined_text += f"\n\n{item_content}"
        else:
            combined_text = item_content
        
        # Проверяем минимальный размер чанка (min_size символов)
        if len(combined_text.strip()) < min_size:
            return None
        
        chunk_counter += 1
        if block_type not in KEPT_BLOCK_TYPES:
            block_type = "body"
        _stats_add(stats, "chunks_by_block_type", block_type, len(combined_text))

        hierarchy_context = {
            "breadcrumb_path": breadcrumb_path,
            "breadcrumb_text": breadcrumb_text,
            "current_level": level,
            "depth": depth,
        }

        return {
            "fragment_id": f"{document_name}_chunk_{chunk_counter}",
            "hierarchy_context": hierarchy_context,
            "fragment_data": {
                "title": title,
                "content": item_content,  # Только один пункт
                "combined_text": combined_text,
                "is_leaf_node": True,
                "has_children": False,
                "text_length": len(combined_text),
                "source": document_type_info.get("document_name", "") if document_type_info else "",
                "hierarchy_context": hierarchy_context,
                # Тип блока (body | list | inset | caption | glossary_entry) и
                # страницы источника — подсказка гейту/генератору и UI.
                "block_type": block_type,
                "page_start": page_start,
                "page_end": page_end,
                # Название подтемы от модели (метод llm): для UI и выгрузки,
                # в текст чанка не входит.
                **({"topic_title": topic_title} if topic_title else {}),
                # Признаки обрыва (учебники/энциклопедии): gate и метрики.
                **({"boundary_flags": boundary_flags(item_content)} if kind in PROSE_KINDS and block_type in ("body", "list") else {}),
            },
        }
    
    def process_node(node, breadcrumb_titles=None, breadcrumb_levels=None, depth=0):
        nonlocal chunk_counter

        if breadcrumb_titles is None:
            breadcrumb_titles = []
        if breadcrumb_levels is None:
            breadcrumb_levels = []
        
        title = node.get("title", "").strip()
        content = node.get("content", "").strip()
        level = node.get("level", 0)
        children = node.get("children", [])
        node_block_type = node.get("block_type") if node.get("block_type") in _NODE_BLOCK_TYPES else None
        
        # Обновляем breadcrumb для текущего узла (включая его title)
        # Это будет использоваться для детей этого узла
        current_breadcrumb_titles = breadcrumb_titles + [title] if title else breadcrumb_titles
        current_breadcrumb_levels = breadcrumb_levels + [level] if title else breadcrumb_levels
        
        # Формируем breadcrumb path для текущего узла
        breadcrumb_path = []
        # Защита от неправильной распаковки: убеждаемся, что списки имеют одинаковую длину
        min_len = min(len(breadcrumb_titles), len(breadcrumb_levels))
        for i in range(min_len):
            bc_title = breadcrumb_titles[i]
            bc_level = breadcrumb_levels[i]
            breadcrumb_path.append({
                "level": bc_level,
                "title": bc_title,
                "position": i
            })
        
        # Breadcrumb text - только родительские заголовки
        breadcrumb_text = " > ".join(breadcrumb_titles) if breadcrumb_titles else ""
        
        # Создаём чанки ТОЛЬКО для листовых узлов (без детей)
        # Узлы с детьми НЕ создают чанки для себя - только для своих детей
        # Исключение (textbook): контент родителя (вводный текст до §/подзаголовка)
        # тоже упаковывается, иначе молча теряется.
        is_leaf = len(children) == 0

        def _emit_content_chunks(node_content: str, node_title: str) -> None:
            if not node_content:
                return
            is_prose = kind in PROSE_KINDS
            units = (
                split_content_for_textbook(
                    node_content, node_block_type, node.get("page_start"), node.get("page_end"),
                    para_pages=node.get("para_pages") if isinstance(node.get("para_pages"), list) else None,
                )
                if is_prose
                else [_unit(item) for item in split_content_into_items(node_content)]
            )
            items = [u["text"] for u in units]
            if items:
                for u in units:
                    chunk = create_chunk(
                        breadcrumb_text, node_title, u["text"], breadcrumb_path, level, depth,
                        block_type=u["block_type"],
                        page_start=u.get("page_start"),
                        page_end=u.get("page_end"),
                        topic_title=u.get("topic_title"),
                    )
                    if chunk:
                        chunks.append(chunk)
                items_chars = sum(len(i) for i in items)
                logger.info(
                    "Leaf node chunk coverage: %.1f%% (%d items / %d content chars, kind=%s, title=%r)",
                    100.0 * items_chars / max(len(node_content), 1),
                    len(items),
                    len(node_content),
                    kind,
                    (node_title or "")[:80],
                )
            elif is_prose:
                # Проза: пустой результат означает, что узел состоял из огрызков
                # вёрстки. Вернуть его целиком нельзя — фильтр стал бы бесполезным.
                logger.debug(
                    "Prose node produced no chunks (kind=%s, %d chars, title=%r)",
                    kind, len(node_content), (node_title or "")[:80],
                )
            else:
                chunk = create_chunk(breadcrumb_text, node_title, node_content, breadcrumb_path, level, depth)
                if chunk:
                    chunks.append(chunk)

        if is_leaf:
            if content:
                _emit_content_chunks(content, title)
            elif title and kind not in PROSE_KINDS:
                # Есть только title, без content - создаем чанк если достаточно длинный
                # (skip for prose kinds: structure-only nodes should not become questions)
                chunk = create_chunk(breadcrumb_text, title, "", breadcrumb_path, level, depth)
                if chunk:
                    chunks.append(chunk)
        elif content:
            # Non-leaf with body text (intro before subsections) — keep it.
            # This used to be prose-only, so a normative section that carries its
            # own text on top of subsections (a GOST «4 Общие требования», a
            # chapter preamble before «Статья 17») produced no chunk at all.
            _emit_content_chunks(content, title)
        
        # Рекурсия в детей - передаем обновленный breadcrumb (включая текущий title)
        for child in children:
            process_node(child, current_breadcrumb_titles, current_breadcrumb_levels, depth + 1)
    
    process_node(data)

    # Coverage accounting runs for every kind: normative documents had none
    # at all, so text lost in the split was invisible.
    def _sum_tree_content(node: Dict[str, Any]) -> int:
        total = len((node.get("content") or "").strip())
        for ch in node.get("children") or []:
            if isinstance(ch, dict):
                total += _sum_tree_content(ch)
        return total

    source_chars = _sum_tree_content(data) if isinstance(data, dict) else 0
    chunk_chars = sum(
        len((c.get("fragment_data") or {}).get("content") or "")
        for c in chunks
    )
    # Deliberately dropped text (exercises, front matter, debris) is accounted
    # for, not lost: the warning below is about silent loss only.
    dropped_chars = sum(
        int(v.get("chars", 0)) for v in (stats.get("dropped_blocks") or {}).values()
    )
    coverage_pct = 100.0 * (chunk_chars + dropped_chars) / max(source_chars, 1)
    logger.info(
        "Chunk coverage (%s): %.1f%% (%d chunk chars + %d dropped / %d tree content chars, %d chunks)",
        kind,
        coverage_pct,
        chunk_chars,
        dropped_chars,
        source_chars,
        len(chunks),
    )
    if source_chars > 0 and coverage_pct < 90.0:
        logger.warning(
            "Chunk coverage below 90%% (%.1f%%, kind=%s) — possible silent text loss",
            coverage_pct,
            kind,
        )

    return chunks


def _extract_layout_tree_or_none(
    doc_path: Path, kind: str, stats: Dict[str, Any], required: bool
) -> Optional[Dict[str, Any]]:
    """Дерево из layout-извлечения PDF (textbook_layout) или None → фолбэк на rules.

    required=True: метод layout выбран явно — причина фолбэка пишется в
    stats["chunking_fallback"]; для semantic layout лишь желательная основа.
    """
    try:
        from textbook_layout import extract_layout_tree, LayoutUnavailable
    except ImportError as exc:  # модуль появляется вместе с layout-путём
        if required:
            stats["chunking_fallback"] = f"layout module unavailable: {exc}"
        return None
    try:
        tree = extract_layout_tree(str(doc_path), kind=kind, stats=stats)
    except LayoutUnavailable as exc:
        logger.warning("Layout extraction not applicable, falling back to rules: %s", exc)
        stats["chunking_fallback"] = str(exc)
        return None
    except Exception as exc:  # noqa: BLE001 — фолбэк не должен ронять чанкинг
        logger.warning("Layout extraction failed, falling back to rules: %s", exc, exc_info=True)
        stats["chunking_fallback"] = f"layout extraction failed: {exc}"
        return None
    stats["chunking_engine"] = "layout_pdf"
    return tree


def _make_semantic_segmenter(stats: Dict[str, Any]):
    """Сегментер границ по эмбеддингам для метода semantic (chunker/semantic_segmenter.py)."""
    from semantic_segmenter import SemanticSegmenter

    stats["chunking_engine"] = f"{stats.get('chunking_engine', 'hierarchical_outline')}+semantic"
    return SemanticSegmenter.from_env(stats=stats)


def _fallback_reason(error: str) -> str:
    """«busy: 503 …» → busy; «deadline» → deadline (см. llm_client.WindowRunner)."""
    head = (error or "").split(":", 1)[0].strip().lower()
    return head or "unknown"


def _record_llm_fallback(stats: Dict[str, Any]) -> None:
    """Окна, размеченные правилами вместо модели, — это фолбэк, и он должен быть
    виден в карточке набора (chunking_fallback), а не только в логах."""
    seg = stats.get("llm_segmentation") or {}
    fallback = int(seg.get("fallback") or 0)
    windows = int(seg.get("windows") or 0)
    if fallback <= 0 or stats.get("chunking_fallback"):
        return
    reasons: Dict[str, int] = {}
    for err in (seg.get("errors") or {}).values():
        key = _fallback_reason(str(err))
        reasons[key] = reasons.get(key, 0) + 1
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    stats["chunking_fallback"] = f"llm: {fallback}/{windows} windows by rules" + (f" ({detail})" if detail else "")


def build_chunking_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Диагностика чанкинга для ответа /chunk/ и карточки набора.

    Для метода llm: откуда взяты абзацы (layout | text), окна (всего / моделью /
    по правилам), обрезанные ответы, gapfill; для layout — профиль вёрстки.
    Полный список ошибок окон не отдаём — только число и первые пять.
    """
    out: Dict[str, Any] = {}
    source = stats.get("llm_segmentation_source")
    if source:
        out["paragraph_source"] = source
    seg = stats.get("llm_segmentation")
    if isinstance(seg, dict):
        public = {k: v for k, v in seg.items() if k != "errors"}
        errors = seg.get("errors") or {}
        public["errors_count"] = len(errors)
        public["errors_sample"] = [f"{idx}: {str(err)[:200]}" for idx, err in list(errors.items())[:5]]
        out["llm_segmentation"] = public
    for key in ("layout", "semantic"):
        if isinstance(stats.get(key), dict):
            out[key] = stats[key]
    return out


def _process_document_with_llm(
    doc_path: Path,
    min_size: int,
    kind: str,
    prose_min_chars: Optional[int],
    stats: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Метод llm: сегментация абзацев моделью (chunker/llm_segmentation)."""
    from llm_segmentation import segment_document_with_llm

    stats["chunking_engine"] = "llm_segmentation"
    deadline = time.monotonic() + max(60, _env_int("CHUNKER_PROCESS_TIMEOUT_LLM", 7000) - 90)
    chunks, heading_titles = segment_document_with_llm(
        str(doc_path),
        document_name=doc_path.stem,
        kind=kind,
        min_size=min_size,
        prose_min_chars=prose_min_chars,
        deadline=deadline,
        stats=stats,
    )
    _record_llm_fallback(stats)
    document_type_info = identify_document_type(heading_titles)
    for chunk in chunks:
        fd = chunk.get("fragment_data") or {}
        fd["source"] = document_type_info.get("document_name", "") if document_type_info else ""
    return chunks, document_type_info


def process_document_to_chunks(
    document_path: str,
    min_size: int = 50,
    document_kind: str = "normative",
    prose_min_chars: Optional[int] = None,
    stats: Optional[Dict[str, Any]] = None,
    chunking_method: str = DEFAULT_CHUNKING_METHOD,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Полный цикл обработки документа: документ → docx2json_outline → определение типа → chunker → чанки для LLM.
    
    Args:
        document_path: Путь к исходному документу (DOCX, PDF и т.д.)
        min_size: Минимальный размер текста для создания чанка
        document_kind: "normative", "textbook", or "encyclopedia"
        prose_min_chars: Порог самостоятельного чанка прозы (None = дефолт вида)
        stats: Словарь для статистики чанкинга (dropped_blocks, chunks_by_block_type,
            chunking_method / chunking_engine / chunking_fallback)
        chunking_method: rules | layout | semantic | llm (см. CHUNKING_METHODS)
    
    Returns:
        Кортеж: (список чанков в формате для LLM, информация о типе документа)
    """
    # Нормализуем путь к документу
    doc_path = Path(document_path).resolve()
    if not doc_path.exists():
        raise FileNotFoundError(f"Документ не найден: {doc_path}")

    kind = document_kind if document_kind in VALID_DOCUMENT_KINDS else "normative"
    method = normalize_chunking_method(chunking_method)
    check_chunking_method(method, kind)
    if stats is None:
        stats = {}
    stats["chunking_method"] = method
    stats["chunking_engine"] = "hierarchical_outline"
    stats["chunking_fallback"] = None
    
    logger.info(f"Обработка документа: {doc_path} (method={method}, kind={kind})")

    if method == "llm":
        return _process_document_with_llm(doc_path, min_size, kind, prose_min_chars, stats)
    
    # Шаг 1: Обработка документа через docx2json_outline (или layout-путь для PDF)
    logger.info("[Шаг 1] Извлечение структуры документа...")
    tree = None
    if method in ("layout", "semantic") and kind in PROSE_KINDS and doc_path.suffix.lower() == ".pdf":
        tree = _extract_layout_tree_or_none(doc_path, kind, stats, required=(method == "layout"))
    if tree is None:
        tree = _unwrap_outline_tree(
            extract_outline_from_document(str(doc_path), document_kind=document_kind)
        )
    logger.info("✓ Структура извлечена")
    
    # Шаг 2: Определение типа документа
    logger.info("[Шаг 2] Определение типа документа...")
    titles = extract_all_titles(tree)
    document_type_info = identify_document_type(titles)
    logger.info(f"✓ Тип документа: {document_type_info.get('document_type', 'unknown')}")
    logger.info(f"✓ Название документа: {document_type_info.get('document_name', 'Название не определено')}")
    
    # Шаг 3: Создание чанков
    logger.info("[Шаг 3] Создание чанков для LLM...")
    document_name = doc_path.stem
    segmenter = _make_semantic_segmenter(stats) if method == "semantic" else None
    chunks = create_llm_chunks(
        tree, 
        min_size=min_size, 
        document_name=document_name,
        document_type_info=document_type_info,
        document_kind=document_kind,
        prose_min_chars=prose_min_chars,
        stats=stats,
        segmenter=segmenter,
    )
    logger.info(f"✓ Создано {len(chunks)} чанков (document_kind={document_kind})")
    if stats:
        logger.info(
            "Chunking stats: dropped_blocks=%s chunks_by_block_type=%s",
            json.dumps(stats.get("dropped_blocks", {}), ensure_ascii=False),
            json.dumps(stats.get("chunks_by_block_type", {}), ensure_ascii=False),
        )
    
    return chunks, document_type_info


@app.get("/")
async def root():
    return {
        "message": "GenA Chunker Service",
        "version": "2.0.0",
        "description": "Сервис для создания чанков из документов с использованием docx2json_outline",
        "endpoints": {
            "/chunk/": "POST - обработка загруженного документа",
            "/chunk-docx/": "POST - legacy endpoint для DOCX файлов",
            "/health": "GET - проверка состояния сервиса"
        }
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "chunker", "version": "2.0.0"}


@app.get("/chunking-methods/")
async def chunking_methods():
    """Методы чанкинга и их доступность по текущей конфигурации (для диагностики)."""
    availability = chunking_method_availability()
    return {
        "default": DEFAULT_CHUNKING_METHOD,
        "methods": [
            {
                "id": m,
                "available": availability[m]["available"],
                "reason": availability[m]["reason"],
                "document_kinds": [k for k, ms in CHUNKING_METHODS_BY_KIND.items() if m in ms],
            }
            for m in CHUNKING_METHODS
        ],
    }


@app.post("/chunk/")
async def chunk_file(
    file: UploadFile = File(...),
    min_size: int = Query(50, ge=1, le=100_000, description="Минимальный размер текста для создания чанка"),
    document_kind: str = Query(
        "normative",
        description="Pipeline flavour: normative (legal), textbook, or encyclopedia",
    ),
    prose_min_size: Optional[int] = Query(
        None,
        ge=0,
        le=100_000,
        description=(
            "Порог самостоятельного чанка прозы (textbook/encyclopedia); "
            "по умолчанию берётся из PROSE_MIN_CHUNK_CHARS, 0 отключает склейку"
        ),
    ),
    chunking_method: str = Query(
        DEFAULT_CHUNKING_METHOD,
        description="Метод чанкинга: rules (правила), layout (PDF по шрифтам), semantic (эмбеддинги), llm (модель)",
    ),
):
    """
    Обработка загруженного документа: извлечение структуры и создание чанков для LLM.
    Поддерживает DOCX, PDF и другие форматы, которые поддерживает docx2json_outline.
    """
    if not isinstance(document_kind, str):
        document_kind = "normative"
    kind = (document_kind or "normative").strip().lower()
    if kind not in VALID_DOCUMENT_KINDS:
        kind = "normative"
    if not isinstance(chunking_method, str):
        # Прямой вызов из /chunk-docx/ или тестов: дефолт Query(...) вместо строки.
        chunking_method = DEFAULT_CHUNKING_METHOD
    method_raw = (chunking_method or "").strip().lower()
    if method_raw and method_raw not in CHUNKING_METHODS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"unknown chunking method '{method_raw}'; expected one of {', '.join(CHUNKING_METHODS)}"},
        )
    method = method_raw or DEFAULT_CHUNKING_METHOD

    if not file.filename:
        return JSONResponse(status_code=400, content={"detail": "Filename is required"})

    file_extension = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
    logger.info(f"Расширение файла: {file_extension}")

    content_length = file.headers.get("content-length") if hasattr(file, "headers") else None
    try:
        if content_length and int(content_length) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"File too large (max {MAX_UPLOAD_BYTES} bytes)"},
            )
    except (TypeError, ValueError):
        pass

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    logger.info(f"Получен запрос на обработку файла: {file.filename}, размер: {len(content)} bytes")

    if not content:
        return JSONResponse(status_code=400, content={"detail": "Empty file"})
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"File too large (max {MAX_UPLOAD_BYTES} bytes)"},
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp:
            logger.info(f"Прочитано {len(content)} байт из загруженного файла")
            tmp.write(content)
            tmp_path = tmp.name
            logger.info(f"Создан временный файл: {tmp_path}")

        process_timeout = int(os.getenv("CHUNKER_PROCESS_TIMEOUT", "1800"))
        if method == "llm":
            # Сегментация моделью — десятки вызовов LLM на документ.
            process_timeout = _env_int("CHUNKER_PROCESS_TIMEOUT_LLM", 7000)
        chunk_stats: Dict[str, Any] = {}
        result = await asyncio.wait_for(
            asyncio.to_thread(
                process_document_to_chunks,
                tmp_path,
                min_size=min_size,
                document_kind=kind,
                prose_min_chars=prose_min_size,
                stats=chunk_stats,
                chunking_method=method,
            ),
            timeout=process_timeout,
        )
        if isinstance(result, tuple) and len(result) == 2:
            chunks, document_type_info = result
        else:
            raise ValueError(
                f"process_document_to_chunks вернул неожиданное количество значений: "
                f"{len(result) if isinstance(result, tuple) else 'не кортеж'}"
            )

        logger.info(f"Успешно обработан файл, создано {len(chunks)} чанков")
        chunks_text = [
            (chunk.get("fragment_data") or {}).get("combined_text", "")
            for chunk in chunks
        ]
        if not chunks or not any(text.strip() for text in chunks_text):
            return JSONResponse(
                status_code=422,
                content={"detail": "No chunks produced (document may be empty or below min_size)"},
            )

        return {
            "filename": file.filename,
            "file_type": file_extension,
            "num_chunks": len(chunks),
            "chunks": chunks_text,
            "chunks_detailed": chunks,
            # Метод, выбранный в UI, движок, который реально отработал, и
            # причина фолбэка (например, PDF без текстового слоя → rules).
            "chunking_method": chunk_stats.get("chunking_method", method),
            "chunking_engine": chunk_stats.get("chunking_engine", "hierarchical_outline"),
            "chunking_fallback": chunk_stats.get("chunking_fallback"),
            "min_size": min_size,
            "document_type": document_type_info,
            "document_kind": kind,
            # Что выброшено чанкером (задания, аппарат, оглавление, огрызки)
            # и сколько чанков какого типа блока получилось.
            "dropped_blocks": chunk_stats.get("dropped_blocks", {}),
            "chunks_by_block_type": chunk_stats.get("chunks_by_block_type", {}),
            # Диагностика (метод llm: окна и источник абзацев; layout: профиль).
            "chunking_stats": build_chunking_stats(chunk_stats),
        }

    except asyncio.TimeoutError:
        logger.error("Chunker processing timed out")
        return JSONResponse(status_code=504, content={"detail": "Chunking timed out"})
    except (ValueError, FileNotFoundError, TypeError, json.JSONDecodeError) as e:
        logger.warning(f"Ошибка валидации/обработки файла: {e}")
        return JSONResponse(status_code=400, content={"detail": str(e)})
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info(f"Удален временный файл: {tmp_path}")


@app.post("/chunk-docx/")
async def chunk_docx(
    file: UploadFile = File(...),
    min_size: int = Query(50, ge=1, le=100_000),
    document_kind: str = Query("normative"),
):
    """
    Legacy endpoint для DOCX файлов только.
    """
    if not file.filename or not file.filename.lower().endswith(".docx"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Этот endpoint поддерживает только .docx файлы."},
        )
    
    return await chunk_file(file, min_size=min_size, document_kind=document_kind)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8517)
