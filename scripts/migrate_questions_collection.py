#!/usr/bin/env python3
"""Move questions out of `dataset_versions.questions` into the `questions` collection.

The embedded array grew without bound and carried each question's source chunk,
so a large run walked into MongoDB's hard 16 MB document limit mid-generation
and lost the rest of its questions with only a generic 500 in the log.

Idempotent and resumable: a version whose questions are already migrated is
skipped, and the array is only unset once its rows are in place. Dry-run by
default.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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

logger = logging.getLogger("migrate_questions")

INTERNAL_FIELDS = {"_id", "dataset_id", "version"}


def get_db():
    uri = build_mongo_uri(MONGO_USERNAME, MONGO_PASSWORD, MONGO_HOST, MONGO_PORT)
    return pymongo.MongoClient(uri)[MONGO_DB_NAME]


def ensure_indexes(db) -> None:
    db.questions.create_index([("dataset_id", 1), ("version", 1)])
    db.questions.create_index([("dataset_id", 1), ("version", 1), ("question_id", 1)])
    db.questions.create_index(
        [("dataset_id", 1), ("version", 1), ("idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )


def rows_for(questions: List[Dict[str, Any]], dataset_id: str, version: int):
    rows = []
    for q in questions:
        row = {k: v for k, v in q.items() if k not in INTERNAL_FIELDS}
        row["dataset_id"] = dataset_id
        row["version"] = version
        rows.append(row)
    return rows


def migrate(dry_run: bool = True, limit: Optional[int] = None) -> Dict[str, Any]:
    db = get_db()
    if not dry_run:
        ensure_indexes(db)

    summary = {
        "dry_run": dry_run,
        "versions_scanned": 0,
        "versions_migrated": 0,
        "versions_already_done": 0,
        "questions_moved": 0,
        "versions_failed": 0,
    }

    cursor = db.dataset_versions.find({"questions": {"$exists": True}}, {"_id": 1})
    if limit:
        cursor = cursor.limit(limit)

    for ref in cursor:
        summary["versions_scanned"] += 1
        doc = db.dataset_versions.find_one({"_id": ref["_id"]})
        if not doc:
            continue
        dataset_id = doc.get("dataset_id")
        version = doc.get("version", 1)
        questions = doc.get("questions") or []

        already = db.questions.count_documents(
            {"dataset_id": dataset_id, "version": version}
        )
        if already >= len(questions) and (already or not questions):
            summary["versions_already_done"] += 1
            if not dry_run:
                db.dataset_versions.update_one(
                    {"_id": ref["_id"]}, {"$unset": {"questions": ""}}
                )
            continue

        if dry_run:
            summary["versions_migrated"] += 1
            summary["questions_moved"] += len(questions)
            continue

        try:
            if questions:
                db.questions.insert_many(
                    rows_for(questions, dataset_id, version), ordered=False
                )
        except pymongo.errors.BulkWriteError as exc:
            # Duplicate idempotency keys mean part of this version is already
            # migrated; the rest still went in with ordered=False.
            non_dup = [
                e for e in exc.details.get("writeErrors", []) if e.get("code") != 11000
            ]
            if non_dup:
                logger.error(
                    "Version %s of dataset %s failed: %s", version, dataset_id, non_dup[:2]
                )
                summary["versions_failed"] += 1
                continue
        except Exception as exc:  # pragma: no cover - depends on live data
            logger.error("Version %s of dataset %s failed: %s", version, dataset_id, exc)
            summary["versions_failed"] += 1
            continue

        # Only drop the array once the rows are in place.
        db.dataset_versions.update_one({"_id": ref["_id"]}, {"$unset": {"questions": ""}})
        summary["versions_migrated"] += 1
        summary["questions_moved"] += len(questions)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Max versions to process")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry_run = not args.apply
    if dry_run:
        logger.info("Dry-run mode — pass --apply to persist changes")

    summary = migrate(dry_run=dry_run, limit=args.limit)
    logger.info("Migration summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
