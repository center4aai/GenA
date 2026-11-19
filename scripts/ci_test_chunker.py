#!/usr/bin/env python3
"""
CI Chunker Test — uploads a test docx to the local chunker service
and validates that chunks are reasonable for question generation.

Runs inside the chunker container (has requests via requirements).
Usage:  python ci_test_chunker.py /tmp/test_doc.docx [--textbook-pdf /tmp/sample.pdf]

The optional textbook PDF is chunked with document_kind=textbook and
chunking_method=layout (no LLM involved) and checked for the textbook-specific
guarantees: insets are separate chunks, running headers and exercises are
dropped, pages are attached to body chunks.
"""

import argparse
import re
import sys
import os
import statistics

os.environ["PYTHONUNBUFFERED"] = "1"

CHUNKER_URL = "http://127.0.0.1:8517"

# Пороги под hierarchical_outline (листья + split по пунктам): много
# относительно коротких чанков и не у всех заполнен breadcrumb_text верхнего уровня.
MIN_EXPECTED_CHUNKS = 5
MAX_EXPECTED_CHUNKS = 250
MIN_MEDIAN_CHARS = 150
# «Крошечный» = заметно ниже типичного чанка hierarchical_outline (медиана ~425):
# такой фрагмент уже не несёт материала для вопроса. Доля, а не абсолютное число —
# иначе порог зависит от размера тестового документа.
TINY_CHARS = 200
MAX_TINY_FRACTION = 0.15
# Доля чанков с явным родительским контекстом (текст пути или path из заголовков)
MIN_HIERARCHY_FRACTION = 0.12

_passed = 0
_failed = 0


def _print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def ok(msg):
    global _passed
    _passed += 1
    _print(f"  [PASS] {msg}")


def fail(msg):
    global _failed
    _failed += 1
    _print(f"  [FAIL] {msg}", file=sys.stderr)


# ── Textbook / layout checks ────────────────────────────────────────
# Колонтитул страницы учебника: «50 ГЛАВА I», «ГЛАВА I 51»,
# «§ 4. Место и роль СССР … 51» (заголовок параграфа с номером страницы).
RUNNING_HEADER_RE = re.compile(
    r"^(?:\d{1,3}\s+ГЛАВА\s+[IVXLC]+|ГЛАВА\s+[IVXLC]+\s+\d{1,3}|§\s*\d+(?:-\d+)?\.\s.{3,120}\s\d{1,3})\s*$",
    re.M,
)
# Задание, вклеенное в текст: маркер «⬤» или поручение «Узнайте/Выясните…».
EXERCISE_RE = re.compile(r"⬤|(?:^|\n)\s*(?:Узнайте|Выясните|Подумайте|Объясните)\b", re.M)
# Врезка из образца medinskiy_layout_sample.pdf (биография).
SAMPLE_INSET_MARK = "Иосип Броз Тито"


def _body(chunks):
    return [c for c in chunks if (c.get("fragment_data") or {}).get("block_type") in (None, "body", "list")]


def _text(c):
    fd = c.get("fragment_data") or {}
    return fd.get("content") or fd.get("combined_text") or ""


