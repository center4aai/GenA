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

COMPOSE=(docker compose)
if [ -n "${COMPOSE_PROJECT:-}" ]; then
  COMPOSE+=( -p "$COMPOSE_PROJECT" )
fi

chunker_service() {
  local svc
  for svc in chunker chunker_gpu; do
    if "${COMPOSE[@]}" ps --status running -q "$svc" 2>/dev/null | grep -q .; then
      echo "$svc"
      return 0
    fi
  done
  echo "chunker"
}

TEST_FILE="$SRC/tests/data/Family_code_Russian_Federation_1-4.docx"
# PDF-учебник (3 страницы) для проверки метода layout: врезки отдельно,
# колонтитулы и задания вырезаны. Без LLM, занимает секунды.
TEXTBOOK_PDF="$SRC/tests/data/medinskiy_layout_sample.pdf"
TEST_PY="$SRC/scripts/ci_test_chunker.py"

if [ ! -f "$TEST_FILE" ]; then
  echo "ERROR: test file not found: $TEST_FILE" >&2
  exit 1
fi
if [ ! -f "$TEST_PY" ]; then
  echo "ERROR: test script not found: $TEST_PY" >&2
  exit 1
fi
if [ ! -f "$TEXTBOOK_PDF" ]; then
  echo "ERROR: textbook sample not found: $TEXTBOOK_PDF" >&2
  exit 1
fi

CHUNKER_SVC="$(chunker_service)"

echo "Copying test file and script into chunker container ($CHUNKER_SVC)..."
"${COMPOSE[@]}" cp "$TEST_FILE" "$CHUNKER_SVC:/tmp/test_doc.docx"
"${COMPOSE[@]}" cp "$TEXTBOOK_PDF" "$CHUNKER_SVC:/tmp/test_textbook.pdf"
"${COMPOSE[@]}" cp "$TEST_PY" "$CHUNKER_SVC:/tmp/ci_test_chunker.py"

echo "Running chunker test..."
# `set -e` would abort before the cleanup below, so capture the status explicitly.
EXIT_CODE=0
"${COMPOSE[@]}" exec -T -e PYTHONUNBUFFERED=1 "$CHUNKER_SVC" \
  python -u /tmp/ci_test_chunker.py /tmp/test_doc.docx --textbook-pdf /tmp/test_textbook.pdf || EXIT_CODE=$?

"${COMPOSE[@]}" exec -T "$CHUNKER_SVC" rm -f /tmp/test_doc.docx /tmp/test_textbook.pdf /tmp/ci_test_chunker.py 2>/dev/null || true

exit $EXIT_CODE
