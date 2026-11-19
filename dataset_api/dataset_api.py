from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple, Union, Literal
from datetime import datetime, timedelta, timezone
import os
import pymongo
import uuid
from pymongo import ReturnDocument, UpdateOne
import gridfs
from bson import ObjectId
from bson.errors import InvalidId
import logging
from urllib.parse import quote
from config import MONGO_HOST, MONGO_PORT, MONGO_USERNAME, MONGO_PASSWORD, MONGO_DB_NAME, build_mongo_uri

from fastapi import Depends
from auth_router import router as auth_router
from auth_utils import get_current_user, require_role, seed_users_locked, verify_worker_key
from pipeline_constants import REVALIDATION_SCOPES
from request_context import RequestIdMiddleware, install_error_handlers

from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Singleton MongoClient (one connection pool per process)
_mongo_client: Optional[pymongo.MongoClient] = None


def get_mongo_client() -> pymongo.MongoClient:
    global _mongo_client
    if _mongo_client is None:
        # Credentials are used only when both are set; an empty pair means no auth
        connection_string = build_mongo_uri(
            MONGO_USERNAME if MONGO_PASSWORD else "", MONGO_PASSWORD, MONGO_HOST, MONGO_PORT
        )
        _mongo_client = pymongo.MongoClient(connection_string)
    return _mongo_client


def get_db():
    return get_mongo_client()[MONGO_DB_NAME]


def get_gridfs():
    return gridfs.GridFS(get_db())


# Raw uploaded documents can be large; cap what we accept so async chunking
# stays within the same limit the browser chunker enforced (default 50MB).
RAW_DOCUMENT_MAX_BYTES = int(os.getenv("RAW_DOCUMENT_MAX_BYTES", str(50 * 1024 * 1024)))