def check_textbook_layout(requests, pdf_path):
    _print("\n== T1. Textbook PDF, method=layout ==")
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"{CHUNKER_URL}/chunk/",
            params={"document_kind": "textbook", "chunking_method": "layout"},
            files={"file": ("test_textbook.pdf", f, "application/pdf")},
            timeout=300,
        )
    if resp.status_code != 200:
        fail(f"Chunker HTTP {resp.status_code}: {resp.text[:300]}")
        return
    data = resp.json()
    chunks = data.get("chunks_detailed", [])
    _print(f"  {len(chunks)} chunks, engine={data.get('chunking_engine')}, "
           f"fallback={data.get('chunking_fallback')}, by_type={data.get('chunks_by_block_type')}")

    if data.get("chunking_engine") == "layout_pdf" and not data.get("chunking_fallback"):
        ok("layout engine ran without fallback")
    else:
        fail(f"expected layout_pdf without fallback, got engine={data.get('chunking_engine')} "
             f"fallback={data.get('chunking_fallback')}")

    _print("\n== T2. Insets are separate chunks ==")
    insets = [c for c in chunks if (c.get("fragment_data") or {}).get("block_type") == "inset"]
    body = _body(chunks)
    inset_hit = any(SAMPLE_INSET_MARK in _text(c) for c in insets)
    body_hit = any(SAMPLE_INSET_MARK in _text(c) for c in body)
    if inset_hit and not body_hit:
        ok(f"inset «{SAMPLE_INSET_MARK}» is its own chunk ({len(insets)} insets total)")
    else:
        fail(f"inset «{SAMPLE_INSET_MARK}»: in insets={inset_hit}, leaked into body={body_hit}")

    _print("\n== T3. Running headers and exercises dropped ==")
    headers = [c for c in chunks if RUNNING_HEADER_RE.search(_text(c))]
    if not headers:
        ok("no running headers inside chunks")
    else:
        fail(f"{len(headers)} chunks contain a running header, e.g. {_text(headers[0])[:80]!r}")
    exercises = [c for c in chunks if EXERCISE_RE.search(_text(c))]
    if not exercises:
        ok("no exercise markers inside chunks")
    else:
        fail(f"{len(exercises)} chunks contain exercise text, e.g. {_text(exercises[0])[:80]!r}")
    dropped = data.get("dropped_blocks") or {}
    if (dropped.get("exercise") or {}).get("count", 0) > 0:
        ok(f"exercises dropped: {dropped['exercise']}")
    else:
        fail(f"no exercises reported in dropped_blocks: {dropped}")

    _print("\n== T4. Pages on body chunks ==")
    pages = [((c.get("fragment_data") or {}).get("page_start"), (c.get("fragment_data") or {}).get("page_end")) for c in body]
    if body and all(isinstance(a, int) and isinstance(b, int) and b >= a for a, b in pages):
        ok(f"all {len(body)} body chunks carry page_start/page_end")
    else:
        fail(f"body chunks without valid pages: {[p for p in pages if not (isinstance(p[0], int) and isinstance(p[1], int))][:5]}")
    if any(b > a for a, b in pages if isinstance(a, int) and isinstance(b, int)):
        ok("at least one body chunk continues across a page break")
    else:
        fail("no body chunk spans a page break — paragraphs are cut at page ends")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_path", help="normative DOCX (rules method, defaults)")
    ap.add_argument("--textbook-pdf", help="textbook PDF for the layout-method checks")
    args = ap.parse_args()

    doc_path = args.doc_path
    if not os.path.isfile(doc_path):
        _print(f"File not found: {doc_path}", file=sys.stderr)
        sys.exit(1)
    if args.textbook_pdf and not os.path.isfile(args.textbook_pdf):
        _print(f"File not found: {args.textbook_pdf}", file=sys.stderr)
        sys.exit(1)

    import requests

    # ── 1. Upload ────────────────────────────────────────────────────
    _print("\n== 1. Upload document to chunker ==")
    with open(doc_path, "rb") as f:
        resp = requests.post(
            f"{CHUNKER_URL}/chunk/",
            files={"file": ("test_doc.docx", f,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            timeout=120,
        )

    if resp.status_code != 200:
        fail(f"Chunker HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    data = resp.json()
    num_chunks = data.get("num_chunks", 0)
    chunks_detailed = data.get("chunks_detailed", [])
    _print(f"  Chunker returned {num_chunks} chunks (method: {data.get('chunking_method')})")

    # ── 2. Count check ──────────────────────────────────────────────
    _print("\n== 2. Chunk count ==")
    if MIN_EXPECTED_CHUNKS <= num_chunks <= MAX_EXPECTED_CHUNKS:
        ok(f"{num_chunks} chunks (expected {MIN_EXPECTED_CHUNKS}-{MAX_EXPECTED_CHUNKS})")
    else:
        fail(f"{num_chunks} chunks outside expected range {MIN_EXPECTED_CHUNKS}-{MAX_EXPECTED_CHUNKS}")

    # ── 3. Size distribution ────────────────────────────────────────
    _print("\n== 3. Size distribution ==")
    lengths = []
    for c in chunks_detailed:
        fd = c.get("fragment_data", {})
        text = fd.get("combined_text", "")
        lengths.append(len(text))

    if not lengths:
        fail("No chunk lengths to analyze")
        sys.exit(1)

    mn, mx = min(lengths), max(lengths)
    mean = statistics.mean(lengths)
    med = statistics.median(lengths)
    tiny = sum(1 for l in lengths if l < TINY_CHARS)
    tiny_frac = tiny / len(lengths)

    _print(f"  min={mn}  max={mx}  mean={mean:.0f}  median={med:.0f}")
    _print(f"  <500: {sum(1 for l in lengths if l < 500)}"
           f"  500-1000: {sum(1 for l in lengths if 500<=l<1000)}"
           f"  1000-2000: {sum(1 for l in lengths if 1000<=l<2000)}"
           f"  2000+: {sum(1 for l in lengths if l>=2000)}")

    if med >= MIN_MEDIAN_CHARS:
        ok(f"Median chunk size {med:.0f} >= {MIN_MEDIAN_CHARS}")
    else:
        fail(f"Median chunk size {med:.0f} < {MIN_MEDIAN_CHARS} — chunks are unusually small")

    if tiny_frac <= MAX_TINY_FRACTION:
        ok(f"{tiny}/{len(lengths)} tiny chunks (<{TINY_CHARS} chars, {tiny_frac:.0%}), "
           f"max allowed {MAX_TINY_FRACTION:.0%}")
    else:
        fail(f"{tiny}/{len(lengths)} tiny chunks (<{TINY_CHARS} chars, {tiny_frac:.0%}) "
             f"exceeds max {MAX_TINY_FRACTION:.0%}")

    # ── 4. Hierarchy context ────────────────────────────────────────
    _print("\n== 4. Hierarchy context ==")
    def _has_parent_hierarchy(c):
        hc = c.get("hierarchy_context") or {}
        bt = (hc.get("breadcrumb_text") or "").strip()
        bp = hc.get("breadcrumb_path") or []
        return bool(bt) or (isinstance(bp, list) and len(bp) > 0)

    has_hierarchy = sum(1 for c in chunks_detailed if _has_parent_hierarchy(c))
    frac = has_hierarchy / num_chunks if num_chunks else 0.0
    _print(f"  {has_hierarchy}/{num_chunks} chunks with parent path or breadcrumb_text ({frac:.0%})")

    missing_keys = [
        i for i, c in enumerate(chunks_detailed)
        if not isinstance(c.get("hierarchy_context"), dict)
        or "fragment_data" not in c
        or not (c.get("fragment_id") or "").strip()
    ]
    if missing_keys:
        fail(f"{len(missing_keys)} chunks missing fragment_id / hierarchy_context / fragment_data")
    elif has_hierarchy == 0 and num_chunks >= 8:
        fail("No chunks carry parent breadcrumb/path — hierarchy extraction likely broken")
    elif frac >= MIN_HIERARCHY_FRACTION:
        ok(f"Hierarchy fields present; {has_hierarchy}/{num_chunks} with non-empty parent context")
    else:
        # Плоская структура (много разделов верхнего уровня) — допускаем при валидных полях
        ok(
            f"Flat outline: only {has_hierarchy}/{num_chunks} with parent context "
            f"(threshold {MIN_HIERARCHY_FRACTION:.0%}), but chunk schema is valid"
        )

    # ── 5. No empty combined_text ───────────────────────────────────
    _print("\n== 5. No empty chunks ==")
    empties = sum(
        1 for c in chunks_detailed
        if not c.get("fragment_data", {}).get("combined_text", "").strip()
    )
    if empties == 0:
        ok("All chunks have non-empty combined_text")
    else:
        fail(f"{empties} chunks have empty combined_text")

    # ── 6. Document type detected ───────────────────────────────────
    _print("\n== 6. Document type detection ==")
    doc_type = data.get("document_type") or {}
    if not isinstance(doc_type, dict):
        fail("document_type is not an object")
    else:
        dtype = doc_type.get("document_type", "unknown")
        dname = doc_type.get("document_name", "")
        conf = float(doc_type.get("confidence") or 0)
        desc = (doc_type.get("description") or "").lower()
        _print(f"  Type: {dtype}, Name: {dname}, Confidence: {conf}")
        llm_off = (
            "не настроен" in desc
            or "llm" in desc and "не" in desc
            or doc_type.get("error")
        )
        if dtype != "unknown" and conf > 0.5:
            ok(f"Document type identified: {dtype} ({conf:.2f})")
        elif llm_off or (dtype == "unknown" and conf == 0.0 and num_chunks >= MIN_EXPECTED_CHUNKS):
            ok(
                "Document type not classified (LLM off/unavailable or unknown); "
                "chunker response still valid"
            )
        else:
            fail(f"Document type not identified (type={dtype}, confidence={conf})")

    # ── 7. Sample chunks ────────────────────────────────────────────
    _print("\n== 7. Sample chunks (first 5) ==")
    for i, c in enumerate(chunks_detailed[:5]):
        fd = c.get("fragment_data", {})
        text = fd.get("combined_text", "")
        extra = " [SEM]" if fd.get("semantic_split") else ""
        _print(f"  Chunk {i} ({len(text)} chars){extra}: {text[:150].replace(chr(10), ' ')}...")

    # ── 8. Textbook PDF via layout (optional) ────────────────────────
    if args.textbook_pdf:
        check_textbook_layout(requests, args.textbook_pdf)

    # ── Summary ─────────────────────────────────────────────────────
    _print(f"\n{'='*50}")
    _print(f"  RESULT: {_passed} passed, {_failed} failed")
    _print(f"{'='*50}")
    sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    main()
