from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
import pymongo
from bson import ObjectId
import json
from config import MONGO_DB_PATH, MONGO_HOST, MONGO_PORT, MONGO_USERNAME, MONGO_PASSWORD, MONGO_DB_NAME

from fastapi import Depends
from auth_router import router as auth_router
from auth_utils import get_current_user, require_role, seed_users_locked

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        seed_users_locked()  
    except Exception:
        import logging; logging.getLogger(__name__).exception("seed_users_locked failed")
    yield

app = FastAPI(title="GenA Dataset API", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router, tags=["auth"])

# MongoDB connection
def get_mongo_client():
    if MONGO_USERNAME and MONGO_PASSWORD:
        connection_string = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/"
    else:
        connection_string = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
    return pymongo.MongoClient(connection_string)

def get_db():
    client = get_mongo_client()
    return client[MONGO_DB_NAME]

# Pydantic models
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

class DatasetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    source_document: str
    questions: List[QuestionData]
    metadata: Optional[Dict[str, Any]] = None

class DatasetUpdate(BaseModel):
    questions: List[QuestionData]
    metadata: Optional[Dict[str, Any]] = None

class DatasetVersion(BaseModel):
    version: int
    created_at: datetime
    questions: List[QuestionData]
    metadata: Optional[Dict[str, Any]] = None

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

class QueueCreate(BaseModel):
    name: str
    description: Optional[str] = None
    priority: Optional[int] = 1

class TaskStatusUpdate(BaseModel):
    status: str
    result: Optional[Dict] = None
    error: Optional[str] = None
    # Number of times the worker has picked up this task; bumped by the worker
    # when it recovers a stuck task.  Used to cap retries so a poison task
    # cannot ping-pong between ``processing`` and ``pending`` forever.
    attempts: Optional[int] = None

class ChunkCreate(BaseModel):
    chunk_index: int
    chunk_text: str
    fragment_data: Optional[Dict[str, Any]] = None
    gate_result: Optional[Dict[str, Any]] = None
    gate_passed: bool = True
    question_types_valid: Optional[List[str]] = None

# API endpoints
@app.post("/datasets/", response_model=Dict[str, Any])
async def create_dataset(
    dataset: DatasetCreate,
    current_user: dict = Depends(require_role("expert"))
):
    try:
        db = get_db()
        datasets_collection = db.datasets

        dataset_doc = {
            "name": dataset.name,
            "description": dataset.description,
            "source_document": dataset.source_document,
            "current_version": 1,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "metadata": dataset.metadata or {}
        }

        result = datasets_collection.insert_one(dataset_doc)
        dataset_id = str(result.inserted_id)

        version_doc = {
            "dataset_id": dataset_id,
            "version": 1,
            "created_at": datetime.utcnow(),
            "questions": [q.dict() for q in dataset.questions],
            "metadata": dataset.metadata or {}
        }

        db.dataset_versions.insert_one(version_doc)

        return {
            "dataset_id": dataset_id,
            "name": dataset.name,
            "version": 1,
            "message": "Dataset created successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating dataset: {str(e)}")

@app.get("/datasets/", response_model=List[Dict[str, Any]])
async def list_datasets(current_user: dict = Depends(get_current_user)):
    try:
        db = get_db()
        datasets = list(db.datasets.find({}, {
            "name": 1, "description": 1, "source_document": 1,
            "current_version": 1, "created_at": 1, "updated_at": 1, "metadata": 1,
        }))
        for dataset in datasets:
            did = str(dataset["_id"])
            dataset["_id"] = did
            dataset["chunks_count"] = db.chunks.count_documents({"dataset_id": did})
            dataset["chunks_valid"] = db.chunks.count_documents({"dataset_id": did, "gate_passed": True})
        return datasets
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing datasets: {str(e)}")

@app.get("/datasets/{dataset_id}", response_model=Dict[str, Any])
async def get_dataset(dataset_id: str, version: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

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
            "name": dataset["name"],
            "description": dataset["description"],
            "source_document": dataset["source_document"],
            "current_version": dataset["current_version"],
            "requested_version": version,
            "created_at": dataset["created_at"],
            "updated_at": dataset["updated_at"],
            "questions": version_doc["questions"],
            "metadata": version_doc.get("metadata", {})
        }
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting dataset: {str(e)}")

