#!/usr/bin/env python3
"""
Idempotent MongoDB backfill for GenA Phase 1.5.

Reconciles datasets.metadata from dataset_versions, assigns question_id,
normalizes provocativeness/difficulty scores, and backfills gate_passed on chunks.

Safe to run repeatedly (dry-run by default).
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
import pymongo

_REPO = Path(__file__).resolve().parent.parent
_DATASET_API = _REPO / "dataset_api"
if str(_DATASET_API) not in sys.path:
    sys.path.insert(0, str(_DATASET_API))

from config import (  # noqa: E402
    MONGO_DB_NAME,
    MONGO_HOST,
    MONGO_PASSWORD,
    MONGO_PORT,
    MONGO_USERNAME,
    build_mongo_uri,
)

logger = logging.getLogger(__name__)

DEFAULT_SENSITIVITY = "2"


def get_db() -> pymongo.database.Database:
    # Credentials are used only when both are set; an empty pair means no auth
    uri = build_mongo_uri(
        MONGO_USERNAME if MONGO_PASSWORD else "", MONGO_PASSWORD, MONGO_HOST, MONGO_PORT
    )
    client = pymongo.MongoClient(uri)
    return client[MONGO_DB_NAME]


def normalize_score(value: Any, default: str = DEFAULT_SENSITIVITY) -> Optional[str]:
    """Map legacy 0 / bool / numeric scores to string values on the 1–3 scale."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return "1" if value else default

    if isinstance(value, (int, float)):
        n = int(value)
        if n == 0:
            return default
        if 1 <= n <= 3:
            return str(n)
        return default

    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a"):
        return None
    if s in ("0", "0.0"):
        return default
    try:
        n = int(float(s))
        if n == 0:
            return default
        if 1 <= n <= 3:
            return str(n)
    except (TypeError, ValueError):
        pass
    return s


