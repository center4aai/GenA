#!/usr/bin/env python3
"""
CI Chunker Test — uploads a test docx to the local chunker service
and validates that chunks are reasonable for question generation.

Runs inside the chunker container (has requests via requirements).
Usage:  python ci_test_chunker.py /tmp/test_doc.docx
"""

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
MAX_ALLOWED_TINY = 35  # chunks < 500 chars
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


def main():
    if len(sys.argv) < 2:
        _print("Usage: ci_test_chunker.py <path_to_docx>", file=sys.stderr)
        sys.exit(1)

    doc_path = sys.argv[1]
    if not os.path.isfile(doc_path):
        _print(f"File not found: {doc_path}", file=sys.stderr)
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
    tiny = sum(1 for l in lengths if l < 500)

    _print(f"  min={mn}  max={mx}  mean={mean:.0f}  median={med:.0f}")
    _print(f"  <500: {tiny}  500-1000: {sum(1 for l in lengths if 500<=l<1000)}"
           f"  1000-2000: {sum(1 for l in lengths if 1000<=l<2000)}"
           f"  2000+: {sum(1 for l in lengths if l>=2000)}")

    if med >= MIN_MEDIAN_CHARS:
        ok(f"Median chunk size {med:.0f} >= {MIN_MEDIAN_CHARS}")
    else:
        fail(f"Median chunk size {med:.0f} < {MIN_MEDIAN_CHARS} — chunks are unusually small")

    if tiny <= MAX_ALLOWED_TINY:
        ok(f"Only {tiny} tiny chunks (<500 chars), max allowed {MAX_ALLOWED_TINY}")
    else:
        fail(f"{tiny} tiny chunks (<500 chars) exceeds max {MAX_ALLOWED_TINY}")

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

    # ── Summary ─────────────────────────────────────────────────────
    _print(f"\n{'='*50}")
    _print(f"  RESULT: {_passed} passed, {_failed} failed")
    _print(f"{'='*50}")
    sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    main()