def _oid(value: str, *, label: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


def _ensure_indexes(db):
    """Create indexes once at startup."""
    db.chunks.create_index([("dataset_id", 1), ("chunk_index", 1)])
    db.tasks.create_index([("status", 1), ("priority", -1), ("created_at", 1)])
    db.tasks.create_index([("dataset_id", 1), ("status", 1)])
    # The queue task table pages with skip/limit, so its default sort has to be
    # index-driven: filtering on queue_id while sorting on created_at otherwise
    # sorts the whole queue in memory on every page turn. The status-first form
    # covers the same query with a filter applied and supersedes a bare
    # (queue_id, status) index.
    db.tasks.create_index([("queue_id", 1), ("created_at", 1)])
    db.tasks.create_index([("queue_id", 1), ("status", 1), ("created_at", 1)])
    db.tasks.create_index([("queue_id", 1), ("updated_at", 1)])
    db.tasks.create_index([("queue_name", 1), ("status", 1), ("priority", -1), ("created_at", 1)])
    # Enqueue dedupe — the hottest query of the fan-out path (one probe per task).
    db.tasks.create_index(
        [("dataset_id", 1), ("chunk_id", 1), ("task_type", 1), ("question_type", 1)]
    )
    # /tasks/stuck sweeps by updated_at within a status.
    db.tasks.create_index([("status", 1), ("updated_at", 1)])
    # /metrics and reconcile count gate rejections.
    db.tasks.create_index([("dataset_id", 1), ("gate_passed", 1)])
    # pipeline-stats groups a run's tasks by (task_type, status) on every poll.
    db.tasks.create_index([("dataset_id", 1), ("task_type", 1), ("status", 1)])
    # GET /datasets/{id}/tasks sorts by created_at within a dataset.
    db.tasks.create_index([("dataset_id", 1), ("created_at", 1)])
    db.datasets.create_index([("metadata.created_by", 1)])
    db.datasets.create_index([("metadata.status", 1)])
    db.dataset_versions.create_index([("dataset_id", 1), ("version", 1)], unique=True)
    # Questions live in their own collection: as an embedded array they pushed
    # the version document towards MongoDB's hard 16 MB limit, and every saved
    # question re-read the whole thing.
    db.questions.create_index([("dataset_id", 1), ("version", 1)])
    db.questions.create_index(
        [("dataset_id", 1), ("version", 1), ("question_id", 1)]
    )
    db.questions.create_index(
        [("dataset_id", 1), ("version", 1), ("idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )
    # Every queue request looks a queue up by name first.
    db.queues.create_index([("name", 1)])
    # Quality revalidation: one active run per version, items claimed by status.
    db.revalidation_runs.create_index([("dataset_id", 1), ("version", 1), ("created_at", -1)])
    db.revalidation_runs.create_index(
        [("dataset_id", 1), ("version", 1)],
        unique=True,
        partialFilterExpression={"status": "processing"},
        name="one_active_revalidation_per_version",
    )
    db.revalidation_items.create_index([("status", 1), ("created_at", 1)])
    db.revalidation_items.create_index([("run_id", 1), ("status", 1)])
    # Upload/download/delete of the raw document all filter GridFS by dataset.
    db.fs.files.create_index([("dataset_id", 1)])


TaskStatus = Literal["pending", "processing", "completed", "failed", "cancelled"]

ALLOWED_STATUS_TRANSITIONS: Dict[str, set] = {
    "pending": {"processing", "cancelled"},
    "processing": {"completed", "failed", "cancelled", "pending"},
    "completed": set(),
    "failed": {"pending"},
    "cancelled": set(),
    # Legacy: accept no further transitions from historical skipped rows.
    "skipped": set(),
}


def _validate_status_transition(current: str, new: str) -> None:
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
    if new not in allowed and current != new:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition: {current} -> {new}",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        seed_users_locked()
    except Exception:
        logger.exception("seed_users_locked failed")
    try:
        _ensure_indexes(get_db())
    except Exception:
        logger.exception("_ensure_indexes failed")
    yield
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None

app = FastAPI(title="GenA Dataset API", version="1.0.0", lifespan=lifespan)
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.add_middleware(RequestIdMiddleware)
install_error_handlers(app, logger)
app.include_router(auth_router, tags=["auth"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "dataset_api"}


@app.get("/ready")
def ready():
    """Readiness: liveness plus a Mongo ping. Keep /health as a cheap liveness probe."""
    try:
        get_db().command("ping")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"mongo unavailable: {type(exc).__name__}")
    return {"status": "ready", "service": "dataset_api"}


@app.get("/metrics")
def metrics(current_user: dict = Depends(get_current_user)):
    """Basic queue/dataset counters for observability."""
    db = get_db()
    return {
        "tasks_pending": db.tasks.count_documents({"status": "pending"}),
        "tasks_processing": db.tasks.count_documents({"status": "processing"}),
        "tasks_completed": db.tasks.count_documents({"status": "completed"}),
        "tasks_failed": db.tasks.count_documents({"status": "failed"}),
        "tasks_gate_rejected": db.tasks.count_documents({"gate_passed": False}),
        "tasks_cancelled": db.tasks.count_documents({"status": "cancelled"}),
        "datasets_total": db.datasets.count_documents({}),
    }


# Pydantic models

# How a question came to differ from what the pipeline generated. The manual-edit
# PATCH derives "user_edited" itself; Transform sends "paraphrased" for the
# questions the model actually reworded. Export preferences and answer shuffles
# are not edits and leave the status untouched.
EditStatus = Literal["user_edited", "paraphrased"]


class QuestionData(BaseModel):
    chunk_id: int
    question_type: str
    task: str
    options: Union[str, Dict[str, str]]
    correct_answer: str
    provocativeness: str
    difficulty: Optional[str] = None
    validation_passed: Optional[str] = None
    validation_score: Optional[str] = None
    validation_threshold: Optional[str] = None
    validation_details: Optional[str] = None
    validation_justifications: Optional[str] = None
    retry_count: Optional[str] = None
    source_chunk: Optional[str] = None
    question_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    sensitivity_explanation: Optional[str] = None
    difficulty_explanation: Optional[str] = None
    # The model's ratings as they were before the first manual change. Set by
    # the PATCH handler, never by the client; absent for questions edited before
    # this existed. Travel with the question into new versions like the
    # explanations do.
    provocativeness_original: Optional[str] = None
    difficulty_original: Optional[str] = None
    excluded_from_export: Optional[bool] = None
    needs_revalidation: Optional[bool] = None
    # Whether validation_* describe the question's current wording. False after
    # a wording edit or an AI rephrase; the old verdict stays stored (ТЗ 10.4)
    # but the UI shows "revalidation required" until a new check succeeds.
    validation_is_current: Optional[bool] = None
    # Which model produced validation_* (generation: the task's validator;
    # revalidation: the run's) and the run that last rewrote them.
    validation_model_id: Optional[str] = None
    revalidation_run_id: Optional[str] = None
    edit_status: Optional[EditStatus] = None
    edited_at: Optional[datetime] = None

    class Config:
        extra = "ignore"


# Upper bound for task listings. pymongo treats .limit(0) as "no limit", so an
# unvalidated ?limit=0 dumped the whole collection in one response.
MAX_TASK_PAGE = 5000

# Sortable task columns, mapped from the UI's column key to the document fields
# behind it. Anything outside this map falls back to `created_at`, so a typo in a
# query string can never turn into an unindexed sort over the whole collection.
#
# `task_type` and `message_code` back columns the UI renders as localized labels
# (taskTypeLabel / taskMessageLabel), so their order groups like with like rather
# than matching the alphabet of the translated text. That is the price of sorting
# server-side, which is what makes paging past the first page possible at all.
_TASK_SORT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "created_at": ("created_at",),
    "updated_at": ("updated_at",),
    "status": ("status",),
    "chunk_id": ("chunk_id",),
    "task_type": ("task_type", "question_type"),
    "generation_model_id": ("generation_model_id",),
    "validation_model_id": ("validation_model_id",),
    "message_code": ("message_code", "error"),
}

# Statuses where generation is over, so a new version can safely branch off.
FINISHED_DATASET_STATUSES = frozenset(
    {"completed", "completed_with_failures", "no_valid_chunks"}
)

# Fields a user may edit in place. Everything else on a question is produced by
# the pipeline and must not be writable through the manual-edit endpoint.
QUESTION_PATCH_FIELDS = (
    "question_type",
    "task",
    "options",
    "correct_answer",
    "provocativeness",
    "difficulty",
    "excluded_from_export",
    "needs_revalidation",
    "validation_passed",
    "validation_score",
    "validation_threshold",
    "validation_details",
    "validation_justifications",
)

# Editing any of these is a content edit: it sets edit_status="user_edited" on the
# question and stamps the version. excluded_from_export is an export preference,
# not an edit, and the validation_* / needs_revalidation keys only travel along
# with a wording change.
QUESTION_CONTENT_FIELDS = frozenset(
    {"question_type", "task", "options", "correct_answer", "provocativeness", "difficulty"}
)

# The subset the quality validator scores. Changing any of them makes the stored
# verdict stale (validation_is_current=False); sensitivity, difficulty and the
# export toggle do not (ТЗ 10.4).
QUESTION_VALIDATED_FIELDS = frozenset({"question_type", "task", "options", "correct_answer"})


def _validation_is_current(q: Dict[str, Any]) -> bool:
    """Whether the stored verdict describes the question as it is now.

    Rows written before validation_is_current existed carry only
    needs_revalidation (and had their scores nulled): derive it from those.
    """
    flag = q.get("validation_is_current")
    if isinstance(flag, bool):
        return flag
    if q.get("needs_revalidation"):
        return False
    passed = q.get("validation_passed")
    return passed is not None and str(passed).strip() != ""

# Ratings whose first manual change also keeps the model's value as
# <field>_original, so the "original model assessment" line in the UI stays
# true after the user picks a different level.
QUESTION_RATING_FIELDS = ("provocativeness", "difficulty")


def _preserve_original_ratings(current: Dict[str, Any], changes: Dict[str, Any]) -> None:
    """Copy a rating's pre-edit value to <field>_original on its first real change.

    Never overwrites an original already kept, and skips no-op edits (same
    value re-sent) and ratings the model never produced (nothing to keep).
    """
    for field in QUESTION_RATING_FIELDS:
        if field not in changes:
            continue
        before = current.get(field)
        if before is None or str(before).strip() == "":
            continue
        if current.get(f"{field}_original") is not None:
            continue
        after = changes[field]
        if str(after if after is not None else "") == str(before):
            continue
        changes[f"{field}_original"] = before


class QuestionPatch(BaseModel):
    """One question's partial update, addressed by question_id."""

    question_id: str
    question_type: Optional[str] = None
    task: Optional[str] = None
    options: Optional[Union[str, Dict[str, str]]] = None
    correct_answer: Optional[str] = None
    provocativeness: Optional[str] = None
    difficulty: Optional[str] = None
    excluded_from_export: Optional[bool] = None
    needs_revalidation: Optional[bool] = None
    validation_passed: Optional[str] = None
    validation_score: Optional[str] = None
    validation_threshold: Optional[str] = None
    validation_details: Optional[str] = None
    validation_justifications: Optional[str] = None

    class Config:
        extra = "ignore"


class QuestionsPatchRequest(BaseModel):
    questions: List[QuestionPatch]

    class Config:
        extra = "ignore"


class DatasetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    source_document: str
    questions: List[QuestionData]
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        extra = "ignore"


class DatasetUpdate(BaseModel):
    questions: List[QuestionData]
    metadata: Optional[Dict[str, Any]] = None
    base_version: Optional[int] = None

    class Config:
        extra = "ignore"


# Task Queue Models
class TaskData(BaseModel):
    chunk_id: int
    chunk_text: str
    question_type: str
    source_document: str
    dataset_name: str
    dataset_id: Optional[str] = None
    dataset_description: Optional[str] = None
    priority: Optional[int] = 1
    generation_model_id: Optional[str] = None
    validation_model_id: Optional[str] = None
    chunk_pre_validated: Optional[bool] = None
    pipeline_mode: Optional[str] = "full"
    # "generation" (default) or "gate" — gate tasks validate a chunk in the
    # background queue instead of blocking the browser tab.
    task_type: Optional[str] = "generation"
    # For gate tasks: which question types the user requested (defines which
    # generation tasks the gate decision applies to).
    question_types: Optional[List[str]] = None
    # Pipeline flavour: "normative", "textbook", or "encyclopedia".
    document_kind: Optional[str] = "normative"
    # For chunk tasks: rules | layout | semantic | llm (chunker CHUNKING_METHODS).
    # pydantic drops unknown fields, so anything the worker must see has to be
    # declared here explicitly.
    chunking_method: Optional[str] = None
    # For gate/generation tasks: chunker block type (body, inset, caption, …).
    block_type: Optional[str] = None

    class Config:
        extra = "ignore"


class QueueCreate(BaseModel):
    name: str
    description: Optional[str] = None
    priority: Optional[int] = 1

    class Config:
        extra = "ignore"


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    result: Optional[Dict] = None
    error: Optional[str] = None
    attempts: Optional[int] = None
    expected_status: Optional[TaskStatus] = None
    message_code: Optional[str] = None
    gate_passed: Optional[bool] = None
    # Optional owner check: when set, CAS also requires this worker_id.
    worker_id: Optional[str] = None

    class Config:
        extra = "ignore"


class TaskClaimRequest(BaseModel):
    worker_id: Optional[str] = "default"
    limit: int = Field(default=10, ge=1, le=100)
    queue_name: Optional[str] = None

    class Config:
        extra = "ignore"


class ChunkCreate(BaseModel):
    chunk_index: int
    chunk_text: str
    fragment_data: Optional[Dict[str, Any]] = None
    hierarchy_context: Optional[Dict[str, Any]] = None
    gate_result: Optional[Dict[str, Any]] = None
    gate_passed: bool = True
    question_types_valid: Optional[List[str]] = None
    # True while the chunk is waiting for its async gate task to finish.
    gate_pending: bool = False

    class Config:
        extra = "ignore"


class ChunkGateUpdate(BaseModel):
    gate_result: Dict[str, Any]
    gate_passed: bool
    question_types_valid: Optional[List[str]] = None

    class Config:
        extra = "ignore"


# API endpoints
@app.post("/datasets/", response_model=Dict[str, Any])
def create_dataset(
    dataset: DatasetCreate,
    current_user=Depends(require_role("expert")),
):
    db = get_db()
    datasets_collection = db.datasets

    # Stamp ownership server-side from JWT (ignore client-supplied created_by).
    meta = dict(dataset.metadata or {})
    meta["created_by"] = getattr(current_user, "username", None) or "public"

    dataset_doc = {
        "name": dataset.name,
        "description": dataset.description,
        "source_document": dataset.source_document,
        "current_version": 1,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "metadata": meta,
    }

    result = datasets_collection.insert_one(dataset_doc)
    dataset_id = str(result.inserted_id)

    version_doc = {
        "dataset_id": dataset_id,
        "version": 1,
        "created_at": datetime.utcnow(),
        # questions live in their own collection now
        "metadata": meta,
    }

    db.dataset_versions.insert_one(version_doc)

    return {
        "dataset_id": dataset_id,
        "name": dataset.name,
        "version": 1,
        "message": "Dataset created successfully"
    }


@app.get("/datasets/", response_model=List[Dict[str, Any]])
def list_datasets(
    mine: Optional[int] = None,
    current_user=Depends(get_current_user),
):
    """List datasets. Pass mine=1 to return only those created by the current user."""
    db = get_db()
    query: Dict[str, Any] = {}
    role = getattr(current_user, "role", None)
    if mine == 1 or role == "user":
        username = getattr(current_user, "username", None) or "public"
        query["metadata.created_by"] = username
    datasets = list(db.datasets.find(query, {
        "name": 1, "description": 1, "source_document": 1,
        "current_version": 1, "created_at": 1, "updated_at": 1, "metadata": 1,
    }))
    dataset_ids = [str(d["_id"]) for d in datasets]
    question_counts: Dict[tuple, int] = {}
    if dataset_ids:
        # Only the current version of each dataset is ever displayed. Matching
        # every version made the server read (and $size) whole multi-MB
        # documents just to throw all but one away on each dashboard load.
        wanted = [
            {"dataset_id": str(d["_id"]), "version": d.get("current_version", 1)}
            for d in datasets
        ]
        for row in db.questions.aggregate([
            {"$match": {"$or": wanted}},
            {"$group": {
                "_id": {"dataset_id": "$dataset_id", "version": "$version"},
                "n": {"$sum": 1},
            }},
        ]):
            key = (row["_id"]["dataset_id"], row["_id"].get("version", 1))
            question_counts[key] = int(row.get("n") or 0)

        # Transitional: versions still holding an embedded array report 0
        # above. Remove together with _legacy_questions once migrated.
        missing = [w for w in wanted
                   if (w["dataset_id"], w["version"]) not in question_counts]
        if missing:
            for row in db.dataset_versions.aggregate([
                {"$match": {"$or": missing}},
                {"$project": {
                    "_id": 0,
                    "dataset_id": 1,
                    "version": 1,
                    "n": {"$size": {"$ifNull": ["$questions", []]}},
                }},
            ]):
                key = (row["dataset_id"], row.get("version", 1))
                question_counts[key] = int(row.get("n") or 0)
    chunk_counts: Dict[str, Dict[str, int]] = {
        did: {"total": 0, "valid": 0} for did in dataset_ids
    }
    if dataset_ids:
        for row in db.chunks.aggregate([
            {"$match": {"dataset_id": {"$in": dataset_ids}}},
            {"$group": {
                "_id": "$dataset_id",
                "total": {"$sum": 1},
                "valid": {
                    "$sum": {"$cond": [{"$eq": ["$gate_passed", True]}, 1, 0]},
                },
            }},
        ]):
            chunk_counts[str(row["_id"])] = {
                "total": int(row.get("total") or 0),
                "valid": int(row.get("valid") or 0),
            }
    # Version summary for the "Versions" / "Last modified" columns: one grouped
    # read over the (dataset_id, version) index. Pushing three scalar fields
    # keeps a not-yet-migrated embedded `questions` array out of the result.
    version_summaries: Dict[str, Dict[str, Any]] = {}
    if dataset_ids:
        current_by_id = {str(d["_id"]): d.get("current_version", 1) for d in datasets}
        for row in db.dataset_versions.aggregate([
            {"$match": {"dataset_id": {"$in": dataset_ids}}},
            {"$group": {
                "_id": "$dataset_id",
                "versions": {"$push": {
                    "version": "$version",
                    "created_at": "$created_at",
                    "user_updated_at": "$user_updated_at",
                }},
            }},
        ]):
            did = str(row["_id"])
            version_summaries[did] = _summarize_versions(
                row.get("versions") or [], current_by_id.get(did, 1)
            )
    for dataset in datasets:
        did = str(dataset["_id"])
        dataset["_id"] = did
        counts = chunk_counts.get(did, {"total": 0, "valid": 0})
        dataset["chunks_count"] = counts["total"]
        dataset["chunks_valid"] = counts["valid"]
        version = dataset.get("current_version", 1)
        dataset["questions_count"] = question_counts.get((did, version), 0)
        summary = version_summaries.get(did) or _summarize_versions([], version)
        dataset["versions_count"] = summary["versions_count"] or version
        dataset["last_user_modified_at"] = summary["last_user_modified_at"]
        dataset["current_version_user_updated_at"] = summary["current_version_user_updated_at"]
    return datasets

@app.get("/datasets/{dataset_id}", response_model=Dict[str, Any])
def get_dataset(dataset_id: str, version: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    db = get_db()
    dataset = _assert_dataset_access(db, dataset_id, current_user)

    if version is None:
        version = dataset.get("current_version", 1)

    version_doc = db.dataset_versions.find_one({
        "dataset_id": dataset_id,
        "version": version
    })

    if not version_doc:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    result = {
        "dataset_id": str(dataset["_id"]),
        "_id": str(dataset["_id"]),
        "name": dataset["name"],
        "description": dataset["description"],
        "source_document": dataset["source_document"],
        "current_version": dataset["current_version"],
        "requested_version": version,
        "created_at": dataset["created_at"],
        "updated_at": dataset["updated_at"],
        "questions": _load_questions(db, dataset_id, version),
        "metadata": version_doc.get("metadata", {})
    }
    return result


@app.get("/datasets/{dataset_id}/versions", response_model=List[Dict[str, Any]])
def get_dataset_versions(dataset_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    _assert_dataset_access(db, dataset_id, current_user)

    # `updated_at` is bumped by every generated question, so it is not a
    # user-visible "edited" marker — only `user_updated_at` is.
    versions = list(db.dataset_versions.aggregate([
        {"$match": {"dataset_id": dataset_id}},
        {"$project": {
            "_id": 0,
            "version": 1,
            "created_at": 1,
            "metadata": 1,
            "user_updated_at": 1,
            "user_updated_by": 1,
            # Transitional: only versions not yet migrated still carry the
            # array. Drop this projection once the migration has run.
            "legacy_count": {"$size": {"$ifNull": ["$questions", []]}},
        }},
        {"$sort": {"version": 1}},
    ]))

    counts = {
        row["_id"]: int(row.get("n") or 0)
        for row in db.questions.aggregate([
            {"$match": {"dataset_id": dataset_id}},
            {"$group": {"_id": "$version", "n": {"$sum": 1}}},
        ])
    }
    for v in versions:
        legacy = int(v.pop("legacy_count", 0) or 0)
        v["questions_count"] = counts.get(v.get("version"), 0) or legacy

    return versions


@app.put("/datasets/{dataset_id}", response_model=Dict[str, Any])
def update_dataset(
    dataset_id: str,
    update: DatasetUpdate,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    current_version = dataset.get("current_version", 1)
    # This route now serves `transform` only — manual edits go to
    # PATCH /datasets/{id}/versions/{version}/questions. Branching a new
    # version off anything but the tip would silently revert the versions in
    # between, so base_version is mandatory here.
    if update.base_version is None:
        raise HTTPException(
            status_code=400,
            detail="base_version is required (new versions must branch off the current one)",
        )
    if int(update.base_version) != int(current_version):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dataset version conflict (base_version={update.base_version}, "
                f"current={current_version})"
            ),
        )
    # AI transform branches off the tip; not while that version is being revalidated.
    _assert_no_active_revalidation(db, dataset_id, int(current_version))
    prev_version = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": current_version},
        {"metadata": 1},
    )
    prev_metadata = (prev_version or {}).get("metadata") or {}
    status_now = prev_metadata.get("status") or (dataset.get("metadata") or {}).get("status")
    if status_now not in FINISHED_DATASET_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dataset is not finished (status={status_now!r}); "
                "new versions can only be created once generation is complete"
            ),
        )
    if update.metadata:
        version_metadata = {**prev_metadata, **update.metadata}
    else:
        version_metadata = prev_metadata

    # Compare-and-swap on current_version. Checking it above and bumping it
    # here as two steps let two concurrent transforms with the same
    # base_version both pass, producing v4 and v5 where v5 was built from v3.
    updated = db.datasets.find_one_and_update(
        {
            "_id": _oid(dataset_id, label="dataset_id"),
            "current_version": current_version,
        },
        {"$inc": {"current_version": 1}, "$set": {"updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dataset version conflict (base_version={update.base_version}); "
                "another update won the race"
            ),
        )
    new_version = updated["current_version"]

    version_doc = {
        "dataset_id": dataset_id,
        "version": new_version,
        "created_at": datetime.utcnow(),
        "metadata": version_metadata,
    }

    try:
        db.dataset_versions.insert_one(version_doc)
        payloads = [q.dict() for q in update.questions]
        for q in payloads:
            # Rephrased questions arrive flagged: their inherited verdict is stale.
            q["validation_is_current"] = _validation_is_current(q)
        rows = _question_rows(payloads, dataset_id, new_version)
        if rows:
            db.questions.insert_many(rows, ordered=False)
    except Exception:
        # current_version now points at a version document that does not
        # exist: every read of this dataset would 404 forever. Put the
        # pointer back before surfacing the failure.
        db.datasets.update_one(
            {"_id": _oid(dataset_id, label="dataset_id"), "current_version": new_version},
            {"$set": {"current_version": current_version}},
        )
        db.questions.delete_many({"dataset_id": dataset_id, "version": new_version})
        db.dataset_versions.delete_one(
            {"dataset_id": dataset_id, "version": new_version}
        )
        raise

    if update.metadata:
        # Dotted paths only: replacing the whole metadata object races with
        # the $inc counters written by gate-progress and can roll them back.
        db.datasets.update_one(
            {"_id": _oid(dataset_id, label="dataset_id")},
            {"$set": {f"metadata.{k}": v for k, v in update.metadata.items()}},
        )

    return {"dataset_id": dataset_id, "new_version": new_version, "message": "Dataset updated successfully"}


@app.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    oid = _oid(dataset_id, label="dataset_id")
    dataset = db.datasets.find_one({"_id": oid})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    db.dataset_versions.delete_many({"dataset_id": dataset_id})
    db.questions.delete_many({"dataset_id": dataset_id})
    db.chunks.delete_many({"dataset_id": dataset_id})
    db.tasks.delete_many({"dataset_id": dataset_id})
    fs = get_gridfs()
    for old in db.fs.files.find({"dataset_id": dataset_id}, {"_id": 1}):
        try:
            fs.delete(old["_id"])
        except Exception:
            logger.warning("Failed to delete GridFS file %s", old.get("_id"))
    db.datasets.delete_one({"_id": oid})

    # The queue that fed this dataset is meaningless without it, and its tasks
    # (deleted above by dataset_id) would otherwise leave an empty queue behind.
    queue_deleted = False
    queue_name = (dataset.get("metadata") or {}).get("queue_name")
    if queue_name:
        queue = db.queues.find_one({"name": queue_name}, {"_id": 1})
        if queue:
            db.tasks.delete_many({"queue_id": str(queue["_id"])})
            db.queues.delete_one({"_id": queue["_id"]})
            queue_deleted = True

    return {"message": "Dataset deleted successfully", "queue_deleted": queue_deleted}


# Task Queue endpoints
_QUEUE_NAME_MAX_LEN = 300
_QUEUE_NAME_FORBIDDEN = frozenset("/\\#?%")


def _validate_queue_name(name: str) -> None:
    """Reject names the queue URLs cannot carry.

    The name is a path segment of ``/queues/{queue_name}/...``, so slashes,
    ``#``, ``?``, ``%`` and control characters either break routing or get
    mangled on the way. Anything else (Cyrillic, spaces, dashes, dots, commas,
    brackets) is fine and already in use.
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Queue name must not be empty")
    if len(name) > _QUEUE_NAME_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Queue name is too long (max {_QUEUE_NAME_MAX_LEN} characters)",
        )
    for ch in name:
        if ch in _QUEUE_NAME_FORBIDDEN or ord(ch) < 32 or 127 <= ord(ch) < 160:
            raise HTTPException(
                status_code=400,
                detail="Queue name must not contain '/', '\\', '#', '?', '%' or control characters",
            )


@app.post("/queues/", response_model=Dict[str, Any])
def create_queue(
    queue: QueueCreate,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    queues_collection = db.queues

    _validate_queue_name(queue.name)
    existing_queue = queues_collection.find_one({"name": queue.name})
    if existing_queue:
        raise HTTPException(status_code=400, detail="Queue with this name already exists")

    queue_doc = {
        "name": queue.name,
        "description": queue.description,
        "priority": queue.priority,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "task_count": 0,
        "pending_count": 0,
        "processing_count": 0,
        "completed_count": 0,
        "failed_count": 0
    }

    result = queues_collection.insert_one(queue_doc)
    return {"queue_id": str(result.inserted_id), "name": queue.name, "message": "Queue created successfully"}


@app.get("/queues/", response_model=List[Dict[str, Any]])
def list_queues(current_user: dict = Depends(get_current_user)):
    db = get_db()

    queues = list(db.queues.find({}))

    # One grouped pass over tasks for every queue at once. This endpoint is
    # polled every few seconds by each open tab, and the previous per-queue
    # aggregation loop made that N round trips per poll.
    counts: Dict[str, Dict[str, int]] = {}
    for row in db.tasks.aggregate([
        {"$group": {
            "_id": {"queue_id": "$queue_id", "status": "$status"},
            "count": {"$sum": 1},
        }},
    ]):
        key = row["_id"]
        counts.setdefault(key.get("queue_id") or "", {})[key.get("status") or ""] = int(row["count"])

    for queue in queues:
        by_status = counts.get(str(queue["_id"]), {})
        queue["pending_count"] = by_status.get("pending", 0)
        queue["processing_count"] = by_status.get("processing", 0)
        queue["completed_count"] = by_status.get("completed", 0)
        queue["failed_count"] = by_status.get("failed", 0)
        queue["cancelled_count"] = by_status.get("cancelled", 0)
        # Legacy pre-migration status: counts toward the total, not any bucket.
        legacy_skipped = by_status.get("skipped", 0)

        queue["task_count"] = (
            queue["pending_count"] +
            queue["processing_count"] +
            queue["completed_count"] +
            queue["failed_count"] +
            queue["cancelled_count"] +
            legacy_skipped
        )
        queue["_id"] = str(queue["_id"])

    return queues


@app.post("/queues/{queue_name}/tasks/", response_model=Dict[str, Any])
def add_tasks_to_queue(
    queue_name: str,
    tasks: List[TaskData],
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    queues_collection = db.queues
    tasks_collection = db.tasks

    queue = queues_collection.find_one({"name": queue_name})
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")

    queue_id = str(queue["_id"])
    inserted_tasks = []

    # One dedupe query for the whole batch instead of one per task. Fanning
    # out a large document meant thousands of serialized round trips, which
    # blew past the worker's 30 s client timeout while the API kept
    # inserting — the run then looked failed but kept generating.
    dataset_ids = {t.dataset_id for t in tasks if t.dataset_id is not None}
    existing_keys = set()
    if dataset_ids:
        for row in tasks_collection.find(
            {
                "dataset_id": {"$in": list(dataset_ids)},
                "status": {"$in": ["pending", "processing", "completed"]},
            },
            {"dataset_id": 1, "chunk_id": 1, "task_type": 1, "question_type": 1},
        ):
            existing_keys.add((
                row.get("dataset_id"),
                row.get("chunk_id"),
                row.get("task_type"),
                row.get("question_type"),
            ))

    pending_docs = []
    for task_data in tasks:
        task_type = task_data.task_type or "generation"
        # Skip duplicates so a retried chunk/gate task does not fan out twice.
        if task_data.dataset_id is not None:
            key = (
                task_data.dataset_id,
                task_data.chunk_id,
                task_type,
                task_data.question_type,
            )
            if key in existing_keys:
                continue
            existing_keys.add(key)  # also dedupes within this batch
        task_doc = {
            "queue_id": queue_id,
            "queue_name": queue_name,
            "chunk_id": task_data.chunk_id,
            "chunk_text": task_data.chunk_text,
            "question_type": task_data.question_type,
            "source_document": task_data.source_document,
            "dataset_name": task_data.dataset_name,
            "dataset_id": task_data.dataset_id,
            "dataset_description": task_data.dataset_description,
            "priority": task_data.priority,
            "generation_model_id": task_data.generation_model_id,
            "validation_model_id": task_data.validation_model_id,
            "chunk_pre_validated": task_data.chunk_pre_validated or False,
            "pipeline_mode": task_data.pipeline_mode or "full",
            "task_type": task_type,
            "question_types": task_data.question_types,
            "document_kind": task_data.document_kind or "normative",
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "result": None,
            "error": None
        }
        if task_data.chunking_method:
            task_doc["chunking_method"] = task_data.chunking_method
        if task_data.block_type:
            task_doc["block_type"] = task_data.block_type
        pending_docs.append(task_doc)

    if pending_docs:
        result = tasks_collection.insert_many(pending_docs, ordered=False)
        inserted_tasks = [str(i) for i in result.inserted_ids]

    return {
        "queue_name": queue_name,
        "tasks_added": len(inserted_tasks),
        "task_ids": inserted_tasks,
        "message": f"Added {len(inserted_tasks)} tasks to queue '{queue_name}'"
    }


@app.get("/queues/{queue_name}/tasks/", response_model=List[Dict[str, Any]])
def get_queue_tasks(
    queue_name: str,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=MAX_TASK_PAGE),
    skip: int = Query(0, ge=0),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    brief: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """One page of a queue's tasks.

    ``brief=1`` drops ``chunk_text`` and ``result`` — the two large fields, which
    the UI only renders for a single selected task. Polling the full documents
    every few seconds moved tens of megabytes per open tab.

    ``skip``/``sort_by`` exist because a run can hold far more tasks than any one
    response should carry: the caller pages through them instead of asking for
    everything and cutting the tail off. The total to page against comes from
    ``GET /queues/``, which already counts every status with a ``$group``.
    """
    db = get_db()
    queues_collection = db.queues
    tasks_collection = db.tasks

    queue = queues_collection.find_one({"name": queue_name})
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")

    # A queue is reachable by name only; ownership lives on the dataset it
    # feeds, so a plain user may read it only when that dataset is theirs.
    if getattr(current_user, "role", None) == "user":
        dataset = db.datasets.find_one(
            {"metadata.queue_name": queue_name}, {"metadata.created_by": 1}
        )
        owner = ((dataset or {}).get("metadata") or {}).get("created_by")
        if not dataset or (owner and owner != getattr(current_user, "username", None)):
            raise HTTPException(status_code=403, detail="Queue belongs to another user")

    queue_id = str(queue["_id"])
    filter_query = {"queue_id": queue_id}
    if status:
        filter_query["status"] = status

    fields = _TASK_SORT_FIELDS.get(sort_by, _TASK_SORT_FIELDS["created_at"])
    direction = -1 if sort_dir == "desc" else 1
    sort_spec: Dict[str, int] = {f: direction for f in fields}
    # Tie-break on _id. Thousands of tasks are created in the same fan-out and
    # share a timestamp; without a total order, skip/limit paging silently
    # repeats rows on one page and drops them from another.
    sort_spec["_id"] = 1

    # An aggregation rather than find().sort(): projecting the two large
    # fields away *before* the sort is what keeps a non-indexed sort (model
    # ids, message_code) under the 32 MB in-memory sort budget on a queue
    # with tens of thousands of tasks. allowDiskUse covers the rest.
    pipeline: List[Dict[str, Any]] = [{"$match": filter_query}]
    if brief:
        pipeline.append({"$project": {"chunk_text": 0, "result": 0}})
    pipeline += [{"$sort": sort_spec}, {"$skip": skip}, {"$limit": limit}]

    tasks = list(tasks_collection.aggregate(pipeline, allowDiskUse=True))
    for task in tasks:
        task["_id"] = str(task["_id"])
    return tasks


@app.get("/tasks/stuck", response_model=List[Dict[str, Any]])
def get_stuck_tasks(
    threshold_minutes: int = Query(10, ge=1, le=1440),
    _: None = Depends(verify_worker_key),
):
    """Return tasks stuck in 'processing' longer than threshold."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(minutes=threshold_minutes)
    tasks = list(db.tasks.find({
        "status": "processing",
        "updated_at": {"$lt": cutoff},
    }).limit(100))
    for task in tasks:
        task["_id"] = str(task["_id"])
    return tasks


# Fields that belong to the storage layer, not to the API payload.
_QUESTION_INTERNAL_FIELDS = {"_id", "dataset_id", "version"}


def _question_rows(questions: List[Dict[str, Any]], dataset_id: str, version: int):
    """Turn API question payloads into rows of the `questions` collection."""
    rows = []
    for q in questions:
        row = {k: v for k, v in q.items() if k not in _QUESTION_INTERNAL_FIELDS}
        row["dataset_id"] = dataset_id
        row["version"] = version
        rows.append(row)
    return rows


def _legacy_questions(db, dataset_id: str, version: int) -> List[Dict[str, Any]]:
    """Questions still embedded in the version document.

    Transitional: lets the new code serve data that
    ``scripts/migrate_questions_collection.py`` has not moved yet, so deploy and
    migration do not have to be simultaneous. Delete once every environment has
    been migrated.
    """
    doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": version}, {"questions": 1}
    )
    return list((doc or {}).get("questions") or [])


def _migrate_version_questions(db, dataset_id: str, version: int) -> int:
    """Move one version's embedded questions into the collection, then unset."""
    legacy = _legacy_questions(db, dataset_id, version)
    if legacy:
        try:
            db.questions.insert_many(
                _question_rows(legacy, dataset_id, version), ordered=False
            )
        except pymongo.errors.BulkWriteError as exc:
            fatal = [
                e for e in exc.details.get("writeErrors", []) if e.get("code") != 11000
            ]
            if fatal:
                raise
    db.dataset_versions.update_one(
        {"dataset_id": dataset_id, "version": version},
        {"$unset": {"questions": ""}},
    )
    return len(legacy)


def _load_questions(db, dataset_id: str, version: int) -> List[Dict[str, Any]]:
    """Questions of one version, in insertion order, without storage fields."""
    rows = list(
        db.questions.find(
            {"dataset_id": dataset_id, "version": version},
            {"_id": 0, "dataset_id": 0, "version": 0},
        ).sort("_id", 1)
    ) or _legacy_questions(db, dataset_id, version)
    for row in rows:
        row["validation_is_current"] = _validation_is_current(row)
    return rows


# Everything _validation_passed() and _parse_validation_score() read, and nothing
# else: pipeline-stats only tallies verdicts, so pulling question and answer text
# for thousands of questions on every poll is pure waste.
_VALIDATION_PROJECTION = {
    "_id": 0,
    "validation_passed": 1,
    "validation_score": 1,
    "validation_threshold": 1,
}


def _load_validation_verdicts(db, dataset_id: str, version: int) -> List[Dict[str, Any]]:
    """One row per question, carrying only its validation verdict fields."""
    rows = list(
        db.questions.find(
            {"dataset_id": dataset_id, "version": version}, _VALIDATION_PROJECTION
        )
    )
    if rows:
        return rows
    return _legacy_questions(db, dataset_id, version)


def _count_version_questions(db, dataset_id: str, version: int) -> int:
    """Question count — a counted query, never a document read."""
    n = db.questions.count_documents({"dataset_id": dataset_id, "version": version})
    if n:
        return n
    return len(_legacy_questions(db, dataset_id, version))


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    """Normalize a stored timestamp to a naive UTC datetime.

    Version rows written by the API carry BSON dates, but backfills and older
    rows may hold ISO strings, and a tz-aware value must not raise when compared
    with a naive one. Anything unparseable is treated as absent.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _summarize_versions(versions: List[Dict[str, Any]], current_version: int) -> Dict[str, Any]:
    """Fold a dataset's version rows into what the datasets table shows.

    ``last_user_modified_at`` is the most recent moment a person changed the
    set: an in-place edit of any version (``user_updated_at``) or a transform
    that created a new version (``created_at`` of every version after the
    first). Generation appending questions bumps ``updated_at`` on the dataset
    and is deliberately not counted, so a set that nobody touched reports None.
    """
    candidates: List[datetime] = []
    current_user_updated_at: Optional[datetime] = None
    for v in versions:
        edited = _as_utc_datetime(v.get("user_updated_at"))
        if edited is not None:
            candidates.append(edited)
        number = v.get("version")
        if isinstance(number, int) and number > 1:
            created = _as_utc_datetime(v.get("created_at"))
            if created is not None:
                candidates.append(created)
        if number == current_version:
            current_user_updated_at = edited
    return {
        "versions_count": len(versions),
        "last_user_modified_at": max(candidates) if candidates else None,
        "current_version_user_updated_at": current_user_updated_at,
    }


def _assert_dataset_access(db, dataset_id: str, current_user) -> Dict[str, Any]:
    """Load a dataset and check the caller may see it.

    Ownership was enforced only in the dataset LIST; every read by id let any
    authenticated account reach any dataset, and queue listings hand out ids
    freely. With AUTH_DISABLED everyone is an expert and this is a no-op, but it
    has to be right before authentication is switched on.
    """
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if getattr(current_user, "role", None) == "user":
        owner = (dataset.get("metadata") or {}).get("created_by")
        username = getattr(current_user, "username", None)
        if owner and owner != username:
            # 404, not 403: existence itself is not the caller's business.
            raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _merge_dataset_metadata(db, dataset_id: str, version: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a metadata patch into both dataset_versions and datasets.

    Writes dotted field paths rather than replacing the whole ``metadata``
    object. The read-merge-write it replaced raced with the ``$inc`` counters of
    ``bump_gate_progress``: a worker that had read ``chunks_passed_gate`` as 4
    would write the whole object back and roll a concurrent increment to 5 back
    down to 4, so progress could regress or stall below 100%.
    """
    if not patch:
        return {}

    # Projections keep this off the multi-MB questions array.
    version_doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": version}, {"metadata": 1}
    )
    dataset = db.datasets.find_one(
        {"_id": _oid(dataset_id, label="dataset_id")}, {"_id": 1}
    )
    if not version_doc or not dataset:
        return patch

    dotted = {f"metadata.{k}": v for k, v in patch.items()}

    db.dataset_versions.update_one(
        {"dataset_id": dataset_id, "version": version},
        {"$set": dotted},
    )
    db.datasets.update_one(
        {"_id": _oid(dataset_id, label="dataset_id")},
        {"$set": {**dotted, "updated_at": datetime.utcnow()}},
    )
    return {**(version_doc.get("metadata") or {}), **patch}


def _reopen_dataset_processing(db, dataset_id: str) -> bool:
    """Set dataset status back to processing in both metadata stores."""
    try:
        d = db.datasets.find_one(
            {"_id": _oid(dataset_id, label="dataset_id")},
            {"metadata": 1, "current_version": 1},
        )
    except HTTPException:
        # Malformed dataset_id — nothing to reopen.
        return False
    except Exception:
        # A silent False here reads as "dataset already reopened" to the caller,
        # so the actual failure must at least reach the logs.
        logger.exception("Failed to reopen dataset %s", dataset_id)
        return False
    if not d:
        return False
    patch = {"status": "processing"}
    version = d.get("current_version", 1)
    if not db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": version},
        {"_id": 1},
    ):
        return False
    _merge_dataset_metadata(db, dataset_id, version, patch)
    return True


