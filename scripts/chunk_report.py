#!/usr/bin/env python3
"""Отчёт по чанкингу документа для тестировщиков: блоки, что выброшено, чанки.

Запуск из образа чанкера (там есть pdfplumber/markitdown):

    docker run --rm -v $PWD:/repo -w /repo --entrypoint python gena_dev-chunker \\
        scripts/chunk_report.py tests/data/obsch9_layout_sample.pdf --kind textbook --method layout --out report.md

Показывает профиль вёрстки (для method=layout), блоки по страницам с типами,
что и почему выброшено (задания, аппарат, таблицы, огрызки), итоговые чанки с
block_type и страницами и гистограмму длин. Метод llm требует настроенной
модели (MODEL_ENDPOINTS / LLM_URL_MODEL) и делает десятки вызовов.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
for cand in (_HERE.parent / "chunker", _HERE.parent):
    if (cand / "main.py").is_file() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

import main as chunker_mod  # noqa: E402


def _blocks_report(path: str, out: list) -> None:
    import textbook_layout as tl

    stats = {}
    try:
        blocks, prof = tl.extract_layout_blocks(path, stats)
    except tl.LayoutUnavailable as exc:
        out.append(f"\n## Layout: недоступен ({exc})\n")
        return
    out.append("\n## Профиль вёрстки\n")
    out.append("```json\n" + json.dumps(stats["layout"], ensure_ascii=False, indent=1) + "\n```\n")
    out.append("\n## Блоки по страницам\n")
    page = None
    for b in blocks:
        if b.kind == "junk":
            continue
        if b.page_start != page:
            page = b.page_start
            out.append(f"\n### Страница {page}\n")
        text = (b.text or " ".join(l.text for l in b.lines)).replace("\n", " ⏎ ")
        title = f" «{b.title}»" if b.title and b.kind != "heading" else ""
        out.append(f"- **{b.kind}**{title}: {text[:160]}{'…' if len(text) > 160 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("document")
    ap.add_argument("--kind", default="textbook", choices=sorted(chunker_mod.VALID_DOCUMENT_KINDS))
    ap.add_argument("--method", default="rules", choices=list(chunker_mod.CHUNKING_METHODS))
    ap.add_argument("--min-size", type=int, default=50)
    ap.add_argument("--out", help="Markdown-файл отчёта (по умолчанию stdout)")
    ap.add_argument("--chunks", type=int, default=40, help="сколько чанков показать целиком (0 = все)")
    ap.add_argument("--identify", action="store_true", help="вызывать LLM для определения типа документа")
    ap.add_argument("--json-out", help="сохранить результат в JSON (форма ответа /chunk/ + stats) для scripts/chunk_metrics.py")
    args = ap.parse_args()

    out: list = [f"# Отчёт по чанкингу: {Path(args.document).name}", f"kind={args.kind}, method={args.method}"]
    if args.method == "layout" and args.document.lower().endswith(".pdf"):
        _blocks_report(args.document, out)

    stats: dict = {}
    fake_type = {"document_type": "unknown", "document_name": Path(args.document).stem, "confidence": 0.0,
                 "description": "report", "key_indicators": [], "titles_count": 0}
    ctx = patch.object(chunker_mod, "identify_document_type", return_value=fake_type) if not args.identify else _Null()
    with ctx:
        chunks, _info = chunker_mod.process_document_to_chunks(
            args.document, min_size=args.min_size, document_kind=args.kind, stats=stats, chunking_method=args.method
        )
    out.append("\n## Итог\n")
    out.append(f"- метод: {stats.get('chunking_method')} / движок: {stats.get('chunking_engine')}"
               + (f" / фолбэк: {stats.get('chunking_fallback')}" if stats.get("chunking_fallback") else ""))
    out.append(f"- чанков: {len(chunks)}")
    lens = [len(c['fragment_data']['content']) for c in chunks] or [0]
    out.append(f"- длина content: медиана {int(statistics.median(lens))}, min {min(lens)}, max {max(lens)}")
    out.append("- по типам блоков: " + json.dumps(stats.get("chunks_by_block_type", {}), ensure_ascii=False))
    out.append("- выброшено: " + json.dumps(stats.get("dropped_blocks", {}), ensure_ascii=False))
    for key in ("layout", "semantic", "llm_segmentation"):
        if key in stats and key != "layout":
            out.append(f"- {key}: " + json.dumps(stats[key], ensure_ascii=False))
    buckets = {"<400": 0, "400-1000": 0, "1000-2000": 0, ">2000": 0}
    for n in lens:
        buckets["<400" if n < 400 else "400-1000" if n < 1000 else "1000-2000" if n <= 2000 else ">2000"] += 1
    out.append("- гистограмма длин: " + json.dumps(buckets, ensure_ascii=False))
    out.append("\n## Чанки\n")
    limit = len(chunks) if args.chunks == 0 else args.chunks
    for c in chunks[:limit]:
        fd = c["fragment_data"]
        pages = f" с. {fd.get('page_start')}–{fd.get('page_end')}" if fd.get("page_start") else ""
        out.append(f"\n### {c['fragment_id']} [{fd.get('block_type')}]{pages} ({len(fd['content'])} симв.)\n")
        out.append("```\n" + fd["combined_text"] + "\n```")
    if args.json_out:
        payload = {
            "filename": Path(args.document).name,
            "document_kind": args.kind,
            "num_chunks": len(chunks),
            "chunks": [c["fragment_data"].get("combined_text", "") for c in chunks],
            "chunks_detailed": chunks,
            "chunking_method": stats.get("chunking_method", args.method),
            "chunking_engine": stats.get("chunking_engine"),
            "chunking_fallback": stats.get("chunking_fallback"),
            "dropped_blocks": stats.get("dropped_blocks", {}),
            "chunks_by_block_type": stats.get("chunks_by_block_type", {}),
            "chunking_stats": chunker_mod.build_chunking_stats(stats),
        }
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"json written to {args.json_out}")
    text = "\n".join(out) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"report written to {args.out}: {len(chunks)} chunks")
    else:
        sys.stdout.write(text)
    return 0


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
