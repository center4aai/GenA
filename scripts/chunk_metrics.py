#!/usr/bin/env python3
"""Метрики качества чанкинга по JSON-ответу чанкера (до/после правок).

Вход — один или два JSON: ответ ``POST /chunk/`` либо файл из
``scripts/chunk_report.py --json-out``. С двумя файлами печатается таблица
«до / после / Δ». Считает только по чанкам, модель не вызывает.

    python scripts/chunk_metrics.py before.json [after.json] [--json metrics.json]

Метрики (body и list, если не сказано иное):
  - число чанков, по типам блоков, гистограмма длин, медиана/p10/p90 (body, inset);
  - доля чанков, начинающихся со строчной буквы (обрыв начала);
  - доля чанков без знака конца предложения (обрыв конца);
  - body-чанков на страницу, доля чанков через границу страницы, без страниц;
  - чанки с колонтитулом («8 ГЛАВА I», «§ 1. … 7»);
  - нумерованные подзаголовки («1 Влияние Победы.») в начале / в середине чанка;
  - врезки: число, медиана, короче 160 символов;
  - утечки заданий («⬤», «Выясните…», номерные вопросы «4. Какие…?» в body/inset);
  - body-чанки, в пути которых стоит заголовок врезки того же документа
    (текст врезки стал разделом); чанки с «Глава …» в пути;
  - выброшенное чанкером, движок/фолбэк, статистика llm-окон.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
for cand in (_HERE.parent / "chunker", _HERE.parent):
    if (cand / "text_utils.py").is_file() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

try:  # внутри образа чанкера / репозитория
    from text_utils import ends_like_sentence, find_numbered_task, is_exercise_paragraph  # type: ignore
except Exception:  # noqa: BLE001 — запуск на голом python по одному JSON
    _NUMBERED_TASK_RE = re.compile(r"(?:^|(?<=\n)|(?<=[.!?…»)]\s))\d{1,2}(\*?)[.)]\s+[А-ЯЁ«][^\n]{0,300}")

    def find_numbered_task(text: str):  # type: ignore
        for m in _NUMBERED_TASK_RE.finditer(text or ""):
            if m.group(1) or "?" in m.group(0):
                return m.start()
        return None

    _SENT_END_RE = re.compile(r'[.!?…]\s*["»)\]]?$')
    _EXERCISE_RE = re.compile(r"^\s*(?:⬤|[•●▪■]\s*)?(?:Выясните|Узнайте|Подумайте|Обсудите|Вспомните|Объясните|Составьте|Приведите|Сравните)\b")

    def ends_like_sentence(text: str) -> bool:  # type: ignore
        return bool(_SENT_END_RE.search((text or "").strip()))

    def is_exercise_paragraph(text: str) -> bool:  # type: ignore
        return bool(_EXERCISE_RE.match((text or "").strip()))

RUNNING_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(?:\d{1,3}\s+ГЛАВА\s+[IVXLC]+|ГЛАВА\s+[IVXLC]+\s+\d{1,3}|§\s*\d+(?:-\d+)?\.\s.{3,120}\s\d{1,3})\s*(?:\n|$)"
)
RUNIN_START_RE = re.compile(r"^\d{1,2}\s+[А-ЯЁ][^.!?\n]{2,80}\.")
RUNIN_INSIDE_RE = re.compile(r"\n\n\d{1,2}\s+[А-ЯЁ][^.!?\n]{2,80}\.")
LOWER_START_RE = re.compile(r"^[«\"(\s]*[а-яёa-z]")
LIST_MARK_RE = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|\(?[а-яa-z][.)]|[•●▪■◆*–—-])\s+")
EXERCISE_MARK_RE = re.compile(r"⬤")
CHAPTER_IN_PATH_RE = re.compile(r"^глава\s", re.IGNORECASE)
INSET_SHORT_CHARS = 160
BODY_TYPES = {None, "", "body", "list"}
BUCKETS = [(0, 400, "<400"), (400, 1000, "400-1k"), (1000, 2000, "1-2k"), (2000, 3500, "2-3.5k"), (3500, 5000, "3.5-5k"), (5000, 10**9, ">5k")]


def _fd(c: Dict[str, Any]) -> Dict[str, Any]:
    return c.get("fragment_data") or {}


def _content(c: Dict[str, Any]) -> str:
    fd = _fd(c)
    return (fd.get("content") or fd.get("combined_text") or c.get("chunk_text") or "").strip()


def _block_type(c: Dict[str, Any]) -> Optional[str]:
    return _fd(c).get("block_type")


def _context(c: Dict[str, Any]) -> str:
    """Заголовок узла и путь по иерархии: в пути rules колонтитул попадает сюда."""
    hc = c.get("hierarchy_context") or {}
    parts = [_fd(c).get("title") or "", hc.get("breadcrumb_text") or ""]
    parts += [str(p.get("title") or "") for p in (hc.get("breadcrumb_path") or []) if isinstance(p, dict)]
    return "\n".join(x for x in parts if x)


def _path_titles(c: Dict[str, Any]) -> List[str]:
    """Заголовки уровней пути чанка (breadcrumb_path из fragment_data или верхнего уровня)."""
    titles: List[str] = []
    for hc in (_fd(c).get("hierarchy_context") or {}, c.get("hierarchy_context") or {}):
        for p in hc.get("breadcrumb_path") or []:
            if isinstance(p, dict) and str(p.get("title") or "").strip():
                titles.append(str(p["title"]).strip())
        if titles:
            break
    return titles


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower().replace("ё", "е")


def _pct(n: int, total: int) -> Optional[float]:
    return round(100.0 * n / total, 1) if total else None


def _quantiles(values: List[int]) -> Dict[str, Optional[float]]:
    if not values:
        return {"median": None, "p10": None, "p90": None}
    s = sorted(values)
    q = lambda f: s[min(len(s) - 1, int(round(f * (len(s) - 1))))]  # noqa: E731
    return {"median": statistics.median(s), "p10": q(0.1), "p90": q(0.9)}


def compute_metrics(data: Dict[str, Any]) -> Dict[str, Any]:
    chunks: List[Dict[str, Any]] = data.get("chunks_detailed") or []
    body = [c for c in chunks if _block_type(c) in BODY_TYPES]
    insets = [c for c in chunks if _block_type(c) == "inset"]
    by_type: Dict[str, int] = {}
    for c in chunks:
        by_type[_block_type(c) or "body"] = by_type.get(_block_type(c) or "body", 0) + 1
    lens_all = [len(_content(c)) for c in chunks]
    hist = {label: 0 for _lo, _hi, label in BUCKETS}
    for n in lens_all:
        for lo, hi, label in BUCKETS:
            if lo <= n < hi:
                hist[label] += 1
                break

    starts_lower = [c for c in body if LOWER_START_RE.match(_content(c)) and not LIST_MARK_RE.match(_content(c))]
    ends_open = [c for c in body if not ends_like_sentence(_content(c))]

    pages = [(_fd(c).get("page_start"), _fd(c).get("page_end")) for c in body]
    with_pages = [(a, b) for a, b in pages if isinstance(a, int) and isinstance(b, int)]
    span = [(a, b) for a, b in with_pages if b > a]
    page_set = {p for a, b in with_pages for p in range(a, b + 1)}
    headers = [c for c in chunks if RUNNING_HEADER_RE.search(_content(c)) or RUNNING_HEADER_RE.search(_context(c))]
    runin_start = [c for c in body if RUNIN_START_RE.match(_content(c))]
    runin_inside = [c for c in body if RUNIN_INSIDE_RE.search(_content(c))]
    inset_lens = [len(_content(c)) for c in insets]
    exercise_leaks = [
        c for c in chunks
        if EXERCISE_MARK_RE.search(_content(c)) or any(is_exercise_paragraph(p) for p in re.split(r"\n\s*\n", _content(c)))
        or (_block_type(c) in BODY_TYPES | {"inset"} and find_numbered_task(_content(c)) is not None)
    ]
    inset_titles = {_norm_title(_fd(c).get("title") or "") for c in insets} - {""}
    inset_in_path = [c for c in body if any(_norm_title(t) in inset_titles for t in _path_titles(c))]
    chapter_in_path = [c for c in chunks if any(CHAPTER_IN_PATH_RE.match(t) for t in _path_titles(c))]
    dropped = data.get("dropped_blocks") or {}
    _cnt = lambda k: (dropped.get(k) or {}).get("count", 0) if isinstance(dropped.get(k), dict) else (dropped.get(k) or 0)  # noqa: E731
    seg = (data.get("chunking_stats") or {}).get("llm_segmentation") or {}
    return {
        "file": data.get("filename"),
        "method": data.get("chunking_method"),
        "engine": data.get("chunking_engine"),
        "fallback": data.get("chunking_fallback"),
        "paragraph_source": (data.get("chunking_stats") or {}).get("paragraph_source"),
        "chunks": len(chunks),
        "by_type": by_type,
        "length_hist": hist,
        "body_len": _quantiles([len(_content(c)) for c in body]),
        "inset_len": _quantiles(inset_lens),
        "body_starts_lowercase_pct": _pct(len(starts_lower), len(body)),
        "body_ends_open_pct": _pct(len(ends_open), len(body)),
        "body_per_page": round(len(with_pages) / len(page_set), 2) if page_set else None,
        "body_spanning_pages_pct": _pct(len(span), len(with_pages)),
        "body_without_pages_pct": _pct(len(body) - len(with_pages), len(body)),
        "chunks_with_running_header": len(headers),
        "runin_heading_at_start": len(runin_start),
        "runin_heading_inside": len(runin_inside),
        "insets": len(insets),
        "insets_short": sum(1 for n in inset_lens if n < INSET_SHORT_CHARS),
        "exercise_leaks": len(exercise_leaks),
        "body_inset_title_in_path": len(inset_in_path),
        "chunks_chapter_in_path": len(chapter_in_path),
        "dropped_apparatus": _cnt("apparatus"),
        "dropped_inset_mixed": _cnt("inset_mixed"),
        "dropped_blocks": {k: (v.get("count") if isinstance(v, dict) else v) for k, v in (data.get("dropped_blocks") or {}).items()},
        "llm_windows": seg.get("windows"),
        "llm_fallback_windows": seg.get("fallback"),
        "llm_truncated_windows": seg.get("salvaged_truncated"),
        "llm_gapfill_segments": seg.get("gapfill_segments"),
        "_examples": {
            "starts_lowercase": [_content(c)[:100] for c in starts_lower[:3]],
            "ends_open": [_content(c)[-100:] for c in ends_open[:3]],
            "running_header": [(_context(c) or _content(c))[:100] for c in headers[:3]],
            "exercise_leaks": [_content(c)[:100] for c in exercise_leaks[:3]],
            "inset_title_in_path": [" > ".join(_path_titles(c))[:140] for c in inset_in_path[:3]],
            "chapter_in_path": [" > ".join(_path_titles(c))[:140] for c in chapter_in_path[:3]],
        },
    }


_ROWS = [
    ("chunks", "чанков"), ("by_type", "по типам"), ("length_hist", "гистограмма длин"),
    ("body_len", "длина body (медиана/p10/p90)"), ("inset_len", "длина inset"),
    ("body_starts_lowercase_pct", "body со строчной в начале, %"), ("body_ends_open_pct", "body без конца предложения, %"),
    ("body_per_page", "body-чанков на страницу"), ("body_spanning_pages_pct", "body через границу страницы, %"),
    ("body_without_pages_pct", "body без страниц, %"), ("chunks_with_running_header", "чанков с колонтитулом"),
    ("runin_heading_at_start", "подзаголовок в начале чанка"), ("runin_heading_inside", "подзаголовок в середине чанка"),
    ("insets", "врезок"), ("insets_short", f"врезок < {INSET_SHORT_CHARS} симв."), ("exercise_leaks", "утечки заданий"),
    ("body_inset_title_in_path", "body с заголовком врезки в пути"), ("chunks_chapter_in_path", "чанков с «Глава» в пути"),
    ("dropped_apparatus", "выброшено: apparatus (доп. материалы)"), ("dropped_inset_mixed", "выброшено: inset_mixed"),
    ("dropped_blocks", "выброшено"), ("llm_windows", "llm: окон"), ("llm_fallback_windows", "llm: окон по правилам"),
    ("llm_truncated_windows", "llm: обрезанных ответов"), ("llm_gapfill_segments", "llm: gapfill-сегментов"),
]


def _fmt(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("—" if v is None else str(v))


def render_markdown(before: Dict[str, Any], after: Optional[Dict[str, Any]] = None) -> str:
    out = [f"# Метрики чанкинга: {before.get('file')}", ""]
    hdr = f"{before.get('method')}/{before.get('engine')}" + (f" (фолбэк: {before.get('fallback')})" if before.get("fallback") else "")
    if after is None:
        out += [f"метод: {hdr}", "", "| метрика | значение |", "|---|---|"]
        for key, label in _ROWS:
            out.append(f"| {label} | {_fmt(before.get(key))} |")
    else:
        hdr2 = f"{after.get('method')}/{after.get('engine')}" + (f" (фолбэк: {after.get('fallback')})" if after.get("fallback") else "")
        out += [f"до: {hdr} · после: {hdr2}", "", "| метрика | до | после | Δ |", "|---|---|---|---|"]
        for key, label in _ROWS:
            a, b = before.get(key), after.get(key)
            delta = round(b - a, 2) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else ""
            out.append(f"| {label} | {_fmt(a)} | {_fmt(b)} | {delta} |")
    for name, m in (("до", before), ("после", after)):
        if not m:
            continue
        ex = m.get("_examples") or {}
        if any(ex.values()):
            out += ["", f"## Примеры ({name})"]
            for k, items in ex.items():
                for item in items:
                    out.append(f"- {k}: `{item.replace(chr(10), ' / ')}`")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", help="JSON ответа /chunk/ или chunk_report --json-out")
    ap.add_argument("after", nargs="?", help="второй JSON для сравнения")
    ap.add_argument("--json", dest="json_out", help="сохранить метрики в JSON")
    ap.add_argument("--out", help="markdown-файл (по умолчанию stdout)")
    args = ap.parse_args()
    before = compute_metrics(json.loads(Path(args.before).read_text(encoding="utf-8")))
    after = compute_metrics(json.loads(Path(args.after).read_text(encoding="utf-8"))) if args.after else None
    if args.json_out:
        payload = {"before": before, "after": after} if after else before
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    text = render_markdown(before, after)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"metrics written to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
