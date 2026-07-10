#!/usr/bin/env bash
# CI Integration Test — copies test document into task_worker container
# and runs the full pipeline test.
# Usage:  PROJECT_DIR=/app/gena_dev bash scripts/ci_integration_test.sh
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${PROJECT_DIR:-$SELF_DIR}"
SRC="${CI_PROJECT_DIR:-$SELF_DIR}"
cd "$ROOT"

TEST_FILE="$SRC/gena_web/docs/Family_code_Russian_Federation_1-4.docx"
TEST_PY="$SRC/scripts/ci_integration_test.py"

if [ ! -f "$TEST_FILE" ]; then
  echo "ERROR: test file not found: $TEST_FILE" >&2
  exit 1
fi
if [ ! -f "$TEST_PY" ]; then
  echo "ERROR: test script not found: $TEST_PY" >&2
  exit 1
fi

echo "Copying test file and script into task_worker container..."
docker compose cp "$TEST_FILE" task_worker:/tmp/test_doc.docx
docker compose cp "$TEST_PY" task_worker:/tmp/ci_integration_test.py

echo "Running integration test..."
docker compose exec -T -e PYTHONUNBUFFERED=1 task_worker python -u /tmp/ci_integration_test.py /tmp/test_doc.docx
EXIT_CODE=$?

docker compose exec -T task_worker rm -f /tmp/test_doc.docx /tmp/ci_integration_test.py 2>/dev/null || true

exit $EXIT_CODE
