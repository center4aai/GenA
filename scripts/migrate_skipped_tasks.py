#!/usr/bin/env python3
"""
One-shot migration: remove task status ``skipped``.

- Gate rejections (``error`` starts with ``chunk_rejected:`` or ``task_type=gate``)
  → ``status=completed``, ``gate_passed=false``, ``message_code=gate_rejected_<reason>``.
- Everything else (e.g. claimed-while-cancelled) → ``status=cancelled``.

Dry-run by default; pass ``--apply`` to write.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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


def get_db() -> pymongo.database.Database:
    # Credentials are used only when both are set; an empty pair means no auth
    uri = build_mongo_uri(
        MONGO_USERNAME if MONGO_PASSWORD else "", MONGO_PASSWORD, MONGO_HOST, MONGO_PORT
    )
    client = pymongo.MongoClient(uri)
    return client[MONGO_DB_NAME]


def _rejection_reason(task: Dict[str, Any]) -> str:
    err = task.get("error") or ""
    if isinstance(err, str) and err.startswith("chunk_rejected:"):
        return err.split(":", 1)[1].strip() or "unknown"
    result = task.get("result") or {}
    if isinstance(result, dict):
        gate = result.get("gate_result") or {}
        if isinstance(gate, dict) and gate.get("rejection_reason"):
            return str(gate["rejection_reason"])
        # Inline generation-path rejection payload.
        output = ((result.get("result") or {}).get("output") or {})
        gate2 = output.get("chunk_gate_result") or {}
        if isinstance(gate2, dict) and gate2.get("rejection_reason"):
            return str(gate2["rejection_reason"])
    return "unknown"


def _is_gate_rejection(task: Dict[str, Any]) -> bool:
    err = task.get("error") or ""
    if isinstance(err, str) and err.startswith("chunk_rejected"):
        return True
    if task.get("task_type") == "gate":
        return True
    return False


def migrate(db: pymongo.database.Database, *, apply: bool) -> Dict[str, int]:
    cursor = db.tasks.find({"status": "skipped"})
    counts = {"gate_rejected": 0, "cancelled": 0, "total": 0}
    now = datetime.utcnow()
    for task in cursor:
        counts["total"] += 1
        oid = task["_id"]
        if _is_gate_rejection(task):
            reason = _rejection_reason(task)
            patch: Dict[str, Any] = {
                "status": "completed",
                "gate_passed": False,
                "message_code": f"gate_rejected_{reason}",
                "updated_at": now,
            }
            counts["gate_rejected"] += 1
            action = "gate_rejected→completed"
        else:
            patch = {
                "status": "cancelled",
                "updated_at": now,
            }
            counts["cancelled"] += 1
            action = "other→cancelled"
        logger.info("%s task %s (%s)", "[APPLY]" if apply else "[DRY]", oid, action)
        if apply:
            db.tasks.update_one({"_id": oid, "status": "skipped"}, {"$set": patch})
    return counts


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = get_db()
    counts = migrate(db, apply=args.apply)
    logger.info(
        "Done: total=%s gate_rejected=%s cancelled=%s apply=%s",
        counts["total"],
        counts["gate_rejected"],
        counts["cancelled"],
        args.apply,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
