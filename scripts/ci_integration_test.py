#!/usr/bin/env python3
"""
CI Integration Test — full GenA pipeline on a test document.

Runs inside a docker-compose container (task_worker) and talks to
sibling services via their docker-network hostnames.

Usage:  python ci_integration_test.py /tmp/test_doc.docx
Exit 0 on success, 1 on any assertion failure.
"""

import sys
import os

os.environ["PYTHONUNBUFFERED"] = "1"
import json
import time
import requests
from datetime import datetime

CHUNKER_URL = "http://chunker:8517"
AGENT_API_URL = "http://agent_api:8790"
DATASET_API_URL = "http://dataset_api:8789"

GATE_CHUNKS_LIMIT = 5
TASKS_LIMIT = 2
POLL_TIMEOUT_S = 300
POLL_INTERVAL_S = 10

_passed = 0
_failed = 0


def _print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def step(name: str):
    _print(f"\n{'='*60}\n  STEP: {name}\n{'='*60}")


def ok(msg: str):
    global _passed
    _passed += 1
    _print(f"  [PASS] {msg}")


def fail(msg: str):
    global _failed
    _failed += 1
    _print(f"  [FAIL] {msg}", file=sys.stderr)


def cleanup(dataset_id: str | None, queue_name: str | None):
    """Best-effort cleanup of test data."""
    _print("\n--- Cleanup ---")
    if dataset_id:
        try:
            requests.delete(f"{DATASET_API_URL}/datasets/{dataset_id}", timeout=10)
            requests.delete(f"{DATASET_API_URL}/datasets/{dataset_id}/chunks", timeout=10)
            _print(f"  Deleted dataset {dataset_id}")
        except Exception as e:
            _print(f"  Cleanup dataset error: {e}")
    if queue_name:
        try:
            requests.delete(f"{DATASET_API_URL}/queues/{queue_name}", timeout=10)
            _print(f"  Deleted queue {queue_name}")
        except Exception as e:
            _print(f"  Cleanup queue error: {e}")


