#!/usr/bin/env bash
# CI Chunker Test — uploads a test document and validates chunk quality.
# Runs between smoke and integration tests.
# Usage:  PROJECT_DIR=/app/gena_dev bash scripts/ci_test_chunker.sh
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# ROOT — где лежит docker-compose.yaml (контекст compose-проекта).
ROOT="${PROJECT_DIR:-$SELF_DIR}"
# SRC  — откуда брать тестируемые файлы (исходники тестируемого коммита).
#         В GitLab CI это $CI_PROJECT_DIR, локально — $SELF_DIR.
SRC="${CI_PROJECT_DIR:-$SELF_DIR}"
cd "$ROOT"

TEST_FILE="$SRC/gena_web/docs/Family_code_Russian_Federation_1-4.docx"
TEST_PY="$SRC/scripts/ci_test_chunker.py"

if [ ! -f "$TEST_FILE" ]; then
  echo "ERROR: test file not found: $TEST_FILE" >&2
  exit 1
fi
if [ ! -f "$TEST_PY" ]; then
  echo "ERROR: test script not found: $TEST_PY" >&2
  exit 1
fi

echo "Copying test file and script into chunker container..."
docker compose cp "$TEST_FILE" chunker:/tmp/test_doc.docx
docker compose cp "$TEST_PY" chunker:/tmp/ci_test_chunker.py

echo "Running chunker test..."
docker compose exec -T -e PYTHONUNBUFFERED=1 chunker python -u /tmp/ci_test_chunker.py /tmp/test_doc.docx
EXIT_CODE=$?

docker compose exec -T chunker rm -f /tmp/test_doc.docx /tmp/ci_test_chunker.py 2>/dev/null || true

exit $EXIT_CODE
