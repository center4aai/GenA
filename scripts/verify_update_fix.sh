#!/usr/bin/env bash
# Verify compile, tests, and Docker images (run from repo root: bash scripts/verify_update_fix.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== py_compile =="
.venv/bin/python -m py_compile \
  gena_web/gena/views/bot.py \
  gena_web/gena/views/queue_manager.py \
  gena_web/gena/views/dataset_editor.py \
  task_worker/worker.py \
  task_worker/config.py \
  tests/test_update_fixes.py

echo "== pytest tests/ =="
.venv/bin/python -m pytest tests/ -v --tb=short

echo "== docker build gena_web =="
docker build -t gena_web_test -f gena_web/Dockerfile gena_web

echo "== docker build task_worker =="
docker build -t task_worker_test -f task_worker/Dockerfile task_worker

echo "All steps OK."