def main():
    if len(sys.argv) < 2:
        _print("Usage: ci_integration_test.py <path_to_docx>", file=sys.stderr)
        sys.exit(1)

    doc_path = sys.argv[1]
    if not os.path.isfile(doc_path):
        _print(f"File not found: {doc_path}", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    queue_name = f"ci_test_queue_{ts}"
    dataset_name = f"ci_test_dataset_{ts}"
    dataset_id = None

    try:
        # ── 1. Upload to chunker ─────────────────────────────────────
        step("1. Upload document to chunker")
        with open(doc_path, "rb") as f:
            resp = requests.post(
                f"{CHUNKER_URL}/chunk/",
                files={"file": ("test_doc.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                timeout=120,
            )
        if resp.status_code != 200:
            fail(f"Chunker returned HTTP {resp.status_code}: {resp.text[:300]}")
            return
        chunk_data = resp.json()
        num_chunks = chunk_data.get("num_chunks", 0)
        chunks_detailed = chunk_data.get("chunks_detailed", [])
        chunks_text = chunk_data.get("chunks", [])

        if num_chunks >= 5:
            ok(f"Chunker returned {num_chunks} chunks")
        else:
            fail(f"Expected >= 5 chunks, got {num_chunks}")

        has_semantic = any(
            c.get("fragment_data", {}).get("semantic_split") is True
            for c in chunks_detailed
        )
        if has_semantic:
            ok("SemanticChunker produced sub-chunks (semantic_split=True found)")
        else:
            _print("  [INFO] No semantic sub-chunks detected (items may be short enough)")

        # ── 2. Gate validation (first N chunks) ─────────────────────
        step(f"2. Gate validation (first {GATE_CHUNKS_LIMIT} chunks)")
        gate_results = {}
        sample = chunks_detailed[:GATE_CHUNKS_LIMIT] if chunks_detailed else []
        if not sample:
            sample_texts = chunks_text[:GATE_CHUNKS_LIMIT]
            for i, txt in enumerate(sample_texts):
                sample.append({"_idx": i, "_text": txt})

        for i, chunk in enumerate(sample):
            if isinstance(chunk, dict):
                text = (
                    chunk.get("fragment_data", {}).get("combined_text", "")
                    or chunk.get("_text", "")
                    or str(chunk)
                )
            else:
                text = str(chunk)

            try:
                gr = requests.post(
                    f"{AGENT_API_URL}/chunk_gate/",
                    json={"chunk": text, "question_type": "multi"},
                    timeout=120,
                )
                if gr.status_code == 200:
                    result = gr.json().get("result", {})
                    gate_results[i] = result
                    passed = result.get("passed", False)
                    _print(f"  Chunk {i}: passed={passed}")
                else:
                    gate_results[i] = {"passed": False}
                    _print(f"  Chunk {i}: gate HTTP {gr.status_code}")
            except Exception as e:
                gate_results[i] = {"passed": False}
                _print(f"  Chunk {i}: gate error: {e}")

        passed_chunks = []
        for i, gr in gate_results.items():
            c1 = (gr.get("c1_chunk_informative") or [0])
            c1 = c1[0] if isinstance(c1, list) and c1 else 0
            c2 = (gr.get("c2_chunk_reference_clarity") or [0])
            c2 = c2[0] if isinstance(c2, list) and c2 else 0
            if c1 != 0 and c2 != 0:
                passed_chunks.append(i)

        if len(passed_chunks) >= 1:
            ok(f"{len(passed_chunks)}/{len(gate_results)} chunks passed gate")
        else:
            fail("No chunks passed the gate — cannot continue")
            return

        # ── 3. Create queue ──────────────────────────────────────────
        step("3. Create queue")
        resp = requests.post(
            f"{DATASET_API_URL}/queues/",
            json={"name": queue_name, "description": "CI integration test", "priority": 1},
            timeout=30,
        )
        if resp.status_code == 200:
            ok(f"Queue '{queue_name}' created")
        else:
            fail(f"Queue creation failed: HTTP {resp.status_code} {resp.text[:200]}")
            return

        # ── 4. Create dataset ────────────────────────────────────────
        step("4. Create dataset")
        resp = requests.post(
            f"{DATASET_API_URL}/datasets/",
            json={
                "name": dataset_name,
                "description": "CI integration test dataset",
                "source_document": "Family_code_Russian_Federation_1-4.docx",
                "questions": [],
                "metadata": {
                    "queue_name": queue_name,
                    "question_types": ["multi"],
                    "total_chunks": num_chunks,
                    "chunks_passed_gate": len(passed_chunks),
                    "status": "processing",
                    "created_at": datetime.now().isoformat(),
                },
            },
            timeout=30,
        )
        if resp.status_code == 200:
            dataset_id = resp.json().get("dataset_id")
            ok(f"Dataset created: {dataset_id}")
        else:
            fail(f"Dataset creation failed: HTTP {resp.status_code} {resp.text[:200]}")
            return

        # ── 5. Save chunks ───────────────────────────────────────────
        step("5. Save validated chunks")
        chunk_docs = []
        for i in passed_chunks:
            ch = sample[i] if i < len(sample) else None
            if ch is None:
                continue
            text = (
                ch.get("fragment_data", {}).get("combined_text", "")
                or ch.get("_text", "")
                or str(ch)
            )
            chunk_docs.append({
                "chunk_index": i,
                "chunk_text": text,
                "fragment_data": ch.get("fragment_data"),
                "gate_result": gate_results.get(i, {}),
                "gate_passed": True,
                "question_types_valid": ["multi"],
            })
        resp = requests.post(
            f"{DATASET_API_URL}/datasets/{dataset_id}/chunks",
            json=chunk_docs,
            timeout=30,
        )
        if resp.status_code == 200:
            ok(f"Saved {len(chunk_docs)} chunks")
        else:
            fail(f"Chunk save failed: HTTP {resp.status_code}")

        # ── 6. Create tasks (limited) ────────────────────────────────
        step(f"6. Create tasks (limit {TASKS_LIMIT})")
        tasks = []
        for i in passed_chunks[:TASKS_LIMIT]:
            ch = sample[i] if i < len(sample) else None
            if ch is None:
                continue
            text = (
                ch.get("fragment_data", {}).get("combined_text", "")
                or ch.get("_text", "")
                or str(ch)
            )
            tasks.append({
                "chunk_id": i,
                "chunk_text": text,
                "question_type": "multi",
                "source_document": "Family_code_Russian_Federation_1-4.docx",
                "dataset_name": dataset_name,
                "dataset_id": dataset_id,
                "dataset_description": "CI integration test dataset",
                "priority": 1,
                "chunk_pre_validated": True,
            })

        resp = requests.post(
            f"{DATASET_API_URL}/queues/{queue_name}/tasks/",
            json=tasks,
            timeout=30,
        )
        if resp.status_code == 200:
            added = resp.json().get("tasks_added", 0)
            ok(f"{added} tasks added to queue")
        else:
            fail(f"Task creation failed: HTTP {resp.status_code} {resp.text[:200]}")
            return

        # ── 7. Poll for completion ───────────────────────────────────
        step(f"7. Poll for task completion (timeout {POLL_TIMEOUT_S}s)")
        deadline = time.time() + POLL_TIMEOUT_S
        completed = False
        while time.time() < deadline:
            try:
                pending_resp = requests.get(
                    f"{DATASET_API_URL}/queues/{queue_name}/tasks/",
                    params={"status": "pending", "limit": 100},
                    timeout=15,
                )
                processing_resp = requests.get(
                    f"{DATASET_API_URL}/queues/{queue_name}/tasks/",
                    params={"status": "processing", "limit": 100},
                    timeout=15,
                )
                completed_resp = requests.get(
                    f"{DATASET_API_URL}/queues/{queue_name}/tasks/",
                    params={"status": "completed", "limit": 100},
                    timeout=15,
                )
                n_pending = len(pending_resp.json()) if pending_resp.status_code == 200 else -1
                n_processing = len(processing_resp.json()) if processing_resp.status_code == 200 else -1
                n_completed = len(completed_resp.json()) if completed_resp.status_code == 200 else 0
                _print(f"  pending={n_pending} processing={n_processing} completed={n_completed}")
                if n_pending == 0 and n_processing == 0:
                    completed = True
                    break
            except Exception as e:
                _print(f"  Poll error: {e}")
            time.sleep(POLL_INTERVAL_S)

        if completed:
            ok("All tasks processed")
        else:
            fail(f"Tasks not completed within {POLL_TIMEOUT_S}s")

        # ── 8. Verify dataset has questions ──────────────────────────
        step("8. Verify generated questions in dataset")
        resp = requests.get(f"{DATASET_API_URL}/datasets/{dataset_id}", timeout=30)
        if resp.status_code == 200:
            ds = resp.json()
            questions = ds.get("questions", [])
            if len(questions) >= 1:
                ok(f"Dataset contains {len(questions)} question(s)")
                q = questions[0]
                _print(f"  Sample question: {str(q.get('question', ''))[:120]}...")
            else:
                fail("Dataset has 0 questions after processing")
        else:
            fail(f"Dataset fetch failed: HTTP {resp.status_code}")

    finally:
        # ── 9. Cleanup ───────────────────────────────────────────────
        cleanup(dataset_id, queue_name)
        # Summary + exit must live in finally: early `return` in try skips
        # any code after try/finally, so failures would otherwise exit 0.
        _print(f"\n{'='*60}")
        _print(f"  RESULT: {_passed} passed, {_failed} failed")
        _print(f"{'='*60}")
        sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    main()