@app.get("/datasets/{dataset_id}/versions", response_model=List[Dict[str, Any]])
async def get_dataset_versions(dataset_id: str, current_user: dict = Depends(get_current_user)):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        versions = list(db.dataset_versions.find(
            {"dataset_id": dataset_id},
            {"_id": 0, "version": 1, "created_at": 1, "metadata": 1}
        ).sort("version", 1))

        return versions

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting versions: {str(e)}")

@app.put("/datasets/{dataset_id}", response_model=Dict[str, Any])
async def update_dataset(
    dataset_id: str,
    update: DatasetUpdate,
    current_user: dict = Depends(require_role("expert"))
):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        new_version = dataset.get("current_version", 0) + 1

        version_doc = {
            "dataset_id": dataset_id,
            "version": new_version,
            "created_at": datetime.utcnow(),
            "questions": [q.dict() for q in update.questions],
            "metadata": update.metadata or {}
        }

        db.dataset_versions.insert_one(version_doc)

        db.datasets.update_one(
            {"_id": ObjectId(dataset_id)},
            {"$set": {"current_version": new_version, "updated_at": datetime.utcnow()}}
        )

        return {"dataset_id": dataset_id, "new_version": new_version, "message": "Dataset updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating dataset: {str(e)}")

@app.delete("/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    current_user: dict = Depends(require_role("expert"))
):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        db.dataset_versions.delete_many({"dataset_id": dataset_id})
        db.chunks.delete_many({"dataset_id": dataset_id})
        db.datasets.delete_one({"_id": ObjectId(dataset_id)})

        return {"message": "Dataset deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting dataset: {str(e)}")

# Task Queue endpoints
@app.post("/queues/", response_model=Dict[str, Any])
async def create_queue(
    queue: QueueCreate,
    current_user: dict = Depends(get_current_user)
):
    try:
        db = get_db()
        queues_collection = db.queues

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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating queue: {str(e)}")