_NON_TERMINAL_DATASET_STATUS = ["processing", "pending", "in_progress", "running", "queued"]


@app.post("/datasets/reconcile-status", response_model=Dict[str, Any])
def reconcile_dataset_status(
    limit: int = Query(1000, ge=1, le=5000),
    _: None = Depends(verify_worker_key),
):
    """Finalize datasets whose tasks are all terminal but whose status is still
    stuck in a non-terminal value (e.g. 'processing').

    Dataset generation is finalized in an event-driven way by the worker when it
    happens to process the last task of a batch. If the worker restarts, the
    last update races, or the tasks were completed by another worker, the
    dataset status is never flipped to 'completed' and the UI shows it frozen at
    'processing'. This sweep reconciles those by recomputing task counts and,
    when there is no remaining pending/processing work, marking the dataset
    completed in both the datasets collection and its current version.
    """
    db = get_db()
    candidates = list(
        db.datasets.find(
            {"metadata.status": {"$in": _NON_TERMINAL_DATASET_STATUS}},
            {"_id": 1, "current_version": 1, "metadata": 1},
        ).limit(limit)
    )
    finalized: List[str] = []

    # All the per-dataset counters in one grouped pass. This sweep runs on the
    # worker's timer over up to `limit` datasets; the previous eight
    # count_documents per candidate multiplied into tens of thousands of
    # queries per run.
    def _flag(cond: Dict[str, Any]) -> Dict[str, Any]:
        return {"$sum": {"$cond": [cond, 1, 0]}}

    candidate_ids = [str(d["_id"]) for d in candidates]
    tallies: Dict[str, Dict[str, int]] = {}
    if candidate_ids:
        rows = db.tasks.aggregate([
            {"$match": {"dataset_id": {"$in": candidate_ids}}},
            {"$group": {
                "_id": "$dataset_id",
                "total": {"$sum": 1},
                "active": _flag({"$in": ["$status", ["pending", "processing"]]}),
                "completed": _flag({"$eq": ["$status", "completed"]}),
                "failed": _flag({"$eq": ["$status", "failed"]}),
                "cancelled": _flag({"$eq": ["$status", "cancelled"]}),
                "gate_total": _flag({"$eq": ["$task_type", "gate"]}),
                "gate_rejected": _flag({"$and": [
                    {"$eq": ["$task_type", "gate"]},
                    {"$or": [
                        {"$eq": ["$gate_passed", False]},
                        {"$eq": ["$status", "skipped"]},  # legacy pre-migration
                    ]},
                ]}),
                "gen_total": _flag({"$not": [{"$in": [
                    {"$ifNull": ["$task_type", ""]}, ["gate", "chunk"],
                ]}]}),
                # Completed gate rejections must not inflate success_rate.
                "completed_rejections": _flag({"$and": [
                    {"$eq": ["$status", "completed"]},
                    {"$eq": ["$gate_passed", False]},
                ]}),
            }},
        ])
        tallies = {row["_id"]: row for row in rows}

    for d in candidates:
        did = str(d["_id"])
        t = tallies.get(did)
        if not t or t["total"] == 0:
            continue
        if t["active"] > 0:
            continue
        total = t["total"]
        completed = t["completed"]
        failed = t["failed"]
        cancelled = t["cancelled"]
        gate_total = t["gate_total"]
        gate_rejected = t["gate_rejected"]
        gen_total = t["gen_total"]
        effective_completed = max(0, completed - t["completed_rejections"])
        if cancelled == total:
            final_status = "cancelled"
        elif gate_total > 0 and gate_rejected == gate_total and gen_total == 0:
            final_status = "no_valid_chunks"
        elif failed > 0 and effective_completed == 0:
            # Nothing came out of the run at all.
            final_status = "failed"
        elif failed > 0:
            # Some questions are missing — say so instead of plain "completed".
            final_status = "completed_with_failures"
        else:
            final_status = "completed"
        patch = {
            "status": final_status,
            "completed_at": datetime.utcnow().isoformat(),
            "total_tasks": total,
            "completed_tasks": effective_completed,
            "failed_tasks": failed,
            "skipped_tasks": gate_rejected,
            "cancelled_tasks": cancelled,
            "success_rate": f"{effective_completed}/{total}",
        }
        if final_status == "no_valid_chunks":
            patch["error"] = "no_valid_chunks"
        version = d.get("current_version", 1)
        _merge_dataset_metadata(db, did, version, patch)
        finalized.append(did)
    return {"scanned": len(candidates), "finalized": len(finalized), "dataset_ids": finalized}