def _gate_score(value: Any) -> Optional[int]:
    """Normalize legacy gate criterion values to 0/1 (matches dataset_editor)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) == 1 else 0
    if isinstance(value, list) and value:
        return _gate_score(value[0])
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true"):
            return 1
        if s in ("0", "false"):
            return 0
    return None


def normalize_question_scores(q: Dict[str, Any]) -> bool:
    """Normalize provocativeness/difficulty aliases in-place. Returns True if changed."""
    changed = False

    prov_raw = q.get("provocativeness")
    if prov_raw is None or prov_raw == "" or str(prov_raw).strip() in ("0", "0.0"):
        alt = q.get("sensitivity_level")
        if alt not in (None, "", "0", "0.0"):
            prov_raw = alt
        elif prov_raw is None or prov_raw == "":
            prov_raw = q.get("sensitivity_level")
    prov = normalize_score(prov_raw, DEFAULT_SENSITIVITY)
    if prov is not None:
        if q.get("provocativeness") != prov:
            q["provocativeness"] = prov
            changed = True
        if q.get("sensitivity_level") != prov:
            q["sensitivity_level"] = prov
            changed = True
    elif not q.get("provocativeness") and not q.get("sensitivity_level"):
        q["provocativeness"] = DEFAULT_SENSITIVITY
        changed = True

    diff_raw = q.get("difficulty")
    if diff_raw is None or diff_raw == "" or str(diff_raw).strip() in ("0", "0.0"):
        alt = q.get("difficulty_level")
        if alt not in (None, "", "0", "0.0"):
            diff_raw = alt
        elif diff_raw is None or diff_raw == "":
            diff_raw = q.get("difficulty_level")
    diff = normalize_score(diff_raw)
    if diff is not None:
        if q.get("difficulty") != diff:
            q["difficulty"] = diff
            changed = True
        if q.get("difficulty_level") != diff:
            q["difficulty_level"] = diff
            changed = True

    return changed


def ensure_question_ids(dataset_id: str, questions: List[Dict[str, Any]]) -> int:
    """Assign UUID question_id where missing. Returns count of ids added."""
    added = 0
    for q in questions:
        if not q.get("question_id"):
            q["question_id"] = str(uuid.uuid4())
            added += 1
        if not q.get("idempotency_key") and q.get("chunk_id") is not None and q.get("question_type"):
            q["idempotency_key"] = f"{q['chunk_id']}:{q['question_type']}"
    return added


def infer_gate_passed(chunk: Dict[str, Any]) -> bool:
    if chunk.get("gate_passed") is not None:
        return bool(chunk["gate_passed"])
    gate_result = chunk.get("gate_result") or {}
    if not gate_result:
        return True
    passed = gate_result.get("passed")
    if passed is not None:
        return bool(passed)
    reason = gate_result.get("rejection_reason")
    if reason:
        if reason == "gate_disabled_by_pipeline_mode":
            return True
        return False
    c1 = _gate_score(gate_result.get("c1_chunk_informative"))
    c2 = _gate_score(gate_result.get("c2_chunk_reference_clarity"))
    if c1 is not None or c2 is not None:
        return (c1 or 0) == 1 and (c2 or 0) == 1
    return True


def reconcile_metadata(
    db,
    dataset_id: str,
    current_version: int,
    dry_run: bool,
) -> Tuple[bool, str]:
    """Copy dataset_versions.metadata → datasets.metadata for current version."""
    version_doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": current_version},
        {"metadata": 1},
    )
    if not version_doc:
        return False, "version_not_found"

    version_meta = version_doc.get("metadata") or {}
    dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)}, {"metadata": 1})
    if not dataset:
        return False, "dataset_not_found"

    dataset_meta = dataset.get("metadata") or {}
    if dataset_meta == version_meta:
        return False, "already_synced"

    if not dry_run:
        db.datasets.update_one(
            {"_id": ObjectId(dataset_id)},
            {"$set": {"metadata": version_meta}},
        )
    return True, "reconciled"


def backfill_dataset_version(
    db,
    dataset_id: str,
    version: int,
    dry_run: bool,
    ids_only: bool = False,
) -> Dict[str, int]:
    """Repair a version in place.

    ``ids_only`` restricts the run to identity fields (``question_id`` /
    ``idempotency_key``), which is what manual editing needs. Score
    normalization also substitutes defaults where a model produced none, so it
    is worth running separately and deliberately.
    """
    stats = {"question_ids": 0, "scores": 0, "counters": 0, "failed": 0}
    version_doc = db.dataset_versions.find_one({"dataset_id": dataset_id, "version": version})
    if not version_doc:
        return stats

    questions = list(version_doc.get("questions") or [])
    changed = False

    qids_added = ensure_question_ids(dataset_id, questions)
    if qids_added:
        stats["question_ids"] = qids_added
        changed = True

    score_changes = 0
    if not ids_only:
        for q in questions:
            if normalize_question_scores(q):
                score_changes += 1
        if score_changes:
            stats["scores"] = score_changes
            changed = True

    metadata = dict(version_doc.get("metadata") or {})
    total = len(questions)
    if not ids_only and metadata.get("total_questions_generated") != total:
        metadata["total_questions_generated"] = total
        stats["counters"] = 1
        changed = True

    if changed and not dry_run:
        try:
            db.dataset_versions.update_one(
                {"dataset_id": dataset_id, "version": version},
                {"$set": {"questions": questions, "metadata": metadata}},
            )
        except Exception as exc:  # pragma: no cover - depends on live data size
            # A version already near the 16 MB document limit cannot grow to fit
            # the new ids. Report it and keep going instead of aborting the run.
            logger.error(
                "Version %s of dataset %s could not be updated (%s); "
                "it likely exceeds the MongoDB document limit",
                version,
                dataset_id,
                exc,
            )
            stats["failed"] = 1
            stats["question_ids"] = 0
            stats["scores"] = 0
            stats["counters"] = 0
    return stats


def chunks_needing_gate_passed_filter(dataset_id: str) -> Dict[str, Any]:
    """Chunks missing gate_passed (absent or explicitly null)."""
    return {
        "dataset_id": dataset_id,
        "$or": [
            {"gate_passed": {"$exists": False}},
            {"gate_passed": None},
        ],
    }


def backfill_chunks(db, dataset_id: str, dry_run: bool) -> int:
    updated = 0
    for chunk in db.chunks.find(chunks_needing_gate_passed_filter(dataset_id)):
        gate_passed = infer_gate_passed(chunk)
        updated += 1
        if not dry_run:
            db.chunks.update_one(
                {"_id": chunk["_id"]},
                {"$set": {"gate_passed": gate_passed}},
            )
    return updated


def run_backfill(
    dry_run: bool = True,
    limit: Optional[int] = None,
    ids_only: bool = False,
) -> Dict[str, Any]:
    db = get_db()
    query = {}
    cursor = db.datasets.find(query, {"_id": 1, "current_version": 1})
    if limit:
        cursor = cursor.limit(limit)

    summary: Dict[str, Any] = {
        "dry_run": dry_run,
        "datasets_scanned": 0,
        "versions_scanned": 0,
        "metadata_reconciled": 0,
        "question_ids_added": 0,
        "scores_normalized": 0,
        "counters_fixed": 0,
        "versions_failed": 0,
        "chunks_gate_passed": 0,
    }

    for dataset in cursor:
        summary["datasets_scanned"] += 1
        dataset_id = str(dataset["_id"])
        version = dataset.get("current_version", 1)

        if not ids_only:
            reconciled, _ = reconcile_metadata(db, dataset_id, version, dry_run)
            if reconciled:
                summary["metadata_reconciled"] += 1

        # Every version, not just the current one: manual editing addresses
        # questions by question_id in whichever version the user is viewing,
        # so an id-less older version cannot be edited at all.
        for vdoc in db.dataset_versions.find(
            {"dataset_id": dataset_id}, {"version": 1}
        ):
            vstats = backfill_dataset_version(
                db, dataset_id, vdoc["version"], dry_run, ids_only=ids_only
            )
            summary["versions_scanned"] += 1
            summary["question_ids_added"] += vstats["question_ids"]
            summary["scores_normalized"] += vstats["scores"]
            summary["counters_fixed"] += vstats["counters"]
            summary["versions_failed"] += vstats.get("failed", 0)

        if not ids_only:
            chunk_updates = backfill_chunks(db, dataset_id, dry_run)
            summary["chunks_gate_passed"] += chunk_updates

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="GenA Phase 1.5 idempotent data backfill")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to MongoDB (default is dry-run)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max datasets to process")
    parser.add_argument(
        "--ids-only",
        action="store_true",
        help=(
            "Only assign question_id/idempotency_key (what manual editing needs); "
            "skip score normalization, metadata and chunk repairs"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    dry_run = not args.apply
    if dry_run:
        logger.info("Dry-run mode — pass --apply to persist changes")

    summary = run_backfill(dry_run=dry_run, limit=args.limit, ids_only=args.ids_only)
    logger.info("Backfill summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
