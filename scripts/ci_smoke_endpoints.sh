#!/usr/bin/env bash
# Smoke-тесты HTTP внутри контейнеров после deploy (self-hosted runner + docker compose).
# Вызывается из GitLab CI; локально: PROJECT_DIR=/path/to/gena ./scripts/ci_smoke_endpoints.sh
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${PROJECT_DIR:-$SELF_DIR}"
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

CHUNKER_SVC="chunker"

on_err() {
  echo "=== docker compose ps ===" >&2
  "${COMPOSE[@]}" ps -a >&2 || true
}
trap on_err ERR

# После up -d процессы могут ещё не слушать порты — ждём готовность (до ~3 мин).
wait_for_stack() {
  local max_attempts=60
  local attempt=1
  while [ "$attempt" -le "$max_attempts" ]; do
    CHUNKER_SVC="$(chunker_service)"
    if "${COMPOSE[@]}" exec -T agent_api python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8790/health/', timeout=5)
" 2>/dev/null \
      && "${COMPOSE[@]}" exec -T "$CHUNKER_SVC" python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8517/health', timeout=5)
" 2>/dev/null \
      && "${COMPOSE[@]}" exec -T dataset_api python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8789/health', timeout=5)
" 2>/dev/null \
      && "${COMPOSE[@]}" exec -T task_worker python -c "
import os
os.kill(1, 0)
" 2>/dev/null; then
      echo "Stack is responding (attempt $attempt/$max_attempts)."
      return 0
    fi
    echo "Waiting for stack... attempt $attempt/$max_attempts ($(date -u +%H:%M:%S) UTC)"
    sleep 3
    attempt=$((attempt + 1))
  done
  echo "ERROR: timeout waiting for services to become ready." >&2
  return 1
}

wait_for_stack

CHUNKER_SVC="$(chunker_service)"
check_json_health() {
  local service=$1 port=$2 path=$3
  echo "==> GET $service http://127.0.0.1:${port}${path}"
  "${COMPOSE[@]}" exec -T "$service" python -c "
import json, sys, urllib.request
url = 'http://127.0.0.1:${port}${path}'
try:
    r = urllib.request.urlopen(url, timeout=60)
except Exception as e:
    print('FAIL', url, e, file=sys.stderr)
    sys.exit(1)
if r.status != 200:
    print('FAIL status', r.status, file=sys.stderr)
    sys.exit(1)
body = r.read().decode()
d = json.loads(body)
if d.get('status') != 'healthy':
    print('FAIL unexpected body', body[:300], file=sys.stderr)
    sys.exit(1)
print('OK', url)
"
}

check_json_health agent_api 8790 /health/
check_json_health "$CHUNKER_SVC" 8517 /health

check_dataset_health() {
  echo "==> GET dataset_api http://127.0.0.1:8789/health"
  "${COMPOSE[@]}" exec -T dataset_api python -c "
import json, sys, urllib.request
url = 'http://127.0.0.1:8789/health'
try:
    r = urllib.request.urlopen(url, timeout=60)
except Exception as e:
    print('FAIL', url, e, file=sys.stderr)
    sys.exit(1)
if r.status != 200:
    print('FAIL status', r.status, file=sys.stderr)
    sys.exit(1)
body = r.read().decode()
d = json.loads(body)
if d.get('status') not in ('ok', 'healthy'):
    print('FAIL unexpected body', body[:300], file=sys.stderr)
    sys.exit(1)
print('OK', url)
"
}

check_worker_alive() {
  echo "==> task_worker process alive"
  "${COMPOSE[@]}" exec -T task_worker python -c "
import os, sys
try:
    os.kill(1, 0)
except OSError as e:
    print('FAIL task_worker', e, file=sys.stderr)
    sys.exit(1)
print('OK task_worker')
"
}

check_dataset_health
echo "==> GET dataset_api /ready (mongo ping)"
"${COMPOSE[@]}" exec -T dataset_api python -c "
import json, sys, urllib.request
url = 'http://127.0.0.1:8789/ready'
try:
    r = urllib.request.urlopen(url, timeout=60)
except Exception as e:
    print('FAIL', url, e, file=sys.stderr)
    sys.exit(1)
if r.status != 200:
    print('FAIL status', r.status, file=sys.stderr)
    sys.exit(1)
print('OK', url, r.read().decode()[:200])
"
check_worker_alive

trap - ERR
echo "All smoke checks passed."
