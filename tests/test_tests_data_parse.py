"""Parse smoke tests for every file under tests/data.

Default run uses page-sliced samples for large PDFs (fast). Set
GENA_FULL_DATA_PARSE=1 to process full files (slow; encyclopaedia ~770 pages).

Filenames may be NFD-normalized (macOS-style), so the encyclopaedia path is
resolved by glob rather than a hardcoded NFC literal.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
_DATA = _REPO / "tests" / "data"
if not _CHUNKER.is_dir():
    # Inside chunker image: code at /app, tests at /app/tests
    _CHUNKER = _REPO
    _DATA = Path(__file__).resolve().parent / "data"
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

# Optional deps for PDF slicing — skip sample mode if missing
try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError:
        PdfReader = None  # type: ignore
        PdfWriter = None  # type: ignore


import main as chunker_mod  # noqa: E402

FULL_PARSE = os.getenv("GENA_FULL_DATA_PARSE", "").strip() in ("1", "true", "yes")

# Soft hyphen with lowercase continuation — must be joined by prose cleanup.
# Lower→UPPER residuals are usually column-break artifacts (continuation lost).
_HYPHEN_RESIDUAL_RE = re.compile(
    r"[А-Яа-яЁёA-Za-z][ \t]*-[ \t]*\n[ \t]*[а-яёa-z]"
)
_JUNK_MARKERS = (".indd", "УДК ", "ББК ", "(cid:")


def _resolve(name_glob: str) -> Optional[Path]:
    matches = sorted(_DATA.glob(name_glob))
    return matches[0] if matches else None


def _slice_pdf(src: Path, pages: List[int], dest: Path) -> Path:
    assert PdfReader is not None and PdfWriter is not None
    reader = PdfReader(str(src))
    writer = PdfWriter()
    n = len(reader.pages)
    for i in pages:
        if 0 <= i < n:
            writer.add_page(reader.pages[i])
    with dest.open("wb") as fh:
        writer.write(fh)
    return dest


def _sum_tree_content(node: Dict) -> int:
    total = len((node.get("content") or "").strip())
    for ch in node.get("children") or []:
        if isinstance(ch, dict):
            total += _sum_tree_content(ch)
    return total


def _assert_chunks_ok(
    chunks: List[Dict], kind: str, tree: Dict, label: str, stats: Optional[Dict] = None
) -> None:
    assert chunks, f"{label}: no chunks produced"
    blob = "\n".join(
        (c.get("fragment_data") or {}).get("combined_text") or "" for c in chunks
    )
    for marker in _JUNK_MARKERS:
        assert marker not in blob, f"{label}: junk marker {marker!r} in chunks"

    # Residual soft-hyphen check only for prose kinds (they run de-hyphenation).
    # Check per-chunk so joining blobs cannot invent false cross-chunk matches.
    if kind in chunker_mod.PROSE_KINDS:
        residual_samples: List[str] = []
        for c in chunks:
            text = (c.get("fragment_data") or {}).get("combined_text") or ""
            residual_samples.extend(_HYPHEN_RESIDUAL_RE.findall(text))
        assert len(residual_samples) == 0, (
            f"{label}: {len(residual_samples)} residual soft-hyphen breaks remain, "
            f"e.g. {residual_samples[:3]}"
        )

    if kind in chunker_mod.PROSE_KINDS:
        source_chars = _sum_tree_content(tree)
        chunk_chars = sum(
            len((c.get("fragment_data") or {}).get("content") or "") for c in chunks
        )
        # Задания, оглавление, выходные данные, огрызки выбрасываются
        # намеренно и учитываются в stats — это не потеря текста.
        dropped_chars = sum(
            int(v.get("chars", 0))
            for v in ((stats or {}).get("dropped_blocks") or {}).values()
        )
        if source_chars > 0:
            coverage = 100.0 * (chunk_chars + dropped_chars) / source_chars
            assert coverage >= 95.0, (
                f"{label}: coverage {coverage:.1f}% < 95% "
                f"({chunk_chars}+{dropped_chars} dropped / {source_chars})"
            )
        for c in chunks:
            fd = c.get("fragment_data") or {}
            content = fd.get("content") or ""
            limit = (
                chunker_mod.TEXTBOOK_LIST_HARD_CAP
                if fd.get("block_type") == "list"
                else chunker_mod.TEXTBOOK_MAX_CHARS
            )
            assert len(content) <= limit + 50, (
                f"{label}: content len {len(content)} exceeds limit for {fd.get('block_type')}"
            )


def _cases() -> List[Tuple[str, str, str, Optional[List[int]]]]:
    """(label, glob, kind, page_indices_or_None_for_full)."""
    cases: List[Tuple[str, str, str, Optional[List[int]]]] = [
        ("family_code", "Family_code_Russian_Federation_1-4.docx", "normative", None),
        ("uk_rf", "УК РФ part1.docx", "normative", None),
        ("constitution", "Конституция РФ index.pdf", "normative", None),
        # Синтетика (tests/fixtures/synthetic/build.py) — есть и в публичной копии.
        ("synthetic_textbook", "synthetic/textbook_*.pdf", "textbook", None),
        ("synthetic_encyclopedia", "synthetic/encyclopedia_*.pdf", "encyclopedia", None),
        ("synthetic_normative", "synthetic/normative_*.docx", "normative", None),
    ]
    # Large PDFs: sample by default
    large = [
        ("obsh9", "obsestvoznanie-9-klass-medvedev.pdf", "textbook", list(range(20, 60))),
        ("obsh10", "obsestvoznanie-10-klass-medvedev.pdf", "textbook", list(range(20, 60))),
        (
            "medinsky",
            "Medinskiy_Torkunov_Istoria_Rossii_11_kl_Bazovy_2023.pdf",
            "textbook",
            list(range(30, 70)),
        ),
        (
            "bre28",
            "*нциклопедия*Том_28.pdf",
            "encyclopedia",
            list(range(30, 70)),
        ),
    ]
    for label, glob, kind, pages in large:
        if FULL_PARSE:
            cases.append((label, glob, kind, None))
        else:
            cases.append((label, glob, kind, pages))
    return cases


@pytest.mark.parametrize(
    "label,glob,kind,pages",
    _cases(),
    ids=[c[0] for c in _cases()],
)
def test_tests_data_file_parses(label: str, glob: str, kind: str, pages: Optional[List[int]]):
    path = _resolve(glob)
    if path is None or not path.is_file():
        pytest.skip(f"fixture not found: {_DATA / glob}")

    work = path
    tmp: Optional[Path] = None
    try:
        if pages is not None:
            if PdfReader is None:
                pytest.skip("pypdf not installed; cannot slice PDF sample")
            fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            tmp = Path(tmp_name)
            _slice_pdf(path, pages, tmp)
            work = tmp

        # Skip LLM type ID for speed/stability in smoke tests
        from unittest.mock import patch

        fake_type = {
            "document_type": "unknown",
            "document_name": label,
            "confidence": 0.0,
            "description": "smoke",
            "key_indicators": [],
            "titles_count": 0,
        }
        stats: Dict = {}
        with patch.object(chunker_mod, "identify_document_type", return_value=fake_type):
            chunks, info = chunker_mod.process_document_to_chunks(
                str(work), min_size=50, document_kind=kind, stats=stats
            )

        # Rebuild tree for coverage (process_document_to_chunks does not return it)
        from docx2json_outline import extract_outline_from_document

        tree = extract_outline_from_document(str(work), document_kind=kind)
        # Unwrap if needed
        if isinstance(tree, dict) and "children" not in tree and "tree" in tree:
            tree = tree["tree"]

        _assert_chunks_ok(chunks, kind, tree, label, stats)

        if kind == "encyclopedia":
            titles = [
                (c.get("fragment_data") or {}).get("title") or "" for c in chunks
            ]
            titled = [t for t in titles if t]
            unique = {t for t in titled}
            assert len(unique) >= 10, (
                f"{label}: expected many entry titles, got {sorted(unique)[:20]}"
            )
            # Stress digits should not dominate titles
            assert sum(1 for t in titled if re.search(r'[А-ЯЁа-яё][29][А-ЯЁа-яё]', t)) < len(titled) * 0.1
            # Reject obvious editorial-board / debris titles
            assert not any(re.match(r'^(?:[А-ЯЁ]\.\s*){1,3}[А-ЯЁ]', t) for t in unique)

        if kind == "textbook":
            breadcrumb_hits = 0
            for c in chunks:
                bc = ((c.get("hierarchy_context") or {}).get("breadcrumb_text") or "")
                if "Глава" in bc or "§" in bc:
                    breadcrumb_hits += 1
            # Sample pages may or may not hit a chapter — soft check on full only
            if FULL_PARSE:
                assert breadcrumb_hits > 0, f"{label}: no Глава/§ breadcrumbs"

    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)