@app.post("/tasks/claim", response_model=List[Dict[str, Any]])
def claim_tasks(
    request: TaskClaimRequest,
    _: None = Depends(verify_worker_key),
):
    """Atomically claim pending tasks (pending -> processing)."""
    db = get_db()
    tasks_collection = db.tasks
    claimed: List[Dict[str, Any]] = []

    for _ in range(request.limit):
        filter_query: Dict[str, Any] = {"status": "pending"}
        if request.queue_name:
            filter_query["queue_name"] = request.queue_name

        task = tasks_collection.find_one_and_update(
            filter_query,
            {
                "$set": {
                    "status": "processing",
                    "worker_id": request.worker_id or "default",
                    "claimed_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
            sort=[("priority", -1), ("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if task is None:
            break
        task["_id"] = str(task["_id"])
        claimed.append(task)

    return claimed


@app.get("/datasets/{dataset_id}/tasks", response_model=List[Dict[str, Any]])
def get_dataset_tasks(
    dataset_id: str,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    tasks_collection = db.tasks

    _assert_dataset_access(db, dataset_id, current_user)

    filter_query = {"dataset_id": dataset_id}
    if status:
        filter_query["status"] = status

    tasks = list(tasks_collection.find(filter_query).sort("created_at", 1))
    for task in tasks:
        task["_id"] = str(task["_id"])
    return tasks


@app.get("/tasks/{task_id}", response_model=Dict[str, Any])
def get_task(task_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    tasks_collection = db.tasks
    task = tasks_collection.find_one({"_id": _oid(task_id, label="task_id")})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Only plain users are scoped; an expert (and the worker, which logs in
    # as one) must still see tasks whose dataset is gone.
    if task.get("dataset_id") and getattr(current_user, "role", None) == "user":
        _assert_dataset_access(db, str(task["dataset_id"]), current_user)
    task["_id"] = str(task["_id"])
    return task

@app.put("/tasks/{task_id}/status", response_model=Dict[str, Any])
def update_task_status(
    task_id: str,
    status_update: TaskStatusUpdate,
    _: None = Depends(verify_worker_key),
):
    db = get_db()
    tasks_collection = db.tasks
    task_oid = _oid(task_id, label="task_id")
    task = tasks_collection.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    current_status = task.get("status", "pending")
    _TERMINAL = frozenset({"completed", "failed", "cancelled", "skipped"})
    # Identity transition onto an already-terminal status: no-op so a
    # late complete from a reclaimed worker cannot overwrite the winner.
    if (
        current_status in _TERMINAL
        and status_update.status == current_status
    ):
        return {
            "task_id": task_id,
            "status": current_status,
            "message": "Task already in terminal status",
        }
    _validate_status_transition(current_status, status_update.status)

    update_data: Dict[str, Any] = {
        "status": status_update.status,
        "updated_at": datetime.utcnow(),
    }
    if status_update.result is not None:
        update_data["result"] = status_update.result
    if status_update.error is not None:
        update_data["error"] = status_update.error
    if status_update.attempts is not None:
        update_data["attempts"] = status_update.attempts
    if status_update.message_code is not None:
        update_data["message_code"] = status_update.message_code
    if status_update.gate_passed is not None:
        update_data["gate_passed"] = status_update.gate_passed

    expected = status_update.expected_status or current_status
    filter_query: Dict[str, Any] = {"_id": task_oid, "status": expected}
    if status_update.worker_id:
        filter_query["worker_id"] = status_update.worker_id

    # Drop stale message_code when the worker does not send a new one
    # (e.g. successful retry after a previous failure).
    update_ops: Dict[str, Any] = {"$set": update_data}
    unset_fields: Dict[str, str] = {}
    if status_update.message_code is None:
        unset_fields["message_code"] = ""
    if (
        status_update.status in ("completed", "pending")
        and status_update.error is None
    ):
        unset_fields["error"] = ""
    if unset_fields:
        update_ops["$unset"] = unset_fields

    result = tasks_collection.update_one(filter_query, update_ops)
    if result.matched_count == 0:
        latest = tasks_collection.find_one({"_id": task_oid}, {"status": 1})
        actual_status = latest.get("status", "unknown") if latest else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Task status conflict (expected {expected}, current {actual_status})",
        )
    return {"task_id": task_id, "status": status_update.status, "message": "Task status updated successfully"}


@app.patch("/tasks/{task_id}/cancel", response_model=Dict[str, Any])
def cancel_task(
    task_id: str,
    current_user: dict = Depends(require_role("expert")),
):
    """Cancel a pending task."""
    db = get_db()
    task_oid = _oid(task_id, label="task_id")
    task = db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending tasks can be cancelled")

    result = db.tasks.update_one(
        {"_id": task_oid, "status": "pending"},
        {"$set": {"status": "cancelled", "updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="Task status changed before cancel")
    return {"task_id": task_id, "status": "cancelled", "message": "Task cancelled"}

@app.delete("/queues/{queue_name}", response_model=Dict[str, Any])
def delete_queue(
    queue_name: str,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    queues_collection = db.queues
    tasks_collection = db.tasks

    queue = queues_collection.find_one({"name": queue_name})
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")

    queue_id = str(queue["_id"])
    tasks_collection.delete_many({"queue_id": queue_id})
    queues_collection.delete_one({"name": queue_name})

    # Without its tasks a dataset still "processing" would never finish, and
    # reconcile skips datasets with no tasks at all: close them out here.
    datasets_cancelled = 0
    patch = {"status": "cancelled", "completed_at": datetime.utcnow().isoformat()}
    for ds in db.datasets.find(
        {
            "metadata.queue_name": queue_name,
            "metadata.status": {"$in": _NON_TERMINAL_DATASET_STATUS},
        },
        {"_id": 1, "current_version": 1},
    ):
        _merge_dataset_metadata(db, str(ds["_id"]), ds.get("current_version", 1), patch)
        datasets_cancelled += 1

    return {
        "queue_name": queue_name,
        "message": "Queue and all its tasks deleted successfully",
        "datasets_cancelled": datasets_cancelled,
    }

@app.post("/datasets/{dataset_id}/add-question", response_model=Dict[str, Any])
def add_question_to_dataset(
    dataset_id: str,
    question: QuestionData,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    current_version = dataset.get("current_version", 1)
    version_doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": current_version}, {"questions": {"$slice": 1}}
    )
    if not version_doc:
        raise HTTPException(status_code=404, detail=f"Version {current_version} not found")
    if version_doc.get("questions"):
        # Not migrated yet: fold it in first, otherwise the collection would
        # hold only the questions added after the deploy.
        _migrate_version_questions(db, dataset_id, current_version)

    idempotency_key = question.idempotency_key or f"{question.chunk_id}:{question.question_type}"
    question_doc = question.dict()
    question_doc["idempotency_key"] = idempotency_key
    # Guarantee an id here rather than trusting the caller: manual editing
    # addresses questions by question_id, and only the worker used to set
    # one — which is why tens of thousands of stored questions had none and
    # could never be edited.
    question_doc["question_id"] = question_doc.get("question_id") or str(uuid.uuid4())
    row = _question_rows([question_doc], dataset_id, current_version)[0]

    # One insert, guarded by the unique index on
    # (dataset_id, version, idempotency_key). The previous $push into an
    # embedded array re-read the whole multi-MB document twice per question
    # and eventually hit MongoDB's 16 MB document limit mid-generation.
    try:
        db.questions.insert_one(row)
    except pymongo.errors.DuplicateKeyError:
        return {
            "dataset_id": dataset_id,
            "question_added": False,
            "duplicate": True,
            "total_questions": _count_version_questions(db, dataset_id, current_version),
            "message": "Question already exists (idempotent)",
        }

    total_questions = _count_version_questions(db, dataset_id, current_version)
    _merge_dataset_metadata(
        db,
        dataset_id,
        current_version,
        {
            "total_questions_generated": total_questions,
            "last_updated": datetime.utcnow().isoformat(),
        },
    )

    return {
        "dataset_id": dataset_id,
        "question_added": True,
        "total_questions": total_questions,
        "message": "Question added successfully",
    }


@app.patch(
    "/datasets/{dataset_id}/versions/{version}/questions",
    response_model=Dict[str, Any],
)
def patch_version_questions(
    dataset_id: str,
    version: int,
    update: QuestionsPatchRequest,
    current_user: dict = Depends(require_role("expert")),
):
    """Edit questions in place, inside an existing version.

    Manual edits must not spawn a version: only `transform` does that. Patching
    in place also keeps generation safe — the worker appends to whatever version
    `current_version` points at, so bumping it mid-run would strand the
    questions produced since the client loaded the page.

    Questions are addressed by `question_id`; nothing is added or removed here,
    which is what keeps a set's question count stable across versions.
    """
    db = get_db()
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    if not db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": version}, {"_id": 1}
    ):
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    if not update.questions:
        raise HTTPException(status_code=400, detail="No questions to update")

    # Wording/rating edits wait for a running revalidation; the export toggle
    # is independent of quality validation and stays available.
    if any(
        QUESTION_CONTENT_FIELDS & q.dict(exclude_unset=True).keys() for q in update.questions
    ):
        _assert_no_active_revalidation(db, dataset_id, version)

    stored = list(
        db.questions.find(
            {"dataset_id": dataset_id, "version": version},
            {
                "_id": 0,
                "question_id": 1,
                # Pre-edit ratings, so the first change can keep the model's value.
                "provocativeness": 1,
                "difficulty": 1,
                "provocativeness_original": 1,
                "difficulty_original": 1,
            },
        )
    )
    by_id = {q["question_id"]: q for q in stored if q.get("question_id")}
    known_ids = set(by_id)
    missing = [q.question_id for q in update.questions if q.question_id not in known_ids]
    if missing:
        # Questions generated before question_id existed cannot be addressed
        # at all. Say that plainly instead of dumping dozens of ids into the
        # UI — the fix is a backfill run, not something the user can do.
        unidentified = sum(1 for q in stored if not q.get("question_id"))
        if unidentified:
            detail = (
                "questions_not_identified: "
                f"{unidentified} of {len(stored)} questions in version {version} "
                "were generated before question ids existed and cannot be edited "
                "until scripts/backfill_data.py --ids-only --apply has been run"
            )
        else:
            shown = ", ".join(sorted(missing)[:5])
            more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            detail = (
                f"unknown_question_ids: {len(missing)} question(s) are not part of "
                f"version {version}: {shown}{more}"
            )
        raise HTTPException(status_code=409, detail=detail)

    now = datetime.utcnow()
    operations = []
    content_edited = False
    for patch in update.questions:
        fields = patch.dict(exclude_unset=True)
        fields.pop("question_id", None)
        changes = {
            key: value
            for key, value in fields.items()
            if key in QUESTION_PATCH_FIELDS
        }
        if not changes:
            continue
        _preserve_original_ratings(by_id.get(patch.question_id, {}), changes)
        if QUESTION_VALIDATED_FIELDS & changes.keys():
            # The verdict no longer describes this wording. Keep it stored
            # (passed survives at the data level), mark it stale.
            changes["validation_is_current"] = False
            changes["needs_revalidation"] = True
        if QUESTION_CONTENT_FIELDS & changes.keys():
            # Derived here, never taken from the client: the patch body is the
            # only place that knows whether wording or ratings changed.
            changes["edit_status"] = "user_edited"
            changes["edited_at"] = now
            content_edited = True
        operations.append(
            UpdateOne(
                {
                    "dataset_id": dataset_id,
                    "version": version,
                    "question_id": patch.question_id,
                },
                {"$set": changes},
            )
        )

    if not operations:
        raise HTTPException(status_code=400, detail="No editable fields to update")

    result = db.questions.bulk_write(operations, ordered=False)
    # Two edit markers: edit_status on each changed question, user_updated_at on
    # the version. Neither moves for an export toggle, otherwise "last modified"
    # would jump on every click of the include-in-export switch.
    if content_edited:
        db.dataset_versions.update_one(
            {"dataset_id": dataset_id, "version": version},
            {"$set": {
                "user_updated_at": now,
                "user_updated_by": getattr(current_user, "username", None) or "public",
            }},
        )

    return {
        "dataset_id": dataset_id,
        "version": version,
        "questions_updated": len(operations),
        "matched": result.matched_count,
        "content_edited": content_edited,
        "user_updated_at": now.isoformat() if content_edited else None,
        "message": "Questions updated successfully",
    }



@app.patch("/datasets/{dataset_id}/metadata", response_model=Dict[str, Any])
def patch_dataset_metadata(
    dataset_id: str,
    metadata_update: Dict[str, Any],
    current_user: dict = Depends(require_role("expert")),
):
    db = get_db()
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    current_version = dataset.get("current_version", 1)
    version_doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": current_version}
    )
    if not version_doc:
        raise HTTPException(status_code=404, detail=f"Version {current_version} not found")

    merged = _merge_dataset_metadata(db, dataset_id, current_version, metadata_update)

    return {
        "dataset_id": dataset_id,
        "version": current_version,
        "message": "Metadata updated successfully",
    }



@app.post("/queues/{queue_name}/retry-failed", response_model=Dict[str, Any])
def retry_failed_tasks(
    queue_name: str,
    current_user: dict = Depends(require_role("expert"))
):
    db = get_db()
    queues_collection = db.queues
    tasks_collection = db.tasks

    queue = queues_collection.find_one({"name": queue_name})
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")

    queue_id = str(queue["_id"])
    failed_tasks = list(tasks_collection.find({"queue_id": queue_id, "status": "failed"}))

    if not failed_tasks:
        return {"queue_name": queue_name, "message": "No failed tasks found to retry", "tasks_retried": 0}

    dataset_ids = {t["dataset_id"] for t in failed_tasks if t.get("dataset_id")}

    result = tasks_collection.update_many(
        {"queue_id": queue_id, "status": "failed"},
        {
            "$set": {
                "status": "pending",
                "error": None,
                "message_code": None,
                "attempts": 0,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    reopened = 0
    for did in dataset_ids:
        if _reopen_dataset_processing(db, did):
            reopened += 1

    return {
        "queue_name": queue_name,
        "message": f"Successfully reset {result.modified_count} failed tasks to pending",
        "tasks_retried": result.modified_count,
        "datasets_reopened": reopened,
    }

@app.post("/tasks/retry-failed", response_model=Dict[str, Any])
def retry_all_failed_tasks(
    queue_name: Optional[str] = None,
    current_user: dict = Depends(require_role("expert")),
):
    """Reset failed tasks back to 'pending' so the worker reprocesses them.

    Failed tasks are almost always transient agent/LLM infrastructure errors
    (gateway HTML errors, connection errors, timeouts), not gate rejections —
    gate-rejected chunks complete without producing a question. Optionally scope
    to a single ``queue_name``; otherwise retries every failed task system-wide.
    """
    db = get_db()
    task_filter: Dict[str, Any] = {"status": "failed"}
    if queue_name:
        task_filter["queue_name"] = queue_name

    failed_count = db.tasks.count_documents(task_filter)
    if failed_count == 0:
        return {"tasks_retried": 0, "datasets_reopened": 0, "message": "No failed tasks found to retry"}

    # Reopen the affected datasets so the UI reflects in-progress work again;
    # the reconcile sweep re-finalizes them once the retries complete.
    dataset_ids = [d for d in db.tasks.distinct("dataset_id", task_filter) if d]

    result = db.tasks.update_many(
        task_filter,
        {
            "$set": {
                "status": "pending",
                "error": None,
                "message_code": None,
                "attempts": 0,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    reopened = 0
    for did in dataset_ids:
        if _reopen_dataset_processing(db, did):
            reopened += 1

    return {
        "tasks_retried": result.modified_count,
        "datasets_reopened": reopened,
        "message": f"Reset {result.modified_count} failed tasks to pending",
    }


@app.post("/datasets/{dataset_id}/document", response_model=Dict[str, Any])
async def upload_dataset_document(
    dataset_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("expert")),
):
    """Store the raw uploaded document in GridFS so the task worker can fetch it
    and run chunking asynchronously in the queue."""
    db = get_db()
    oid = _oid(dataset_id, label="dataset_id")
    if not db.datasets.find_one({"_id": oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Dataset not found")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded document is empty")
    if len(data) > RAW_DOCUMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded document is too large")

    fs = get_gridfs()
    # Replace any previously stored document for this dataset.
    for old in db.fs.files.find({"dataset_id": dataset_id}, {"_id": 1}):
        try:
            fs.delete(old["_id"])
        except Exception:
            logger.warning("Failed to delete old GridFS file %s", old.get("_id"))

    file_id = fs.put(
        data,
        filename=file.filename or "document",
        contentType=file.content_type or "application/octet-stream",
        dataset_id=dataset_id,
    )
    db.datasets.update_one(
        {"_id": oid},
        {"$set": {
            "raw_document_id": str(file_id),
            "raw_document_filename": file.filename or "document",
            "updated_at": datetime.utcnow(),
        }},
    )
    return {
        "dataset_id": dataset_id,
        "file_id": str(file_id),
        "filename": file.filename or "document",
        "size": len(data),
        "message": "Document stored",
    }


def _content_disposition(filename: str) -> str:
    """Build a latin-1 safe Content-Disposition (RFC 5987).

    HTTP headers are encoded as latin-1, so a Cyrillic filename would raise
    UnicodeEncodeError. The ASCII fallback keeps the extension because the
    chunker dispatches processors by it.
    """
    name = filename or "document"
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""
    ascii_stem = stem.encode("ascii", "ignore").decode("ascii").strip() or "document"
    ascii_stem = ascii_stem.replace('"', "").replace("\\", "")
    ascii_ext = ext.encode("ascii", "ignore").decode("ascii")
    ascii_name = f"{ascii_stem}.{ascii_ext}" if ascii_ext else ascii_stem
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )


@app.get("/datasets/{dataset_id}/document")
def download_dataset_document(
    dataset_id: str,
    _: None = Depends(verify_worker_key),
):
    """Return the raw uploaded document (called by the task worker for chunking)."""
    db = get_db()
    meta = db.fs.files.find_one({"dataset_id": dataset_id})
    if not meta:
        raise HTTPException(status_code=404, detail="Document not found")
    fs = get_gridfs()
    grid_out = fs.get(meta["_id"])
    data = grid_out.read()
    filename = meta.get("filename", "document")
    media_type = meta.get("contentType") or "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.post("/datasets/{dataset_id}/chunks", response_model=Dict[str, Any])
def save_chunks(
    dataset_id: str,
    chunks: List[ChunkCreate],
    current_user: dict = Depends(require_role("expert")),
):
    db = get_db()
    dataset = db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    new_indices = [c.chunk_index for c in chunks]
    for c in chunks:
        existing = db.chunks.find_one(
            {"dataset_id": dataset_id, "chunk_index": c.chunk_index},
            {"gate_result": 1, "gate_pending": 1, "gate_passed": 1, "question_types_valid": 1},
        )
        preserve_verdict = bool(
            existing
            and c.gate_pending
            and (
                existing.get("gate_result")
                or existing.get("gate_pending") is False
            )
        )
        doc = {
            "dataset_id": dataset_id,
            "chunk_index": c.chunk_index,
            "chunk_text": c.chunk_text,
            "fragment_data": c.fragment_data,
            "hierarchy_context": c.hierarchy_context,
            "updated_at": datetime.utcnow(),
        }
        if preserve_verdict:
            # Retry of a chunk task must not wipe a verdict already written
            # by a finished gate task.
            doc["gate_result"] = existing.get("gate_result")
            doc["gate_passed"] = existing.get("gate_passed")
            doc["gate_pending"] = existing.get("gate_pending")
            doc["question_types_valid"] = existing.get("question_types_valid")
        else:
            doc["gate_result"] = c.gate_result
            doc["gate_passed"] = c.gate_passed
            doc["gate_pending"] = c.gate_pending
            doc["question_types_valid"] = c.question_types_valid
        db.chunks.update_one(
            {"dataset_id": dataset_id, "chunk_index": c.chunk_index},
            {"$set": doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )

    if new_indices:
        db.chunks.delete_many(
            {"dataset_id": dataset_id, "chunk_index": {"$nin": new_indices}},
        )
    else:
        db.chunks.delete_many({"dataset_id": dataset_id})

    db.datasets.update_one(
        {"_id": _oid(dataset_id, label="dataset_id")},
        {"$set": {"updated_at": datetime.utcnow()}},
    )

    return {
        "dataset_id": dataset_id,
        "chunks_saved": len(chunks),
        "message": "Chunks saved successfully",
    }

@app.get("/datasets/{dataset_id}/chunks", response_model=List[Dict[str, Any]])
def get_chunks(
    dataset_id: str,
    gate_passed_only: bool = True,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    _assert_dataset_access(db, dataset_id, current_user)

    query = {"dataset_id": dataset_id}
    if gate_passed_only:
        query["gate_passed"] = True
    chunks = list(db.chunks.find(query).sort("chunk_index", 1))
    for c in chunks:
        c["_id"] = str(c["_id"])
    return chunks


# Include legacy "skipped" so pre-migration gate tasks still count as terminal.
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped"})

# Which pipeline stage a task belongs to. Same split the frontend uses: generation
# is everything that is neither chunking nor gating, including documents written
# before task_type existed.
_TASK_TYPE_BUCKET = {
    "$let": {
        "vars": {"tt": {"$ifNull": ["$task_type", "generation"]}},
        "in": {"$cond": [{"$in": ["$$tt", ["chunk", "gate"]]}, "$$tt", "generation"]},
    }
}


def _task_matrix(db, dataset_id: str) -> Tuple[Dict[str, Dict[str, int]], List[str], List[str]]:
    """(stage x status) task counts for one dataset, plus the model ids in play.

    A counting aggregation rather than a document scan: a full run holds tens of
    thousands of tasks, and this is polled every few seconds by every open tab.
    The `$addToSet` accumulators are bounded — at most three stages times a
    handful of statuses, each holding the few model ids the run was given.
    """
    counts: Dict[str, Dict[str, int]] = {"chunk": {}, "gate": {}, "generation": {}}
    gen_models: set = set()
    val_models: set = set()
    for row in db.tasks.aggregate([
        {"$match": {"dataset_id": dataset_id}},
        {"$group": {
            "_id": {
                "stage": _TASK_TYPE_BUCKET,
                "status": {"$ifNull": ["$status", "unknown"]},
            },
            "count": {"$sum": 1},
            "gen_models": {"$addToSet": "$generation_model_id"},
            "val_models": {"$addToSet": "$validation_model_id"},
        }},
    ]):
        key = row["_id"]
        counts.setdefault(key["stage"], {})[key["status"]] = int(row["count"])
        gen_models.update(m for m in (row.get("gen_models") or []) if m)
        val_models.update(m for m in (row.get("val_models") or []) if m)
    return counts, sorted(gen_models), sorted(val_models)


def _criterion_pass(gate_result: Optional[Dict[str, Any]], key: str) -> Optional[bool]:
    if not gate_result:
        return None
    raw = gate_result.get(key)
    if isinstance(raw, list) and raw:
        try:
            return int(raw[0]) == 1
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (int, float)):
        return int(raw) == 1
    return None


def _parse_validation_score(score_str: Any) -> Optional[float]:
    if score_str is None:
        return None
    try:
        parts = str(score_str).split("/")
        if len(parts) != 2:
            return None
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _validation_passed(question: Dict[str, Any]) -> Optional[bool]:
    vp = question.get("validation_passed")
    if isinstance(vp, bool):
        return vp
    if isinstance(vp, str):
        v = vp.strip().lower()
        if v in ("true", "passed", "yes", "1"):
            return True
        if v in ("false", "failed", "no", "0"):
            return False
    score = _parse_validation_score(question.get("validation_score"))
    thr_raw = question.get("validation_threshold")
    if score is None or thr_raw in (None, "", "N/A"):
        return None
    try:
        return score >= float(thr_raw)
    except (TypeError, ValueError):
        return None


@app.get("/datasets/{dataset_id}/pipeline-stats", response_model=Dict[str, Any])
def get_pipeline_stats(
    dataset_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Aggregate chunking / gate / generation metrics for one dataset (queue overview)."""
    db = get_db()
    dataset = _assert_dataset_access(db, dataset_id, current_user)

    version = dataset.get("current_version", 1)
    version_doc = db.dataset_versions.find_one(
        {"dataset_id": dataset_id, "version": version},
        {"metadata": 1},
    ) or {}
    metadata = version_doc.get("metadata") or dataset.get("metadata") or {}
    questions = _load_validation_verdicts(db, dataset_id, version)

    task_counts, gen_models, val_models = _task_matrix(db, dataset_id)
    chunks = list(db.chunks.find(
        {"dataset_id": dataset_id},
        {
            "gate_pending": 1,
            "gate_passed": 1,
            "gate_result": 1,
            "question_types_valid": 1,
            "fragment_data.block_type": 1,
        },
    ))

    def _stage_total(stage: str) -> int:
        return sum(task_counts.get(stage, {}).values())

    def _stage_terminal(stage: str) -> int:
        return sum(
            n for s, n in task_counts.get(stage, {}).items()
            if s in _TERMINAL_TASK_STATUSES
        )

    chunk_tasks_total = _stage_total("chunk")
    gate_tasks_total = _stage_total("gate")
    gen_tasks_total = _stage_total("generation")

    pipeline_mode = metadata.get("pipeline_mode") or "full"
    gate_enabled = metadata.get("gate_enabled")
    if gate_enabled is None:
        gate_enabled = pipeline_mode != "generator_validator"
    else:
        gate_enabled = bool(gate_enabled)

    total_chunks_meta = int(metadata.get("total_chunks") or 0)
    total_chunks = total_chunks_meta or len(chunks)

    # ── Chunking ──
    chunk_done = _stage_terminal("chunk")
    if chunk_tasks_total:
        chunking_state = "active" if chunk_done < chunk_tasks_total else "completed"
    else:
        chunking_state = "completed" if total_chunks > 0 else "pending"
    chunk_active = chunking_state == "active"

    # ── Gate criteria from stored chunk verdicts ──
    rejection_reasons: Dict[str, int] = {}
    # Gate outcome per chunker block type (body / list / inset / caption /
    # glossary_entry); legacy chunks without a type count as body.
    by_block_type: Dict[str, Dict[str, int]] = {}

    def _block_bucket(ch: Dict[str, Any]) -> Dict[str, int]:
        fd = ch.get("fragment_data") or {}
        bt = fd.get("block_type") if isinstance(fd, dict) else None
        key = bt if isinstance(bt, str) and bt else "body"
        return by_block_type.setdefault(
            key, {"total": 0, "passed": 0, "rejected": 0, "pending": 0}
        )
    qtypes_valid: Dict[str, int] = {}
    c1_pass = c2_pass = c3_pass = 0
    c1_conf_sum = c2_conf_sum = c3_conf_sum = 0.0
    c1_conf_n = c2_conf_n = c3_conf_n = 0
    passed_count = 0
    rejected_count = 0
    gated_count = 0
    pending_gate_chunks = 0

    for ch in chunks:
        bucket = _block_bucket(ch)
        bucket["total"] += 1
        # Waiting for an async gate verdict: not passed and not rejected yet.
        if ch.get("gate_pending") is True:
            pending_gate_chunks += 1
            bucket["pending"] += 1
            continue
        gr = ch.get("gate_result") or {}
        has_verdict = bool(gr) or ch.get("gate_pending") is False
        if not has_verdict and ch.get("gate_passed") is None:
            continue
        gated_count += 1
        passed_flag = ch.get("gate_passed")
        if passed_flag is None:
            passed_flag = gr.get("passed")
        if passed_flag:
            passed_count += 1
            bucket["passed"] += 1
        else:
            rejected_count += 1
            bucket["rejected"] += 1
            reason = gr.get("rejection_reason") or "unknown"
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

        c1 = _criterion_pass(gr, "c1_chunk_informative")
        c2 = _criterion_pass(gr, "c2_chunk_reference_clarity")
        c3 = _criterion_pass(gr, "c3_chunk_multi_suitability")
        if c1 is True:
            c1_pass += 1
        if c2 is True:
            c2_pass += 1
        if c3 is True:
            c3_pass += 1

        for conf_key, acc_name in (
            ("c1_confidence", "c1"),
            ("c2_confidence", "c2"),
            ("c3_confidence", "c3"),
        ):
            try:
                conf = float(gr.get(conf_key))
            except (TypeError, ValueError):
                continue
            if acc_name == "c1":
                c1_conf_sum += conf
                c1_conf_n += 1
            elif acc_name == "c2":
                c2_conf_sum += conf
                c2_conf_n += 1
            else:
                c3_conf_sum += conf
                c3_conf_n += 1

        for qt in ch.get("question_types_valid") or []:
            qtypes_valid[str(qt)] = qtypes_valid.get(str(qt), 0) + 1

    # Prefer live chunk counts; fall back to metadata only when nothing
    # is in-flight (pending chunks would otherwise resurrect stale counters).
    if gated_count == 0 and pending_gate_chunks == 0:
        passed_count = int(metadata.get("chunks_passed_gate") or 0)
        rejected_count = int(metadata.get("chunks_rejected") or 0)

    gate_task_done = _stage_terminal("gate")
    if not gate_enabled:
        gate_state = "disabled"
        gate_total = total_chunks
        gate_done = total_chunks
    elif gate_tasks_total:
        gate_state = "completed" if gate_task_done >= gate_tasks_total else "active"
        gate_total = gate_tasks_total
        gate_done = gate_task_done
    elif gated_count > 0 or passed_count + rejected_count > 0:
        gate_state = "completed"
        gate_total = gated_count or (passed_count + rejected_count) or total_chunks
        gate_done = gate_total
    else:
        gate_state = "pending" if chunk_active else "completed"
        gate_total = total_chunks
        gate_done = 0 if chunk_active else total_chunks

    if chunk_active and gate_enabled:
        gate_state = "pending"
        gate_done = 0

    def _avg(total: float, n: int) -> Optional[float]:
        if n <= 0:
            return None
        return round(total / n, 4)

    criteria = {
        "c1_pass": c1_pass,
        "c2_pass": c2_pass,
        "c3_pass": c3_pass,
        "c1_avg_confidence": _avg(c1_conf_sum, c1_conf_n),
        "c2_avg_confidence": _avg(c2_conf_sum, c2_conf_n),
        "c3_avg_confidence": _avg(c3_conf_sum, c3_conf_n),
        "scored": gated_count,
    }

    # ── Generation task status counts ──
    by_status: Dict[str, int] = {
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    by_status.update(task_counts.get("generation", {}))

    # `done` counts everything that stopped moving, failures included — that is
    # what decides whether the phase is over. `completed` counts only the tasks
    # that produced a question, which is the number the UI puts on screen.
    gen_done = _stage_terminal("generation")
    gen_completed = by_status.get("completed", 0)
    no_valid = (
        metadata.get("status") == "no_valid_chunks"
        or (
            gate_state in ("completed", "disabled")
            and not gen_tasks_total
            and (rejected_count > 0 and passed_count == 0)
        )
    )
    if chunk_active:
        gen_state = "pending"
    elif no_valid:
        gen_state = "failed"
    elif not gen_tasks_total:
        gen_state = "completed" if gate_state in ("completed", "disabled") else "pending"
    elif gen_done >= gen_tasks_total:
        gen_state = "completed"
    else:
        gen_state = "active"

    # ── Question validation ──
    val_passed = val_failed = 0
    score_sum = 0.0
    score_n = 0
    for q in questions:
        if not isinstance(q, dict):
            continue
        verdict = _validation_passed(q)
        if verdict is True:
            val_passed += 1
        elif verdict is False:
            val_failed += 1
        score = _parse_validation_score(q.get("validation_score"))
        if score is not None:
            score_sum += score
            score_n += 1

    questions_generated = int(
        metadata.get("total_questions_generated")
        if metadata.get("total_questions_generated") is not None
        else len(questions)
    )
    expected_questions = int(metadata.get("expected_questions") or 0)
    if not expected_questions and gen_tasks_total:
        expected_questions = gen_tasks_total

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset.get("name"),
        "meta": {
            "pipeline_mode": pipeline_mode,
            "gate_enabled": gate_enabled,
            "ablation_testing": bool(metadata.get("ablation_testing")),
            "question_types": metadata.get("question_types") or [],
            "queue_name": metadata.get("queue_name"),
            "status": metadata.get("status"),
            "experiment_source_document": metadata.get("experiment_source_document")
            or dataset.get("source_document"),
            "refine_enabled": bool(metadata.get("refine_enabled"))
            if metadata.get("refine_enabled") is not None
            else pipeline_mode == "full",
            # What the chunker threw away (exercises, front matter, debris),
            # as {type: {count, chars}} — from the chunk task's metadata patch.
            "chunking_dropped_blocks": metadata.get("chunking_dropped_blocks")
            if isinstance(metadata.get("chunking_dropped_blocks"), dict)
            else {},
        },
        "chunking": {
            "state": chunking_state,
            "total_chunks": total_chunks,
            "done": 0 if chunk_active else total_chunks,
            "total": total_chunks or chunk_tasks_total or 1,
            "by_status": task_counts.get("chunk", {}),
        },
        "gate": {
            "state": gate_state,
            "total": gate_total,
            "done": gate_done,
            "passed": passed_count,
            "rejected": rejected_count,
            "rejection_reasons": rejection_reasons,
            "by_block_type": by_block_type,
            "criteria": criteria,
            "question_types_valid": qtypes_valid,
            "by_status": task_counts.get("gate", {}),
            "tasks_total": gate_tasks_total,
        },
        "generation": {
            "state": gen_state,
            "total": gen_tasks_total,
            "done": gen_done,
            "completed": gen_completed,
            "by_status": by_status,
            "questions_generated": questions_generated,
            "expected_questions": expected_questions,
            "validation": {
                "passed": val_passed,
                "failed": val_failed,
                "avg_score": _avg(score_sum, score_n),
            },
        },
        # Lets the overview flag unavailable models without scanning every
        # task document to find which models the run actually uses.
        "models": {"generation": gen_models, "validation": val_models},
    }


@app.put("/datasets/{dataset_id}/chunks/{chunk_index}/gate", response_model=Dict[str, Any])
def update_chunk_gate(
    dataset_id: str,
    chunk_index: int,
    update: ChunkGateUpdate,
    _: None = Depends(verify_worker_key),
):
    """Store the async gate verdict for one chunk (called by the task worker)."""
    db = get_db()
    result = db.chunks.update_one(
        {
            "dataset_id": dataset_id,
            "chunk_index": chunk_index,
            "$or": [
                {"gate_pending": True},
                {"gate_pending": {"$exists": False}},
            ],
        },
        {"$set": {
            "gate_result": update.gate_result,
            "gate_passed": update.gate_passed,
            "gate_pending": False,
            "question_types_valid": update.question_types_valid or [],
            "updated_at": datetime.utcnow(),
        }},
    )
    if result.matched_count == 0:
        existing = db.chunks.find_one(
            {"dataset_id": dataset_id, "chunk_index": chunk_index},
            {"gate_passed": 1},
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        # Already has a verdict — idempotent success if the same outcome.
        if existing.get("gate_passed") == update.gate_passed:
            return {
                "dataset_id": dataset_id,
                "chunk_index": chunk_index,
                "gate_passed": update.gate_passed,
                "message": "Chunk gate result already stored",
            }
        raise HTTPException(status_code=409, detail="Chunk already has a gate verdict")
    return {
        "dataset_id": dataset_id,
        "chunk_index": chunk_index,
        "gate_passed": update.gate_passed,
        "message": "Chunk gate result saved",
    }


class GateProgressUpdate(BaseModel):
    passed: bool
    tasks_added: int = 0

    class Config:
        extra = "ignore"


@app.post("/datasets/{dataset_id}/gate-progress", response_model=Dict[str, Any])
def bump_gate_progress(
    dataset_id: str,
    update: GateProgressUpdate,
    _: None = Depends(verify_worker_key),
):
    """Atomically bump gate counters in dataset metadata as async gate tasks finish."""
    db = get_db()
    oid = _oid(dataset_id, label="dataset_id")
    dataset = db.datasets.find_one({"_id": oid}, {"current_version": 1})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    inc = {
        "metadata.chunks_passed_gate": 1 if update.passed else 0,
        "metadata.chunks_rejected": 0 if update.passed else 1,
        "metadata.expected_questions": max(update.tasks_added, 0),
    }
    db.datasets.update_one(
        {"_id": oid},
        {"$inc": inc, "$set": {"updated_at": datetime.utcnow()}},
    )
    db.dataset_versions.update_one(
        {"dataset_id": dataset_id, "version": dataset.get("current_version", 1)},
        {"$inc": inc},
    )
    return {"dataset_id": dataset_id, "message": "Gate progress updated"}


@app.delete("/datasets/{dataset_id}/chunks", response_model=Dict[str, Any])
def delete_chunks(
    dataset_id: str,
    current_user: dict = Depends(require_role("expert")),
):
    db = get_db()
    if not db.datasets.find_one({"_id": _oid(dataset_id, label="dataset_id")}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Dataset not found")

    result = db.chunks.delete_many({"dataset_id": dataset_id})
    return {
        "dataset_id": dataset_id,
        "chunks_deleted": result.deleted_count,
        "message": "Chunks deleted successfully",
    }

# ---------------------------------------------------------------------------
# Quality revalidation (повторная проверка качества)
#
# Re-runs only the quality validator over questions of one version. Runs live in
# their own collections, not in `tasks`: a revalidation task there would be
# deduped by chunk_id, would flip the dataset status via finalize/reconcile, be
# reopened by retry-failed and be counted as generation in pipeline-stats.
# ---------------------------------------------------------------------------

REVALIDATION_STUCK_MINUTES = int(os.getenv("WORKER_STUCK_THRESHOLD_MINUTES", "15"))
REVALIDATION_MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_TASK_ATTEMPTS", "3"))

# Fields the worker may write back on a successful check (same format as the
# generation pipeline writes, see task_worker validation_fields_from_result).
REVALIDATION_RESULT_FIELDS = (
    "validation_passed",
    "validation_score",
    "validation_threshold",
    "validation_details",
    "validation_justifications",
)


class RevalidationStart(BaseModel):
    scope: str
    # Validator for this run; omitted → the version's validation model.
    validation_model_id: Optional[str] = None

    class Config:
        extra = "ignore"


class RevalidationClaim(BaseModel):
    worker_id: Optional[str] = "default"

    class Config:
        extra = "ignore"


class RevalidationItemResult(BaseModel):
    worker_id: Optional[str] = None
    result: Optional[Dict[str, Optional[str]]] = None
    error: Optional[str] = None

    class Config:
        extra = "ignore"


def _active_revalidation(db, dataset_id: str, version: int) -> Optional[Dict[str, Any]]:
    return db.revalidation_runs.find_one(
        {"dataset_id": dataset_id, "version": int(version), "status": "processing"}
    )


def _assert_no_active_revalidation(db, dataset_id: str, version: int) -> None:
    if _active_revalidation(db, dataset_id, version):
        raise HTTPException(
            status_code=409,
            detail=(
                "revalidation_in_progress: quality revalidation is running for "
                f"version {version}; editing is unavailable until it finishes"
            ),
        )


def _version_metadata(db, dataset: Dict[str, Any], version: int) -> Dict[str, Any]:
    doc = db.dataset_versions.find_one(
        {"dataset_id": str(dataset["_id"]), "version": int(version)}, {"metadata": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    return {**(dataset.get("metadata") or {}), **(doc.get("metadata") or {})}


_REVALIDATION_QUESTION_PROJECTION = {
    "_id": 0,
    "question_id": 1,
    "edit_status": 1,
    "validation_is_current": 1,
    "needs_revalidation": 1,
    "validation_passed": 1,
}


def _revalidation_questions(db, dataset_id: str, version: int) -> List[Dict[str, Any]]:
    rows = list(
        db.questions.find(
            {"dataset_id": dataset_id, "version": int(version)},
            _REVALIDATION_QUESTION_PROJECTION,
        )
    )
    if not rows:
        rows = [
            {k: q.get(k) for k in _REVALIDATION_QUESTION_PROJECTION if k != "_id"}
            for q in _legacy_questions(db, dataset_id, version)
        ]
    return rows


def _scope_question_ids(questions: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Question ids per scope (only addressable questions can be revalidated)."""
    scopes: Dict[str, List[str]] = {s: [] for s in ("all", "required", "user_edited", "paraphrased")}
    for q in questions:
        qid = q.get("question_id")
        if not qid:
            continue
        scopes["all"].append(qid)
        if not _validation_is_current(q):
            scopes["required"].append(qid)
        if q.get("edit_status") == "user_edited":
            scopes["user_edited"].append(qid)
        elif q.get("edit_status") == "paraphrased":
            scopes["paraphrased"].append(qid)
    return scopes


def _verdict_snapshots(
    db, dataset_id: str, version: int, ids: List[str], default_model: Optional[str]
) -> Dict[str, Dict[str, Any]]:
    """Current validation_* of each question, with the model that produced it."""
    projection = {"_id": 0, "question_id": 1, "validation_model_id": 1}
    projection.update({k: 1 for k in REVALIDATION_RESULT_FIELDS})
    snaps: Dict[str, Dict[str, Any]] = {}
    for q in db.questions.find(
        {"dataset_id": dataset_id, "version": int(version), "question_id": {"$in": list(ids)}},
        projection,
    ):
        snap = {k: q.get(k) for k in REVALIDATION_RESULT_FIELDS}
        snap["validation_model_id"] = q.get("validation_model_id") or default_model
        snaps[q["question_id"]] = snap
    return snaps


def _run_counts(db, run_id: str) -> Dict[str, int]:
    counts = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    for row in db.revalidation_items.aggregate([
        {"$match": {"run_id": run_id}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"]] = row["n"]
    return counts


def _refresh_revalidation_run(db, run_id: str) -> Optional[Dict[str, Any]]:
    """Recount a run; close it once nothing is pending or processing."""
    counts = _run_counts(db, run_id)
    total = sum(counts.values())
    now = datetime.utcnow()
    fields: Dict[str, Any] = {
        "total": total,
        "succeeded": counts["done"],
        "errors": counts["error"],
        "updated_at": now,
    }
    if counts["pending"] == 0 and counts["processing"] == 0:
        if counts["error"] == 0:
            status = "completed"
        elif counts["done"] == 0:
            status = "failed"
        else:
            status = "completed_with_errors"
        fields.update({"status": status, "finished_at": now})
    return db.revalidation_runs.find_one_and_update(
        {"run_id": run_id}, {"$set": fields}, return_document=ReturnDocument.AFTER
    )


def _serialize_run(run: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not run:
        return None
    return {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "scope": run.get("scope"),
        "total": run.get("total", 0),
        "succeeded": run.get("succeeded", 0),
        "errors": run.get("errors", 0),
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "created_by": run.get("created_by"),
        "validation_model_id": run.get("validation_model_id"),
    }


def _revalidation_state(db, dataset: Dict[str, Any], version: int) -> Dict[str, Any]:
    dataset_id = str(dataset["_id"])
    metadata = _version_metadata(db, dataset, version)
    questions = _revalidation_questions(db, dataset_id, version)
    scopes = _scope_question_ids(questions)
    required = sum(1 for q in questions if not _validation_is_current(q))
    active = _active_revalidation(db, dataset_id, version)
    last = active or db.revalidation_runs.find_one(
        {"dataset_id": dataset_id, "version": int(version)}, sort=[("created_at", -1)]
    )
    # Priority from ТЗ 11.2: processing > required > last result / not started.
    if active:
        primary = "processing"
    elif required:
        primary = "required"
    elif not last:
        primary = "not_started"
    else:
        primary = last.get("status") or "not_started"
    retryable = 0
    if last and not active and last.get("status") in ("completed_with_errors", "failed"):
        retryable = db.revalidation_items.count_documents(
            {"run_id": last["run_id"], "status": "error"}
        )
    return {
        "dataset_id": dataset_id,
        "version": int(version),
        "available": metadata.get("status") in FINISHED_DATASET_STATUSES,
        "primary_status": primary,
        "required_count": required,
        "total_count": len(questions),
        "scope_counts": {k: len(v) for k, v in scopes.items()},
        "active_run": _serialize_run(active),
        "last_run": _serialize_run(last),
        "retryable_count": retryable,
    }


@app.get("/datasets/{dataset_id}/versions/{version}/revalidation", response_model=Dict[str, Any])
def get_revalidation_state(
    dataset_id: str,
    version: int,
    current_user: dict = Depends(get_current_user),
):
    """State of quality revalidation for one version (accordion in the set card)."""
    db = get_db()
    dataset = _assert_dataset_access(db, dataset_id, current_user)
    return _revalidation_state(db, dataset, version)


@app.post("/datasets/{dataset_id}/versions/{version}/revalidation", response_model=Dict[str, Any])
def start_revalidation(
    dataset_id: str,
    version: int,
    request: RevalidationStart,
    current_user: dict = Depends(require_role("expert")),
):
    """Start a new revalidation run over the chosen scope of one version."""
    db = get_db()
    dataset = _assert_dataset_access(db, dataset_id, current_user)
    metadata = _version_metadata(db, dataset, version)
    if metadata.get("status") not in FINISHED_DATASET_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="revalidation_not_available: the question set is not ready yet",
        )
    scope = (request.scope or "").strip()
    if scope not in REVALIDATION_SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown revalidation scope {scope!r}")
    _assert_no_active_revalidation(db, dataset_id, version)
    if not db.questions.count_documents({"dataset_id": dataset_id, "version": int(version)}, limit=1):
        # Results are written to the questions collection: move a legacy
        # embedded version there first.
        _migrate_version_questions(db, dataset_id, int(version))
    ids = _scope_question_ids(_revalidation_questions(db, dataset_id, version))[scope]
    if not ids:
        raise HTTPException(status_code=400, detail=f"No questions in scope {scope!r}")
    model_id = (request.validation_model_id or "").strip() or metadata.get("validation_model_id")
    previous = _verdict_snapshots(db, dataset_id, int(version), ids, metadata.get("validation_model_id"))

    now = datetime.utcnow()
    run_id = uuid.uuid4().hex
    run = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "version": int(version),
        "scope": scope,
        "status": "processing",
        "total": len(ids),
        "succeeded": 0,
        "errors": 0,
        "validation_model_id": model_id,
        "document_kind": metadata.get("document_kind") or "normative",
        "created_by": getattr(current_user, "username", None) or "public",
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }
    try:
        db.revalidation_runs.insert_one(run)
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail=f"revalidation_in_progress: another run for version {version} has started",
        )
    db.revalidation_items.insert_many([
        {
            "run_id": run_id,
            "dataset_id": dataset_id,
            "version": int(version),
            "question_id": qid,
            "status": "pending",
            "attempts": 0,
            "error": None,
            # Verdict before this run: the question's fields get overwritten,
            # the run keeps both (comparison export, cross-model experiments).
            "previous": previous.get(qid),
            "result": None,
            "created_at": now,
            "updated_at": now,
        }
        for qid in ids
    ])
    return _revalidation_state(db, dataset, version)


@app.post("/datasets/{dataset_id}/versions/{version}/revalidation/retry", response_model=Dict[str, Any])
def retry_revalidation(
    dataset_id: str,
    version: int,
    current_user: dict = Depends(require_role("expert")),
):
    """Re-run only the questions the last run failed on (technical errors).

    Continues that run rather than starting a new one (ТЗ 12.6): the progress
    counter starts from the questions it already processed.
    """
    db = get_db()
    dataset = _assert_dataset_access(db, dataset_id, current_user)
    _assert_no_active_revalidation(db, dataset_id, version)
    last = db.revalidation_runs.find_one(
        {"dataset_id": dataset_id, "version": int(version)}, sort=[("created_at", -1)]
    )
    if not last or last.get("status") not in ("completed_with_errors", "failed"):
        raise HTTPException(status_code=409, detail="nothing_to_retry: the last run has no errors")
    now = datetime.utcnow()
    try:
        db.revalidation_runs.update_one(
            {"run_id": last["run_id"]},
            {"$set": {"status": "processing", "finished_at": None, "updated_at": now}},
        )
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(status_code=409, detail="revalidation_in_progress")
    reset = db.revalidation_items.update_many(
        {"run_id": last["run_id"], "status": "error"},
        {"$set": {"status": "pending", "attempts": 0, "error": None, "updated_at": now}},
    )
    if not reset.modified_count:
        _refresh_revalidation_run(db, last["run_id"])
    return _revalidation_state(db, dataset, version)


def _requeue_stuck_revalidation_items(db) -> None:
    """Items a worker dropped mid-check: back to pending, or error after N tries."""
    cutoff = datetime.utcnow() - timedelta(minutes=REVALIDATION_STUCK_MINUTES)
    stuck = list(db.revalidation_items.find(
        {"status": "processing", "updated_at": {"$lt": cutoff}}, {"_id": 1, "run_id": 1, "attempts": 1}
    ).limit(200))
    touched = set()
    for item in stuck:
        if int(item.get("attempts") or 0) >= REVALIDATION_MAX_ATTEMPTS:
            update = {"status": "error", "error": "interrupted", "updated_at": datetime.utcnow()}
        else:
            update = {"status": "pending", "updated_at": datetime.utcnow()}
        res = db.revalidation_items.update_one(
            {"_id": item["_id"], "status": "processing"}, {"$set": update}
        )
        if res.modified_count:
            touched.add(item["run_id"])
    for run_id in touched:
        _refresh_revalidation_run(db, run_id)


def _chunk_text_for(db, dataset_id: str, chunk_id: Any) -> str:
    try:
        idx = int(chunk_id)
    except (TypeError, ValueError):
        return ""
    doc = db.chunks.find_one({"dataset_id": dataset_id, "chunk_index": idx}, {"chunk_text": 1})
    return (doc or {}).get("chunk_text") or ""


def _verdict_summary(v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    return {
        "passed": v.get("validation_passed"),
        "score": v.get("validation_score"),
        "threshold": v.get("validation_threshold"),
        "justifications": v.get("validation_justifications"),
        "model_id": v.get("validation_model_id"),
    }


@app.get(
    "/datasets/{dataset_id}/versions/{version}/revalidation/runs/{run_id}/items",
    response_model=Dict[str, Any],
)
def get_revalidation_run_items(
    dataset_id: str,
    version: int,
    run_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Per-question verdicts of one run, before and after (comparison export)."""
    db = get_db()
    _assert_dataset_access(db, dataset_id, current_user)
    run = db.revalidation_runs.find_one(
        {"run_id": run_id, "dataset_id": dataset_id, "version": int(version)}
    )
    if not run:
        raise HTTPException(status_code=404, detail="Revalidation run not found")
    items = list(db.revalidation_items.find({"run_id": run_id}).sort("created_at", 1))
    texts = {
        q["question_id"]: q
        for q in db.questions.find(
            {"dataset_id": dataset_id, "version": int(version),
             "question_id": {"$in": [i["question_id"] for i in items]}},
            {"_id": 0, "question_id": 1, "question_type": 1, "task": 1},
        )
    }
    return {
        "run": _serialize_run(run),
        "items": [
            {
                "question_id": i["question_id"],
                "question_type": texts.get(i["question_id"], {}).get("question_type"),
                "task": texts.get(i["question_id"], {}).get("task"),
                "status": i.get("status"),
                "error": i.get("error"),
                "previous": _verdict_summary(i.get("previous")),
                "result": _verdict_summary(i.get("result")),
            }
            for i in items
        ],
    }


@app.post("/revalidation/claim", response_model=Dict[str, Any])
def claim_revalidation_item(
    request: RevalidationClaim,
    _: None = Depends(verify_worker_key),
):
    """Claim one pending revalidation item with everything the validator needs.

    Returns {"item": null} when there is nothing to do.
    """
    db = get_db()
    _requeue_stuck_revalidation_items(db)
    while True:
        now = datetime.utcnow()
        item = db.revalidation_items.find_one_and_update(
            {"status": "pending"},
            {
                "$set": {
                    "status": "processing",
                    "worker_id": request.worker_id or "default",
                    "claimed_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if item is None:
            return {"item": None}
        run = db.revalidation_runs.find_one({"run_id": item["run_id"]}) or {}
        question = db.questions.find_one(
            {"dataset_id": item["dataset_id"], "version": item["version"], "question_id": item["question_id"]},
            {"_id": 0, "dataset_id": 0, "version": 0},
        )
        if question is None:
            question = next(
                (q for q in _legacy_questions(db, item["dataset_id"], item["version"])
                 if q.get("question_id") == item["question_id"]),
                None,
            )
        if question is None:
            db.revalidation_items.update_one(
                {"_id": item["_id"]},
                {"$set": {"status": "error", "error": "question_not_found", "updated_at": now}},
            )
            _refresh_revalidation_run(db, item["run_id"])
            continue
        source = question.get("source_chunk") or _chunk_text_for(db, item["dataset_id"], question.get("chunk_id"))
        return {
            "item": {
                "item_id": str(item["_id"]),
                "run_id": item["run_id"],
                "dataset_id": item["dataset_id"],
                "version": item["version"],
                "question_id": item["question_id"],
                "attempts": item.get("attempts", 1),
                "validation_model_id": run.get("validation_model_id"),
                "document_kind": run.get("document_kind") or "normative",
                "question": {
                    k: question.get(k)
                    for k in ("question_type", "task", "options", "correct_answer", "chunk_id")
                },
                "source_text": source,
            }
        }


@app.put("/revalidation/items/{item_id}/heartbeat", response_model=Dict[str, Any])
def heartbeat_revalidation_item(item_id: str, _: None = Depends(verify_worker_key)):
    db = get_db()
    res = db.revalidation_items.update_one(
        {"_id": _oid(item_id, label="item_id"), "status": "processing"},
        {"$set": {"updated_at": datetime.utcnow()}},
    )
    if not res.matched_count:
        raise HTTPException(status_code=409, detail="item is not processing")
    return {"ok": True}


@app.put("/revalidation/items/{item_id}", response_model=Dict[str, Any])
def complete_revalidation_item(
    item_id: str,
    payload: RevalidationItemResult,
    _: None = Depends(verify_worker_key),
):
    """Store a revalidation result, or record a technical error.

    A technical error never becomes passed=False and never touches the
    question (ТЗ 12.4): its previous verdict and currency stay as they were.
    """
    db = get_db()
    oid = _oid(item_id, label="item_id")
    item = db.revalidation_items.find_one({"_id": oid})
    if not item:
        raise HTTPException(status_code=404, detail="Revalidation item not found")
    if item.get("status") != "processing":
        raise HTTPException(status_code=409, detail=f"item is {item.get('status')}, not processing")
    now = datetime.utcnow()
    status, error = "error", (payload.error or "unknown_error")[:500]
    if payload.result is not None and not payload.error:
        fields = {k: payload.result.get(k) for k in REVALIDATION_RESULT_FIELDS if k in payload.result}
        run = db.revalidation_runs.find_one({"run_id": item["run_id"]}, {"validation_model_id": 1}) or {}
        verdict = {**fields, "validation_model_id": run.get("validation_model_id")}
        res = db.questions.update_one(
            {"dataset_id": item["dataset_id"], "version": item["version"], "question_id": item["question_id"]},
            {"$set": {
                **verdict,
                "revalidation_run_id": item["run_id"],
                "validation_is_current": True,
                "needs_revalidation": False,
                "revalidated_at": now,
            }},
        )
        if res.matched_count:
            status, error = "done", None
        else:
            error = "question_not_found"
    item_fields: Dict[str, Any] = {"status": status, "error": error, "updated_at": now}
    if status == "done":
        item_fields["result"] = verdict
    updated = db.revalidation_items.update_one(
        {"_id": oid, "status": "processing"},
        {"$set": item_fields},
    )
    if not updated.modified_count:
        raise HTTPException(status_code=409, detail="item was completed concurrently")
    run = _refresh_revalidation_run(db, item["run_id"])
    return {"item_id": item_id, "status": status, "run": _serialize_run(run)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8789)