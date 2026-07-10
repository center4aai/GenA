#!/usr/bin/env bash
# Smoke-тесты HTTP внутри контейнеров после deploy (self-hosted runner + docker compose).
# Вызывается из GitLab CI; локально: PROJECT_DIR=/path/to/gena ./scripts/ci_smoke_endpoints.sh
set -euo pipefail

ROOT="${PROJECT_DIR:-/app/gena_dev}"
cd "$ROOT"

on_err() {
  echo "=== docker compose ps ===" >&2
  docker compose ps -a >&2 || true
}
trap on_err ERR

# После up -d процессы могут ещё не слушать порты — ждём готовность (до ~3 мин).
wait_for_stack() {
  local max_attempts=60
  local attempt=1
  while [ "$attempt" -le "$max_attempts" ]; do
    if docker compose exec -T agent_api python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8790/health/', timeout=5)
" 2>/dev/null \
      && docker compose exec -T chunker python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8517/health', timeout=5)
" 2>/dev/null \
      && docker compose exec -T dataset_api python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8789/openapi.json', timeout=5)
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

check_json_health() {
  local service=$1 port=$2 path=$3
  echo "==> GET $service http://127.0.0.1:${port}${path}"
  docker compose exec -T "$service" python -c "
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

check_openapi() {
  local service=$1 port=$2
  echo "==> GET $service http://127.0.0.1:${port}/openapi.json"
  docker compose exec -T "$service" python -c "
import sys, urllib.request
url = 'http://127.0.0.1:${port}/openapi.json'
try:
    r = urllib.request.urlopen(url, timeout=60)
except Exception as e:
    print('FAIL', url, e, file=sys.stderr)
    sys.exit(1)
if r.status != 200:
    print('FAIL status', r.status, file=sys.stderr)
    sys.exit(1)
data = r.read()
if b'openapi' not in data[:800].lower():
    print('FAIL not openapi response', data[:200], file=sys.stderr)
    sys.exit(1)
print('OK', url)
"
}

check_json_health agent_api 8790 /health/
check_json_health chunker 8517 /health
check_openapi dataset_api 8789

trap - ERR
echo "All smoke checks passed."
