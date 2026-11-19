"""Quality revalidation in dataset_api: runs, items, locks, validation_is_current.

Runs on a small in-memory stand-in for the Mongo collections the feature uses
(find/find_one/insert/update/find_one_and_update/count/aggregate), so the run
lifecycle is exercised end to end without a database.
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pymongo
import pytest
from bson import ObjectId
from fastapi import HTTPException

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from dataset_api_loader import load_dataset_api_module  # noqa: E402

DATASET_ID = "507f1f77bcf86cd799439011"
EXPERT = SimpleNamespace(username="tester", role="expert")


# ─── in-memory collections ───────────────────────────────────────────────


def _get(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _matches(doc, flt):
    for key, cond in (flt or {}).items():
        val = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$lt" and not (val is not None and val < arg):
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$type" and not isinstance(val, str):
                    return False
        elif val != cond:
            return False
    return True


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    include = {k for k, v in projection.items() if v and k != "_id"}
    exclude = {k for k, v in projection.items() if not v}
    if include:
        out = {k: copy.deepcopy(doc[k]) for k in include if k in doc}
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        return out
    return {k: copy.deepcopy(v) for k, v in doc.items() if k not in exclude}


class _Result(SimpleNamespace):
    pass


class FakeCollection:
    def __init__(self, unique_active=False):
        self.docs = []
        self.unique_active = unique_active

    def _check_unique(self, doc, ignore=None):
        if not self.unique_active or doc.get("status") != "processing":
            return
        for other in self.docs:
            if other is ignore:
                continue
            if (
                other.get("status") == "processing"
                and other["dataset_id"] == doc["dataset_id"]
                and other["version"] == doc["version"]
            ):
                raise pymongo.errors.DuplicateKeyError("dup")

    def _sorted(self, docs, sort):
        for key, direction in reversed(sort or []):
            docs = sorted(docs, key=lambda d: (_get(d, key) is None, _get(d, key)), reverse=direction < 0)
        return docs

    def find(self, flt=None, projection=None):
        found = [_project(d, projection) for d in self.docs if _matches(d, flt)]

        class _Cursor(list):
            def sort(self_inner, *args):
                key = args[0]
                if isinstance(key, str):
                    return _Cursor(sorted(self_inner, key=lambda d: str(d.get(key)), reverse=args[1] < 0))
                return self_inner

            def limit(self_inner, n):
                return _Cursor(self_inner[:n])

        return _Cursor(found)

    def find_one(self, flt=None, projection=None, sort=None):
        docs = self._sorted([d for d in self.docs if _matches(d, flt)], sort)
        return _project(docs[0], projection) if docs else None

    def insert_one(self, doc):
        doc = copy.deepcopy(doc)
        doc.setdefault("_id", ObjectId())
        self._check_unique(doc)
        self.docs.append(doc)
        return _Result(inserted_id=doc["_id"])

    def insert_many(self, docs, ordered=True):
        ids = [self.insert_one(d).inserted_id for d in docs]
        return _Result(inserted_ids=ids)

    def _apply(self, doc, update):
        new = copy.deepcopy(doc)
        for k, v in (update.get("$set") or {}).items():
            new[k] = v
        for k, v in (update.get("$inc") or {}).items():
            new[k] = (new.get(k) or 0) + v
        for k in (update.get("$unset") or {}):
            new.pop(k, None)
        self._check_unique(new, ignore=doc)
        doc.clear()
        doc.update(new)

    def update_one(self, flt, update):
        for d in self.docs:
            if _matches(d, flt):
                self._apply(d, update)
                return _Result(matched_count=1, modified_count=1)
        return _Result(matched_count=0, modified_count=0)

    def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if _matches(d, flt):
                self._apply(d, update)
                n += 1
        return _Result(matched_count=n, modified_count=n)

    def find_one_and_update(self, flt, update, sort=None, return_document=None):
        docs = self._sorted([d for d in self.docs if _matches(d, flt)], sort)
        if not docs:
            return None
        self._apply(docs[0], update)
        return copy.deepcopy(docs[0])

    def count_documents(self, flt, limit=None):
        return sum(1 for d in self.docs if _matches(d, flt))

    def aggregate(self, pipeline):
        match = pipeline[0]["$match"]
        counts = {}
        for d in self.docs:
            if _matches(d, match):
                counts[d["status"]] = counts.get(d["status"], 0) + 1
        return [{"_id": k, "n": v} for k, v in counts.items()]

    def bulk_write(self, operations, ordered=True):
        matched = 0
        for op in operations:
            res = self.update_one(op._filter, op._doc)
            matched += res.matched_count
        return _Result(matched_count=matched)


class FakeDB:
    def __init__(self):
        self.datasets = FakeCollection()
        self.dataset_versions = FakeCollection()
        self.questions = FakeCollection()
        self.chunks = FakeCollection()
        self.revalidation_runs = FakeCollection(unique_active=True)
        self.revalidation_items = FakeCollection()


def _question(qid, **kw):
    q = {
        "question_id": qid,
        "chunk_id": 1,
        "question_type": "one",
        "task": f"Вопрос {qid}?",
        "options": {"option_1": "a", "option_2": "b"},
        "correct_answer": "1",
        "provocativeness": "1",
        "validation_passed": "True",
        "validation_score": "19/20.5",
        "source_chunk": "Текст чанка.",
    }
    q.update(kw)
    return q


@pytest.fixture
def env(monkeypatch):
    mod = load_dataset_api_module()
    db = FakeDB()
    db.datasets.insert_one({
        "_id": ObjectId(DATASET_ID), "name": "ds", "description": "", "source_document": "doc",
        "current_version": 2, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
        "metadata": {"status": "completed", "document_kind": "textbook", "validation_model_id": "qwen"},
    })
    for v in (1, 2):
        db.dataset_versions.insert_one({
            "dataset_id": DATASET_ID, "version": v, "created_at": datetime.utcnow(),
            "metadata": {"status": "completed"},
        })
    rows = [
        _question("q1"),
        _question("q2", edit_status="user_edited", validation_is_current=False),
        _question("q3", edit_status="paraphrased", needs_revalidation=True, validation_passed=None),
        _question("q4", validation_passed="False"),
    ]
    for r in rows:
        db.questions.insert_one({**r, "dataset_id": DATASET_ID, "version": 2})
    monkeypatch.setattr(mod, "get_db", lambda: db)
    return mod, db


def _state(mod, version=2):
    return mod.get_revalidation_state(DATASET_ID, version, current_user=EXPERT)


def _q(db, qid, version=2):
    return db.questions.find_one({"dataset_id": DATASET_ID, "version": version, "question_id": qid})


def _result(passed="True"):
    return {
        "validation_passed": passed,
        "validation_score": "18.5/20.5",
        "validation_threshold": "18.0",
        "validation_details": "{}",
        "validation_justifications": "{}",
    }


def _drain(mod, db, errors=()):
    """Process every pending item like the worker does; qids in `errors` fail."""
    done = []
    while True:
        claimed = mod.claim_revalidation_item(mod.RevalidationClaim(worker_id="w"))["item"]
        if claimed is None:
            return done
        qid = claimed["question_id"]
        body = (
            mod.RevalidationItemResult(error="llm unavailable")
            if qid in errors
            else mod.RevalidationItemResult(result=_result("False" if qid == "q2" else "True"))
        )
        mod.complete_revalidation_item(claimed["item_id"], body)
        done.append(qid)


# ─── validation_is_current ───────────────────────────────────────────────


def test_validation_is_current_derivation(env):
    mod, _db = env
    assert mod._validation_is_current({"validation_passed": "True"}) is True
    assert mod._validation_is_current({"validation_passed": "True", "needs_revalidation": True}) is False
    assert mod._validation_is_current({"validation_passed": None}) is False
    assert mod._validation_is_current({"validation_passed": "False", "validation_is_current": True}) is True
    assert mod._validation_is_current({"validation_passed": "True", "validation_is_current": False}) is False


def test_get_dataset_annotates_validation_is_current(env):
    mod, _db = env
    ds = mod.get_dataset(DATASET_ID, 2, current_user=EXPERT)
    flags = {q["question_id"]: q["validation_is_current"] for q in ds["questions"]}
    assert flags == {"q1": True, "q2": False, "q3": False, "q4": True}


def test_wording_edit_marks_verdict_stale_but_keeps_it(env):
    mod, db = env
    req = mod.QuestionsPatchRequest(questions=[mod.QuestionPatch(question_id="q1", task="Новая формулировка?")])
    mod.patch_version_questions(DATASET_ID, 2, req, current_user=EXPERT)
    q = _q(db, "q1")
    assert q["validation_is_current"] is False and q["needs_revalidation"] is True
    assert q["validation_passed"] == "True"  # passed survives at the data level
    assert q["edit_status"] == "user_edited"


def test_difficulty_and_export_edits_keep_verdict_current(env):
    mod, db = env
    req = mod.QuestionsPatchRequest(questions=[
        mod.QuestionPatch(question_id="q1", difficulty="3"),
        mod.QuestionPatch(question_id="q4", excluded_from_export=True),
    ])
    mod.patch_version_questions(DATASET_ID, 2, req, current_user=EXPERT)
    assert "validation_is_current" not in _q(db, "q1")
    assert "validation_is_current" not in _q(db, "q4")


# ─── state ───────────────────────────────────────────────────────────────


def test_state_before_any_run(env):
    mod, _db = env
    st = _state(mod)
    assert st["available"] is True
    assert st["primary_status"] == "required"
    assert (st["required_count"], st["total_count"]) == (2, 4)
    assert st["scope_counts"] == {"all": 4, "required": 2, "user_edited": 1, "paraphrased": 1}
    assert st["last_run"] is None and st["active_run"] is None


def test_state_not_started_when_everything_current(env):
    mod, db = env
    for qid in ("q2", "q3"):
        db.questions.update_one({"question_id": qid}, {"$set": {"validation_is_current": True, "needs_revalidation": False, "validation_passed": "True"}})
    assert _state(mod)["primary_status"] == "not_started"


# ─── run lifecycle ───────────────────────────────────────────────────────


def test_run_required_scope_completes_and_updates_questions(env):
    mod, db = env
    st = mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="required"), current_user=EXPERT)
    assert st["primary_status"] == "processing"
    assert st["active_run"]["total"] == 2
    claimed = mod.claim_revalidation_item(mod.RevalidationClaim(worker_id="w"))["item"]
    assert claimed["document_kind"] == "textbook"
    assert claimed["validation_model_id"] == "qwen"
    assert claimed["source_text"] == "Текст чанка."
    assert set(claimed["question"]) == {"question_type", "task", "options", "correct_answer", "chunk_id"}
    mod.complete_revalidation_item(claimed["item_id"], mod.RevalidationItemResult(result=_result("False")))
    _drain(mod, db)
    st = _state(mod)
    assert st["primary_status"] == "completed"
    assert st["last_run"]["status"] == "completed"
    assert (st["last_run"]["succeeded"], st["last_run"]["total"], st["last_run"]["errors"]) == (2, 2, 0)
    for qid in ("q2", "q3"):
        q = _q(db, qid)
        assert q["validation_is_current"] is True and q["needs_revalidation"] is False
        assert q["validation_score"] == "18.5/20.5"
        assert q.get("edit_status") in ("user_edited", "paraphrased")  # revalidation is not an edit
    assert _q(db, "q1")["validation_score"] == "19/20.5"  # outside scope: untouched
    # Dataset status is never touched.
    assert db.datasets.find_one({})["metadata"]["status"] == "completed"


def test_technical_errors_keep_previous_verdict_and_allow_retry(env):
    mod, db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    _drain(mod, db, errors={"q1", "q2"})
    st = _state(mod)
    assert st["last_run"]["status"] == "completed_with_errors"
    assert (st["last_run"]["succeeded"], st["last_run"]["errors"]) == (2, 2)
    assert st["retryable_count"] == 2
    # q2 was stale before the run and stays stale; q1 keeps its current verdict.
    assert st["primary_status"] == "required" and st["required_count"] == 1
    assert _q(db, "q1")["validation_passed"] == "True" and _q(db, "q1").get("validation_is_current") is None
    assert _q(db, "q2")["validation_is_current"] is False

    st = mod.retry_revalidation(DATASET_ID, 2, current_user=EXPERT)
    assert st["primary_status"] == "processing"
    assert (st["active_run"]["succeeded"], st["active_run"]["total"]) == (2, 4)  # continues the run
    assert sorted(_drain(mod, db)) == ["q1", "q2"]
    st = _state(mod)
    assert st["primary_status"] == "completed"
    assert (st["last_run"]["succeeded"], st["last_run"]["total"], st["last_run"]["errors"]) == (4, 4, 0)
    assert st["retryable_count"] == 0


def test_full_failure_is_failed_and_retry_reruns_scope(env):
    mod, db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="user_edited"), current_user=EXPERT)
    _drain(mod, db, errors={"q2"})
    st = _state(mod)
    assert st["last_run"]["status"] == "failed"
    assert st["retryable_count"] == 1
    mod.retry_revalidation(DATASET_ID, 2, current_user=EXPERT)
    assert _drain(mod, db) == ["q2"]
    assert _state(mod)["last_run"]["status"] == "completed"


def test_start_guards(env):
    mod, db = env
    with pytest.raises(HTTPException) as exc:
        mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="nope"), current_user=EXPERT)
    assert exc.value.status_code == 400
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    with pytest.raises(HTTPException) as exc:
        mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    assert exc.value.status_code == 409 and "revalidation_in_progress" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        mod.retry_revalidation(DATASET_ID, 2, current_user=EXPERT)
    assert exc.value.status_code == 409
    # Another version stays free.
    db.questions.insert_one({**_question("v1q"), "dataset_id": DATASET_ID, "version": 1})
    assert mod.start_revalidation(DATASET_ID, 1, mod.RevalidationStart(scope="all"), current_user=EXPERT)["primary_status"] == "processing"


def test_not_available_while_generating(env):
    mod, db = env
    db.dataset_versions.update_one({"version": 2}, {"$set": {"metadata": {"status": "processing"}}})
    db.datasets.update_one({}, {"$set": {"metadata": {"status": "processing"}}})
    assert _state(mod)["available"] is False
    with pytest.raises(HTTPException) as exc:
        mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    assert exc.value.status_code == 409 and "revalidation_not_available" in exc.value.detail


def test_empty_scope_is_rejected(env):
    mod, db = env
    db.questions.update_many({}, {"$set": {"edit_status": None}})
    with pytest.raises(HTTPException) as exc:
        mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="paraphrased"), current_user=EXPERT)
    assert exc.value.status_code == 400


# ─── locks ───────────────────────────────────────────────────────────────


def test_editing_is_locked_during_run_but_export_toggle_is_not(env):
    mod, db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    edit = mod.QuestionsPatchRequest(questions=[mod.QuestionPatch(question_id="q1", task="x?")])
    with pytest.raises(HTTPException) as exc:
        mod.patch_version_questions(DATASET_ID, 2, edit, current_user=EXPERT)
    assert exc.value.status_code == 409 and "revalidation_in_progress" in exc.value.detail
    toggle = mod.QuestionsPatchRequest(questions=[mod.QuestionPatch(question_id="q1", excluded_from_export=True)])
    mod.patch_version_questions(DATASET_ID, 2, toggle, current_user=EXPERT)
    assert _q(db, "q1")["excluded_from_export"] is True


def test_transform_is_locked_while_latest_version_revalidates(env):
    mod, _db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    upd = mod.DatasetUpdate(questions=[], base_version=2)
    with pytest.raises(HTTPException) as exc:
        mod.update_dataset(DATASET_ID, upd, current_user=EXPERT)
    assert exc.value.status_code == 409 and "revalidation_in_progress" in exc.value.detail


# ─── worker edge cases ───────────────────────────────────────────────────


def test_missing_question_becomes_item_error(env):
    mod, db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="user_edited"), current_user=EXPERT)
    db.questions.docs = [d for d in db.questions.docs if d["question_id"] != "q2"]
    assert mod.claim_revalidation_item(mod.RevalidationClaim())["item"] is None
    st = _state(mod)
    assert st["last_run"]["status"] == "failed"


def test_stuck_items_are_requeued_then_fail_after_max_attempts(env, monkeypatch):
    mod, db = env
    monkeypatch.setattr(mod, "REVALIDATION_MAX_ATTEMPTS", 2)
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="user_edited"), current_user=EXPERT)
    for _ in range(2):
        item = mod.claim_revalidation_item(mod.RevalidationClaim())["item"]
        assert item is not None
        db.revalidation_items.update_one({"status": "processing"}, {"$set": {"updated_at": datetime.utcnow() - timedelta(hours=1)}})
    assert mod.claim_revalidation_item(mod.RevalidationClaim())["item"] is None
    st = _state(mod)
    assert st["last_run"]["status"] == "failed"
    assert db.revalidation_items.find_one({})["error"] == "interrupted"


def test_completing_twice_is_rejected(env):
    mod, _db = env
    mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="user_edited"), current_user=EXPERT)
    item = mod.claim_revalidation_item(mod.RevalidationClaim())["item"]
    mod.complete_revalidation_item(item["item_id"], mod.RevalidationItemResult(result=_result()))
    with pytest.raises(HTTPException) as exc:
        mod.complete_revalidation_item(item["item_id"], mod.RevalidationItemResult(result=_result()))
    assert exc.value.status_code == 409


# ─── validator model of a run, verdict snapshots, comparison export ──────


def test_chosen_model_goes_to_run_claim_and_question(env):
    mod, db = env
    st = mod.start_revalidation(
        DATASET_ID, 2, mod.RevalidationStart(scope="all", validation_model_id="gemma"), current_user=EXPERT
    )
    assert st["active_run"]["validation_model_id"] == "gemma"
    claimed = mod.claim_revalidation_item(mod.RevalidationClaim(worker_id="w"))["item"]
    assert claimed["validation_model_id"] == "gemma"
    mod.complete_revalidation_item(claimed["item_id"], mod.RevalidationItemResult(result=_result()))
    _drain(mod, db)
    q = _q(db, claimed["question_id"])
    assert q["validation_model_id"] == "gemma"
    assert q["revalidation_run_id"] == st["active_run"]["run_id"]
    assert _state(mod)["last_run"]["validation_model_id"] == "gemma"


def test_without_model_the_version_validator_is_used(env):
    mod, db = env
    st = mod.start_revalidation(DATASET_ID, 2, mod.RevalidationStart(scope="all"), current_user=EXPERT)
    assert st["active_run"]["validation_model_id"] == "qwen"


def test_retry_keeps_the_model_of_its_run(env):
    mod, db = env
    mod.start_revalidation(
        DATASET_ID, 2, mod.RevalidationStart(scope="user_edited", validation_model_id="gemma"), current_user=EXPERT
    )
    _drain(mod, db, errors={"q2"})
    mod.retry_revalidation(DATASET_ID, 2, current_user=EXPERT)
    claimed = mod.claim_revalidation_item(mod.RevalidationClaim(worker_id="w"))["item"]
    assert claimed["validation_model_id"] == "gemma"


def test_run_items_export_has_previous_and_new_verdicts(env):
    mod, db = env
    db.questions.update_one(
        {"question_id": "q1"}, {"$set": {"validation_model_id": "old-qwen", "validation_justifications": "{\"c1\": \"ok\"}"}}
    )
    st = mod.start_revalidation(
        DATASET_ID, 2, mod.RevalidationStart(scope="all", validation_model_id="gemma"), current_user=EXPERT
    )
    run_id = st["active_run"]["run_id"]
    _drain(mod, db, errors={"q4"})
    out = mod.get_revalidation_run_items(DATASET_ID, 2, run_id, current_user=EXPERT)
    assert out["run"]["validation_model_id"] == "gemma"
    rows = {r["question_id"]: r for r in out["items"]}
    assert set(rows) == {"q1", "q2", "q3", "q4"}
    q1 = rows["q1"]
    assert q1["task"] == "Вопрос q1?" and q1["question_type"] == "one" and q1["status"] == "done"
    assert q1["previous"] == {
        "passed": "True", "score": "19/20.5", "threshold": None,
        "justifications": "{\"c1\": \"ok\"}", "model_id": "old-qwen",
    }
    assert q1["result"]["passed"] == "True" and q1["result"]["model_id"] == "gemma"
    # No stored model on the question → the version's validator.
    assert rows["q2"]["previous"]["model_id"] == "qwen"
    assert rows["q2"]["result"]["passed"] == "False"
    # Technical error: no new verdict, the previous one is still listed.
    assert rows["q4"]["status"] == "error" and rows["q4"]["result"] is None
    assert rows["q4"]["previous"]["passed"] == "False"
    # The snapshot survives the question being overwritten.
    assert _q(db, "q1")["validation_model_id"] == "gemma"


def test_run_items_unknown_run_is_404(env):
    mod, db = env
    with pytest.raises(HTTPException) as exc_info:
        mod.get_revalidation_run_items(DATASET_ID, 2, "nope", current_user=EXPERT)
    assert exc_info.value.status_code == 404