@app.get("/queues/", response_model=List[Dict[str, Any]])
async def list_queues(current_user: dict = Depends(get_current_user)):
    try:
        db = get_db()
        queues_collection = db.queues
        tasks_collection = db.tasks

        queues = list(queues_collection.find({}))

        for queue in queues:
            queue_id = str(queue["_id"])

            stats = tasks_collection.aggregate([
                {"$match": {"queue_id": queue_id}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ])

            queue["pending_count"] = 0
            queue["processing_count"] = 0
            queue["completed_count"] = 0
            queue["failed_count"] = 0
            queue["cancelled_count"] = 0

            for stat in stats:
                s = stat["_id"]; c = stat["count"]
                if s == "pending": queue["pending_count"] = c
                elif s == "processing": queue["processing_count"] = c
                elif s == "completed": queue["completed_count"] = c
                elif s == "failed": queue["failed_count"] = c
                elif s == "cancelled": queue["cancelled_count"] = c

            queue["task_count"] = (
                queue["pending_count"] +
                queue["processing_count"] +
                queue["completed_count"] +
                queue["failed_count"] +
                queue["cancelled_count"]
            )
            queue["_id"] = str(queue["_id"])

        return queues

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing queues: {str(e)}")

@app.post("/queues/{queue_name}/tasks/", response_model=Dict[str, Any])
async def add_tasks_to_queue(
    queue_name: str,
    tasks: List[TaskData],
    current_user: dict = Depends(get_current_user)
):
    try:
        db = get_db()
        queues_collection = db.queues
        tasks_collection = db.tasks

        queue = queues_collection.find_one({"name": queue_name})
        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")

        queue_id = str(queue["_id"])
        inserted_tasks = []

        for task_data in tasks:
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
                "status": "pending",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "result": None,
                "error": None
            }
            result = tasks_collection.insert_one(task_doc)
            inserted_tasks.append(str(result.inserted_id))

        return {
            "queue_name": queue_name,
            "tasks_added": len(inserted_tasks),
            "task_ids": inserted_tasks,
            "message": f"Added {len(inserted_tasks)} tasks to queue '{queue_name}'"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding tasks: {str(e)}")

@app.get("/queues/{queue_name}/tasks/", response_model=List[Dict[str, Any]])
async def get_queue_tasks(
    queue_name: str,
    status: Optional[str] = None,
    limit: Optional[int] = 100,
    current_user: dict = Depends(get_current_user)
):
    try:
        db = get_db()
        queues_collection = db.queues
        tasks_collection = db.tasks

        queue = queues_collection.find_one({"name": queue_name})
        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")

        queue_id = str(queue["_id"])
        filter_query = {"queue_id": queue_id}
        if status:
            filter_query["status"] = status

        tasks = list(tasks_collection.find(filter_query).sort("created_at", 1).limit(limit))
        for task in tasks:
            task["_id"] = str(task["_id"])
        return tasks

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting tasks: {str(e)}")

@app.get("/tasks/stuck", response_model=List[Dict[str, Any]])
async def get_stuck_tasks(threshold_minutes: int = 10):
    """Return tasks stuck in 'processing' longer than threshold."""
    try:
        db = get_db()
        cutoff = datetime.utcnow() - __import__('datetime').timedelta(minutes=threshold_minutes)
        tasks = list(db.tasks.find({
            "status": "processing",
            "updated_at": {"$lt": cutoff},
        }).limit(100))
        for task in tasks:
            task["_id"] = str(task["_id"])
        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting stuck tasks: {str(e)}")


_NON_TERMINAL_DATASET_STATUS = ["processing", "pending", "in_progress", "running", "queued"]


@app.post("/datasets/reconcile-status", response_model=Dict[str, Any])
async def reconcile_dataset_status(limit: int = 1000):
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
    try:
        db = get_db()
        candidates = list(
            db.datasets.find(
                {"metadata.status": {"$in": _NON_TERMINAL_DATASET_STATUS}},
                {"_id": 1, "current_version": 1, "metadata": 1},
            ).limit(limit)
        )
        finalized: List[str] = []
        for d in candidates:
            did = str(d["_id"])
            total = db.tasks.count_documents({"dataset_id": did})
            if total == 0:
                continue
            active = db.tasks.count_documents(
                {"dataset_id": did, "status": {"$in": ["pending", "processing"]}}
            )
            if active > 0:
                continue
            completed = db.tasks.count_documents({"dataset_id": did, "status": "completed"})
            failed = db.tasks.count_documents({"dataset_id": did, "status": "failed"})
            patch = {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "total_tasks": total,
                "completed_tasks": completed,
                "failed_tasks": failed,
                "success_rate": f"{completed}/{total}",
            }
            version = d.get("current_version", 1)
            db.datasets.update_one(
                {"_id": d["_id"]},
                {"$set": {
                    "metadata": {**(d.get("metadata") or {}), **patch},
                    "updated_at": datetime.utcnow(),
                }},
            )
            vdoc = db.dataset_versions.find_one({"dataset_id": did, "version": version})
            if vdoc is not None:
                db.dataset_versions.update_one(
                    {"dataset_id": did, "version": version},
                    {"$set": {"metadata": {**(vdoc.get("metadata") or {}), **patch}}},
                )
            finalized.append(did)
        return {"scanned": len(candidates), "finalized": len(finalized), "dataset_ids": finalized}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reconciling dataset status: {str(e)}")


@app.get("/tasks/pending", response_model=List[Dict[str, Any]])
async def get_pending_tasks(queue_name: Optional[str] = None, limit: Optional[int] = 10):
    try:
        db = get_db()
        tasks_collection = db.tasks
        filter_query = {"status": "pending"}
        if queue_name:
            filter_query["queue_name"] = queue_name

        tasks = list(tasks_collection.find(filter_query).sort([
            ("priority", -1), ("created_at", 1)
        ]).limit(limit))

        for task in tasks:
            task["_id"] = str(task["_id"])
        return tasks

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting pending tasks: {str(e)}")

@app.get("/datasets/{dataset_id}/tasks", response_model=List[Dict[str, Any]])
async def get_dataset_tasks(
    dataset_id: str,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    try:
        db = get_db()
        tasks_collection = db.tasks

        filter_query = {"dataset_id": dataset_id}
        if status:
            filter_query["status"] = status

        tasks = list(tasks_collection.find(filter_query).sort("created_at", 1))
        for task in tasks:
            task["_id"] = str(task["_id"])
        return tasks

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting dataset tasks: {str(e)}")

@app.get("/tasks/{task_id}", response_model=Dict[str, Any])
async def get_task(task_id: str):
    try:
        db = get_db()
        tasks_collection = db.tasks
        task = tasks_collection.find_one({"_id": ObjectId(task_id)})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        task["_id"] = str(task["_id"])
        return task
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting task: {str(e)}")

@app.put("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def update_task_status(task_id: str, status_update: TaskStatusUpdate):
    try:
        db = get_db()
        tasks_collection = db.tasks
        task = tasks_collection.find_one({"_id": ObjectId(task_id)})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        update_data = {"status": status_update.status, "updated_at": datetime.utcnow()}
        if status_update.result is not None:
            update_data["result"] = status_update.result
        if status_update.error is not None:
            update_data["error"] = status_update.error
        if status_update.attempts is not None:
            update_data["attempts"] = status_update.attempts

        tasks_collection.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
        return {"task_id": task_id, "status": status_update.status, "message": "Task status updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating task status: {str(e)}")

@app.delete("/queues/{queue_name}", response_model=Dict[str, Any])
async def delete_queue(
    queue_name: str,
    current_user: dict = Depends(require_role("expert"))
):
    try:
        db = get_db()
        queues_collection = db.queues
        tasks_collection = db.tasks

        queue = queues_collection.find_one({"name": queue_name})
        if not queue:
            raise HTTPException(status_code=404, detail="Queue not found")

        queue_id = str(queue["_id"])
        tasks_collection.delete_many({"queue_id": queue_id})
        queues_collection.delete_one({"name": queue_name})

        return {"queue_name": queue_name, "message": "Queue and all its tasks deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting queue: {str(e)}")

@app.post("/datasets/{dataset_id}/add-question", response_model=Dict[str, Any])
async def add_question_to_dataset(
    dataset_id: str,
    question: QuestionData,
    current_user: dict = Depends(require_role("expert"))
):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        current_version = dataset.get("current_version", 1)
        version_doc = db.dataset_versions.find_one({"dataset_id": dataset_id, "version": current_version})
        if not version_doc:
            raise HTTPException(status_code=404, detail=f"Version {current_version} not found")

        updated_questions = version_doc["questions"] + [question.dict()]

        db.dataset_versions.update_one(
            {"dataset_id": dataset_id, "version": current_version},
            {"$set": {"questions": updated_questions, "updated_at": datetime.utcnow()}}
        )

        metadata = version_doc.get("metadata", {})
        metadata["total_questions_generated"] = len(updated_questions)
        metadata["last_updated"] = datetime.utcnow().isoformat()
        db.dataset_versions.update_one(
            {"dataset_id": dataset_id, "version": current_version},
            {"$set": {"metadata": metadata}}
        )

        return {
            "dataset_id": dataset_id,
            "question_added": True,
            "total_questions": len(updated_questions),
            "message": "Question added successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding question: {str(e)}")

@app.patch("/datasets/{dataset_id}/metadata", response_model=Dict[str, Any])
async def patch_dataset_metadata(
    dataset_id: str,
    metadata_update: Dict[str, Any],
    current_user: dict = Depends(require_role("expert")),
):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        current_version = dataset.get("current_version", 1)
        version_doc = db.dataset_versions.find_one(
            {"dataset_id": dataset_id, "version": current_version}
        )
        if not version_doc:
            raise HTTPException(status_code=404, detail=f"Version {current_version} not found")

        merged = {**version_doc.get("metadata", {}), **metadata_update}
        db.dataset_versions.update_one(
            {"dataset_id": dataset_id, "version": current_version},
            {"$set": {"metadata": merged}},
        )
        # Keep the top-level datasets.metadata in sync: the dataset list view
        # reads status/progress from db.datasets, so if we only patched the
        # version doc the list would show a stale status (e.g. stuck on
        # "processing" after a dataset actually finished).
        datasets_metadata = {**dataset.get("metadata", {}), **metadata_update}
        db.datasets.update_one(
            {"_id": ObjectId(dataset_id)},
            {"$set": {"metadata": datasets_metadata, "updated_at": datetime.utcnow()}},
        )

        return {
            "dataset_id": dataset_id,
            "version": current_version,
            "message": "Metadata updated successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating metadata: {str(e)}")


@app.post("/queues/{queue_name}/retry-failed", response_model=Dict[str, Any])
async def retry_failed_tasks(
    queue_name: str,
    current_user: dict = Depends(require_role("expert"))
):
    try:
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

        result = tasks_collection.update_many(
            {"queue_id": queue_id, "status": "failed"},
            {"$set": {"status": "pending", "error": None, "updated_at": datetime.utcnow()}}
        )

        return {
            "queue_name": queue_name,
            "message": f"Successfully reset {result.modified_count} failed tasks to pending",
            "tasks_retried": result.modified_count
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrying failed tasks: {str(e)}")

@app.post("/tasks/retry-failed", response_model=Dict[str, Any])
async def retry_all_failed_tasks(
    queue_name: Optional[str] = None,
    current_user: dict = Depends(require_role("expert")),
):
    """Reset failed tasks back to 'pending' so the worker reprocesses them.

    Failed tasks are almost always transient agent/LLM infrastructure errors
    (gateway HTML errors, connection errors, timeouts), not gate rejections —
    gate-rejected chunks complete without producing a question. Optionally scope
    to a single ``queue_name``; otherwise retries every failed task system-wide.
    """
    try:
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
            {"$set": {"status": "pending", "error": None, "updated_at": datetime.utcnow()}},
        )

        reopened = 0
        for did in dataset_ids:
            try:
                d = db.datasets.find_one({"_id": ObjectId(did)}, {"metadata": 1, "current_version": 1})
            except Exception:
                continue
            if not d:
                continue
            reopen_patch = {"status": "processing"}
            db.datasets.update_one(
                {"_id": d["_id"]},
                {"$set": {
                    "metadata": {**(d.get("metadata") or {}), **reopen_patch},
                    "updated_at": datetime.utcnow(),
                }},
            )
            version = d.get("current_version", 1)
            vdoc = db.dataset_versions.find_one({"dataset_id": did, "version": version})
            if vdoc is not None:
                db.dataset_versions.update_one(
                    {"dataset_id": did, "version": version},
                    {"$set": {"metadata": {**(vdoc.get("metadata") or {}), **reopen_patch}}},
                )
            reopened += 1

        return {
            "tasks_retried": result.modified_count,
            "datasets_reopened": reopened,
            "message": f"Reset {result.modified_count} failed tasks to pending",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrying failed tasks: {str(e)}")


@app.post("/datasets/{dataset_id}/chunks", response_model=Dict[str, Any])
async def save_chunks(
    dataset_id: str,
    chunks: List[ChunkCreate],
    current_user: dict = Depends(require_role("expert")),
):
    try:
        db = get_db()
        dataset = db.datasets.find_one({"_id": ObjectId(dataset_id)})
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        db.chunks.delete_many({"dataset_id": dataset_id})

        docs = []
        for c in chunks:
            docs.append({
                "dataset_id": dataset_id,
                "chunk_index": c.chunk_index,
                "chunk_text": c.chunk_text,
                "fragment_data": c.fragment_data,
                "gate_result": c.gate_result,
                "gate_passed": c.gate_passed,
                "question_types_valid": c.question_types_valid,
                "created_at": datetime.utcnow(),
            })
        if docs:
            db.chunks.insert_many(docs)

        db.datasets.update_one(
            {"_id": ObjectId(dataset_id)},
            {"$set": {"updated_at": datetime.utcnow()}},
        )
        db.chunks.create_index([("dataset_id", 1), ("chunk_index", 1)])

        return {
            "dataset_id": dataset_id,
            "chunks_saved": len(docs),
            "message": "Chunks saved successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving chunks: {str(e)}")

@app.get("/datasets/{dataset_id}/chunks", response_model=List[Dict[str, Any]])
async def get_chunks(
    dataset_id: str,
    gate_passed_only: bool = True,
    current_user: dict = Depends(get_current_user),
):
    try:
        db = get_db()
        query = {"dataset_id": dataset_id}
        if gate_passed_only:
            query["gate_passed"] = True
        chunks = list(db.chunks.find(query).sort("chunk_index", 1))
        for c in chunks:
            c["_id"] = str(c["_id"])
        return chunks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting chunks: {str(e)}")

@app.delete("/datasets/{dataset_id}/chunks", response_model=Dict[str, Any])
async def delete_chunks(
    dataset_id: str,
    current_user: dict = Depends(require_role("expert")),
):
    try:
        db = get_db()
        result = db.chunks.delete_many({"dataset_id": dataset_id})
        return {
            "dataset_id": dataset_id,
            "chunks_deleted": result.deleted_count,
            "message": "Chunks deleted successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting chunks: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8789)