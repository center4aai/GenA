"""Golden-снимок нормативного чанкинга: путь rules для DOCX не должен меняться.

Эталон: число чанков, fragment_id и sha256 от combined_text каждого чанка на
tests/data/Family_code_Russian_Federation_1-4.docx. Любое изменение упаковщика
или извлечения текста, затронувшее нормативку, провалит тест.

Обновить эталон осознанно (после ревью диффа чанков):
    GENA_UPDATE_GOLDEN=1 python -m pytest tests/test_normative_golden.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CHUNKER = _REPO / "chunker"
if str(_CHUNKER) not in sys.path:
    sys.path.insert(0, str(_CHUNKER))

import main as chunker_mod  # noqa: E402

DOC = _REPO / "tests" / "data" / "Family_code_Russian_Federation_1-4.docx"
GOLDEN = _REPO / "tests" / "data" / "golden" / "family_code_rules.json"
UPDATE = os.getenv("GENA_UPDATE_GOLDEN", "").strip() in ("1", "true", "yes")

DOC_TYPE_INFO = {
    "document_type": "Кодекс", "document_name": "Семейный кодекс", "confidence": 1.0,
    "description": "golden", "key_indicators": [], "titles_count": 0,
}


def _snapshot() -> dict:
    pytest.importorskip("docx")
    with patch.object(chunker_mod, "identify_document_type", return_value=DOC_TYPE_INFO):
        chunks, _info = chunker_mod.process_document_to_chunks(
            str(DOC), min_size=50, document_kind="normative", chunking_method="rules"
        )
    return {
        "document": DOC.name,
        "num_chunks": len(chunks),
        "chunks": [
            {
                "fragment_id": c.get("fragment_id"),
                "sha256": hashlib.sha256(((c.get("fragment_data") or {}).get("combined_text") or "").encode("utf-8")).hexdigest(),
                "chars": len((c.get("fragment_data") or {}).get("combined_text") or ""),
            }
            for c in chunks
        ],
    }


@pytest.mark.skipif(not DOC.is_file(), reason="fixture docx missing")
def test_normative_rules_chunking_matches_golden():
    actual = _snapshot()
    if UPDATE or not GOLDEN.is_file():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"golden snapshot was missing and has been written to {GOLDEN}; commit it and re-run")
        return
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual["num_chunks"] == expected["num_chunks"], (
        f"normative chunk count changed: {expected['num_chunks']} → {actual['num_chunks']} "
        f"(GENA_UPDATE_GOLDEN=1 to accept after review)"
    )
    changed = [
        (e["fragment_id"], e["chars"], a["chars"])
        for e, a in zip(expected["chunks"], actual["chunks"])
        if e["sha256"] != a["sha256"] or e["fragment_id"] != a["fragment_id"]
    ]
    assert not changed, (
        f"{len(changed)} normative chunks changed, e.g. {changed[:5]} "
        f"(fragment_id, chars before, chars after); GENA_UPDATE_GOLDEN=1 to accept after review"
    )
