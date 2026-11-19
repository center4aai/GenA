#!/bin/bash
set -e

ENV=$1  # prod или dev

if [ "$ENV" = "prod" ]; then
  POSTFIX="_MAIN"
  ENV_SUFFIX="main"
  # 27369 is occupied by an unrelated container on the shared runner host, so
  # the demo frontend defaults to a free port. Override with WEB_PORT_MAIN.
  WEB_PORT_DEFAULT="27373"
elif [ "$ENV" = "dev" ]; then
  POSTFIX="_DEV"
  ENV_SUFFIX="dev"
  WEB_PORT_DEFAULT="27371"
else
  echo "Usage: $0 {prod|dev}"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_DEFAULTS_FILE="$REPO_ROOT/config/llm_defaults.env"
# Ключи, заданные в этом файле, — источник правды для LLM-конфигурации:
# они перекрывают и общие, и *_DEV/*_MAIN переменные GitLab (см. LLM_FILE_KEYS ниже).
LLM_FILE_KEYS=""
if [ -f "$LLM_DEFAULTS_FILE" ]; then
  LLM_FILE_KEYS="$(grep -oE '^[A-Z_]+=' "$LLM_DEFAULTS_FILE" | tr -d '=' | tr '\n' ' ')"
  set -a
  # shellcheck disable=SC1091
  . "$LLM_DEFAULTS_FILE"
  set +a
fi

llm_file_has_key() {
  case " $LLM_FILE_KEYS " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Функция для подстановки: сначала пробуем с постфиксом, если нет — берем общую
get_var() {
  local var_name=$1
  local with_postfix="${var_name}${POSTFIX}"

  # Ключи из config/llm_defaults.env выигрывают у GitLab Variables
  # (включая *_DEV/*_MAIN), чтобы репозиторий был источником правды для LLM.
  if llm_file_has_key "$var_name"; then
    echo "${!var_name}"
    return
  fi

  # Если есть переменная с постфиксом — используем её
  if [ -n "${!with_postfix}" ]; then
    echo "${!with_postfix}"
  else
    # Иначе берём общую (без постфикса)
    echo "${!var_name}"
  fi
}

# Для isolation-sensitive переменных (порты, имена контейнеров, имя сети):
# берём ТОЛЬКО env-specific (*_DEV/_MAIN); общую (без постфикса) игнорируем,
# иначе dev и main будут получать одинаковые значения и драться за порты/имена.
# Если *_DEV/_MAIN не задана — используем автоматический env-specific дефолт.
get_isolated_var() {
  local var_name=$1
  local fallback=$2
  local with_postfix="${var_name}${POSTFIX}"
  if [ -n "${!with_postfix}" ]; then
    echo "${!with_postfix}"
  else
    echo "$fallback"
  fi
}

# get_var с дефолтом: если ни *_DEV/_MAIN, ни общая переменная не заданы — берём default.
# Дефолты ниже должны совпадать с дефолтами в config.py соответствующего сервиса
# (контракт проверяет tests/test_env_contract.py).
get_var_or() {
  local val
  val=$(get_var "$1")
  if [ -n "$val" ]; then
    echo "$val"
  else
    echo "$2"
  fi
}

# Значение в одинарных кавычках для .env: секреты и JSON могут содержать
# символы, которые ломают `. ./.env` в CI (например `)` или пробел вне кавычек).
# bash, docker compose (env_file) и python-dotenv одинаково снимают одинарные
# кавычки и не интерполируют `$` внутри них. Сама одинарная кавычка в значении
# не поддерживается: bash и compose экранируют её по-разному — лучше упасть, чем
# записать битое значение.
# sq вызывается внутри heredoc через $(...), поэтому `exit` завершил бы только
# подоболочку: вместо этого пишем маркер, а после генерации .env проверяем его.
SQ_INVALID_MARKER="__GEN_ENV_INVALID_VALUE__"
sq() {
  case $1 in
    *\'*)
      echo "gen_env.sh: значение содержит одинарную кавычку — не поддерживается в .env" >&2
      printf '%s' "$SQ_INVALID_MARKER"
      return 1
      ;;
  esac
  printf "'%s'" "$1"
}

# Defaults for internal service URLs
AGENT_API_URL_VAL=$(get_var AGENT_API_URL)
if [ -z "$AGENT_API_URL_VAL" ]; then
  AGENT_API_URL_VAL="http://agent_api:8790"
fi
API_DATASET_URL_VAL=$(get_var API_DATASET_URL)
if [ -z "$API_DATASET_URL_VAL" ]; then
  API_DATASET_URL_VAL="http://dataset_api:8789"
fi
WORKER_AGENT_TIMEOUT_VAL=$(get_var WORKER_AGENT_TIMEOUT)
if [ -z "$WORKER_AGENT_TIMEOUT_VAL" ]; then
  WORKER_AGENT_TIMEOUT_VAL="600"
fi
WORKER_AGENT_MAX_RETRIES_VAL=$(get_var WORKER_AGENT_MAX_RETRIES)
if [ -z "$WORKER_AGENT_MAX_RETRIES_VAL" ]; then
  WORKER_AGENT_MAX_RETRIES_VAL="2"
fi
CHUNKER_MAX_UPLOAD_BYTES_VAL=$(get_var CHUNKER_MAX_UPLOAD_BYTES)
if [ -z "$CHUNKER_MAX_UPLOAD_BYTES_VAL" ]; then
  CHUNKER_MAX_UPLOAD_BYTES_VAL="52428800"
fi
VITE_DATASET_API_URL_VAL=$(get_var VITE_DATASET_API_URL)
if [ -z "$VITE_DATASET_API_URL_VAL" ]; then
  VITE_DATASET_API_URL_VAL="/api/dataset"
fi
VITE_AGENT_API_URL_VAL=$(get_var VITE_AGENT_API_URL)
if [ -z "$VITE_AGENT_API_URL_VAL" ]; then
  VITE_AGENT_API_URL_VAL="/api/agent"
fi
VITE_CHUNKER_URL_VAL=$(get_var VITE_CHUNKER_URL)
if [ -z "$VITE_CHUNKER_URL_VAL" ]; then
  VITE_CHUNKER_URL_VAL="/api/chunker"
fi
AUTH_DISABLED_VAL=$(get_var AUTH_DISABLED)
if [ -z "$AUTH_DISABLED_VAL" ]; then
  AUTH_DISABLED_VAL="true"
fi
VITE_AUTH_MODE_VAL=$(get_var VITE_AUTH_MODE)
if [ -z "$VITE_AUTH_MODE_VAL" ]; then
  case "${AUTH_DISABLED_VAL,,}" in
    0|false|no) VITE_AUTH_MODE_VAL="enabled" ;;
    *) VITE_AUTH_MODE_VAL="disabled" ;;
  esac
fi
# Default UI locale: develop → Russian, main/demo → English (overridable via Variables)
VITE_DEFAULT_LOCALE_VAL=$(get_var VITE_DEFAULT_LOCALE)
if [ -z "$VITE_DEFAULT_LOCALE_VAL" ]; then
  if [ "$ENV" = "dev" ]; then
    VITE_DEFAULT_LOCALE_VAL="ru"
  else
    VITE_DEFAULT_LOCALE_VAL="en"
  fi
fi
MILVUS_ENABLED_VAL=$(get_var MILVUS_ENABLED)
if [ -z "$MILVUS_ENABLED_VAL" ]; then
  MILVUS_ENABLED_VAL="false"
fi
GIGACHAT_VERIFY_SSL_VAL=$(get_var GIGACHAT_VERIFY_SSL)
if [ -z "$GIGACHAT_VERIFY_SSL_VAL" ]; then
  GIGACHAT_VERIFY_SSL_VAL="true"
fi
# Учётка task_worker в dataset_api (login → JWT): по умолчанию = expert-учётка
EXPERT_USERNAME_VAL=$(get_var EXPERT_USERNAME)
EXPERT_PASSWORD_VAL=$(get_var EXPERT_PASSWORD)
DATASET_API_USER_VAL=$(get_var DATASET_API_USER)
if [ -z "$DATASET_API_USER_VAL" ]; then
  DATASET_API_USER_VAL="$EXPERT_USERNAME_VAL"
fi
DATASET_API_PASS_VAL=$(get_var DATASET_API_PASS)
if [ -z "$DATASET_API_PASS_VAL" ]; then
  DATASET_API_PASS_VAL="$EXPERT_PASSWORD_VAL"
fi
# ArgoCD платформы моделей; пусто — discovery через ArgoCD выключен (как в agent_api/config.py).
# Адрес нашего развёртывания задан в config/llm_defaults.env.
ARGOCD_URL_VAL=$(get_var ARGOCD_URL)

# Генерируем .env
cat > .env << EOF
# MongoDB
MONGO_USERNAME=$(sq "$(get_var MONGO_USERNAME)")
MONGO_PASSWORD=$(sq "$(get_var MONGO_PASSWORD)")
MONGO_HOST=$(sq "$(get_var MONGO_HOST)")
MONGO_PORT=$(get_var MONGO_PORT)
MONGO_DB_NAME=$(sq "$(get_isolated_var MONGO_DB_NAME "gena_${ENV_SUFFIX}")")

# Milvus
MILVUS_HOST=$(sq "$(get_var MILVUS_HOST)")
MILVUS_PORT=$(get_var MILVUS_PORT)

# API
LLM_MODEL_NAME=$(sq "$(get_var LLM_MODEL_NAME)")
LLM_URL_MODEL=$(sq "$(get_var LLM_URL_MODEL)")
LLM_API_KEY=$(sq "$(get_var LLM_API_KEY)")
# Приватные сети, разрешённые SSRF-фильтром agent_api; пусто — allowlist выключен.
OUTBOUND_ALLOWED_CIDRS=$(sq "$(get_var OUTBOUND_ALLOWED_CIDRS)")
AGENT_API_URL=$(sq "${AGENT_API_URL_VAL}")

# Embedding
TEI_MODEL_NAME=$(sq "$(get_var TEI_MODEL_NAME)")
TEI_URL_EMBEDDER=$(sq "$(get_var TEI_URL_EMBEDDER)")
TEI_API_KEY=$(sq "$(get_var TEI_API_KEY)")
API_DATASET_URL=$(sq "${API_DATASET_URL_VAL}")
DATASET_API_URL=$(sq "${API_DATASET_URL_VAL}")
TASK_QUEUE_API_URL=$(sq "${API_DATASET_URL_VAL}")

# LLM
MAX_LEN_USER_PROMPT=$(get_var MAX_LEN_USER_PROMPT)
# Таймаут gate-вызова LLM, сек. (agent_api/config.py; покрывает холодную загрузку модели)
GATE_LLM_TIMEOUT=$(get_var_or GATE_LLM_TIMEOUT 180)
# Таймаут/SDK-ретраи остальных LLM-цепочек (agent_api/config.py)
LLM_TIMEOUT=$(get_var_or LLM_TIMEOUT 90)
LLM_MAX_RETRIES=$(get_var_or LLM_MAX_RETRIES 1)
# Пробный запрос к моделям для /models/health/, сек.; 0 — выключен (agent_api/config.py)
MODEL_SMOKE_INTERVAL_SEC=$(get_var_or MODEL_SMOKE_INTERVAL_SEC 900)

# GigaChat (Sber) — scope/model из config/llm_defaults.env (или override из Variables)
GIGACHAT_CREDENTIALS=$(sq "$(get_var GIGACHAT_CREDENTIALS)")
GIGACHAT_SCOPE=$(sq "$(get_var GIGACHAT_SCOPE)")
GIGACHAT_MODEL=$(sq "$(get_var GIGACHAT_MODEL)")

# YandexGPT — model из config/llm_defaults.env; ключ и folder из Variables
YANDEX_CLOUD_API_KEY=$(sq "$(get_var YANDEX_CLOUD_API_KEY)")
YANDEX_CLOUD_FOLDER=$(sq "$(get_var YANDEX_CLOUD_FOLDER)")
YANDEX_CLOUD_MODEL=$(sq "$(get_var YANDEX_CLOUD_MODEL)")

# Provider для chunker
LLM_PROVIDER=$(sq "$(get_var LLM_PROVIDER)")

# Auth
AUTH_DISABLED=${AUTH_DISABLED_VAL}
EXPERT_USERNAME=$(sq "${EXPERT_USERNAME_VAL}")
EXPERT_PASSWORD=$(sq "${EXPERT_PASSWORD_VAL}")
USER_USERNAME=$(sq "$(get_var USER_USERNAME)")
USER_PASSWORD=$(sq "$(get_var USER_PASSWORD)")
WORKER_API_KEY=$(sq "$(get_var WORKER_API_KEY)")
JWT_SECRET=$(sq "$(get_var JWT_SECRET)")
# Срок жизни JWT, мин. (dataset_api/config.py)
ACCESS_TOKEN_EXPIRE_MINUTES=$(get_var_or ACCESS_TOKEN_EXPIRE_MINUTES 120)
GIGACHAT_VERIFY_SSL=${GIGACHAT_VERIFY_SSL_VAL}
MILVUS_ENABLED=${MILVUS_ENABLED_VAL}
VITE_AUTH_MODE=$(sq "${VITE_AUTH_MODE_VAL}")
VITE_DEFAULT_LOCALE=$(sq "${VITE_DEFAULT_LOCALE_VAL}")
VITE_DATASET_API_URL=$(sq "${VITE_DATASET_API_URL_VAL}")
VITE_AGENT_API_URL=$(sq "${VITE_AGENT_API_URL_VAL}")
VITE_CHUNKER_URL=$(sq "${VITE_CHUNKER_URL_VAL}")

# Dataset API
# CORS_ORIGINS: пусто = CORS-middleware не подключается (SPA ходит same-origin через nginx)
CORS_ORIGINS=$(sq "$(get_var CORS_ORIGINS)")
RAW_DOCUMENT_MAX_BYTES=$(get_var_or RAW_DOCUMENT_MAX_BYTES 52428800)

# Task worker — дефолты как в task_worker/config.py
DATASET_API_USER=$(sq "${DATASET_API_USER_VAL}")
DATASET_API_PASS=$(sq "${DATASET_API_PASS_VAL}")
WORKER_TOKEN_TTL=$(get_var_or WORKER_TOKEN_TTL 3600)
CHUNKER_URL=$(sq "$(get_var_or CHUNKER_URL "http://chunker:8517")")
WORKER_CHUNK_TIMEOUT=$(get_var_or WORKER_CHUNK_TIMEOUT 1800)
WORKER_CHUNK_TIMEOUT_LLM=$(get_var_or WORKER_CHUNK_TIMEOUT_LLM 7200)
WORKER_AGENT_TIMEOUT=${WORKER_AGENT_TIMEOUT_VAL}
WORKER_AGENT_MAX_RETRIES=${WORKER_AGENT_MAX_RETRIES_VAL}
WORKER_POLL_INTERVAL=$(get_var_or WORKER_POLL_INTERVAL 5)
WORKER_BATCH_SIZE=$(get_var_or WORKER_BATCH_SIZE 5)
WORKER_MAX_RETRIES=$(get_var_or WORKER_MAX_RETRIES 3)
WORKER_STUCK_THRESHOLD_MINUTES=$(get_var_or WORKER_STUCK_THRESHOLD_MINUTES 15)
WORKER_RECOVERY_INTERVAL_SECONDS=$(get_var_or WORKER_RECOVERY_INTERVAL_SECONDS 300)
WORKER_HEARTBEAT_SECONDS=$(get_var_or WORKER_HEARTBEAT_SECONDS 60)
WORKER_MAX_TASK_ATTEMPTS=$(get_var_or WORKER_MAX_TASK_ATTEMPTS 3)
# Сколько вопросов повторной проверки качества проверяется одновременно.
WORKER_REVALIDATION_CONCURRENCY=$(get_var_or WORKER_REVALIDATION_CONCURRENCY 1)

# Chunker
CHUNKER_MAX_UPLOAD_BYTES=${CHUNKER_MAX_UPLOAD_BYTES_VAL}
# Таймаут обработки одного документа, сек. (chunker/main.py)
CHUNKER_PROCESS_TIMEOUT=$(get_var_or CHUNKER_PROCESS_TIMEOUT 1800)
# То же для метода chunking_method=llm (десятки вызовов модели на документ);
# WORKER_CHUNK_TIMEOUT_LLM воркера должен быть больше.
CHUNKER_PROCESS_TIMEOUT_LLM=$(get_var_or CHUNKER_PROCESS_TIMEOUT_LLM 7000)
# Размеры чанков прозы (учебник/энциклопедия), символы: цель упаковки,
# потолок, жёсткий потолок для списков, минимум склейки, порог огрызка.
CHUNKER_TEXTBOOK_TARGET_CHARS=$(get_var_or CHUNKER_TEXTBOOK_TARGET_CHARS 1500)
CHUNKER_TEXTBOOK_MAX_CHARS=$(get_var_or CHUNKER_TEXTBOOK_MAX_CHARS 2000)
CHUNKER_TEXTBOOK_LIST_HARD_CAP=$(get_var_or CHUNKER_TEXTBOOK_LIST_HARD_CAP 6000)
CHUNKER_PROSE_MIN_CHARS_TEXTBOOK=$(get_var_or CHUNKER_PROSE_MIN_CHARS_TEXTBOOK 400)
CHUNKER_PROSE_MIN_CHARS_ENCYCLOPEDIA=$(get_var_or CHUNKER_PROSE_MIN_CHARS_ENCYCLOPEDIA 200)
CHUNKER_PROSE_DEBRIS_MAX_CHARS=$(get_var_or CHUNKER_PROSE_DEBRIS_MAX_CHARS 120)
# Через запятую: заголовки разделов учебника, которые выбрасывать / никогда не
# выбрасывать (поверх встроенных «Вопросы и задания», «Введение», …), и
# дополнительные сокращения, после которых предложение не заканчивается.
CHUNKER_TEXTBOOK_DROP_HEADINGS=$(sq "$(get_var_or CHUNKER_TEXTBOOK_DROP_HEADINGS "")")
CHUNKER_TEXTBOOK_KEEP_HEADINGS=$(sq "$(get_var_or CHUNKER_TEXTBOOK_KEEP_HEADINGS "")")
CHUNKER_ABBREVIATIONS_EXTRA=$(sq "$(get_var_or CHUNKER_ABBREVIATIONS_EXTRA "")")
# Метод layout: подзаголовки «1 Название.» в начале абзаца и разбор колонок во
# врезках; 0 — откат к прежнему поведению без пересборки.
CHUNKER_LAYOUT_RUNIN_HEADINGS=$(get_var_or CHUNKER_LAYOUT_RUNIN_HEADINGS 1)
CHUNKER_LAYOUT_COLUMNS=$(get_var_or CHUNKER_LAYOUT_COLUMNS 1)
# Метод semantic: OpenAI-совместимый эндпоинт эмбеддингов (…/v1) и модель;
# пусто — метод недоступен в UI-выборе (чанкер вернёт 400).
# По умолчанию — сервис embedder (TEI) этого же стека.
EMBEDDER_MODEL_ID=$(sq "$(get_var_or EMBEDDER_MODEL_ID "deepvk/USER-bge-m3")")
EMBEDDER_GPU_DEVICE=$(sq "$(get_var_or EMBEDDER_GPU_DEVICE "1")")
CHUNKER_EMBEDDING_URL=$(sq "$(get_var_or CHUNKER_EMBEDDING_URL "http://embedder:80/v1")")
CHUNKER_EMBEDDING_MODEL=$(sq "$(get_var_or CHUNKER_EMBEDDING_MODEL "$(get_var_or EMBEDDER_MODEL_ID "deepvk/USER-bge-m3")")")
CHUNKER_EMBEDDING_API_KEY=$(sq "$(get_var_or CHUNKER_EMBEDDING_API_KEY "none")")
CHUNKER_EMBEDDING_TIMEOUT=$(get_var_or CHUNKER_EMBEDDING_TIMEOUT 60)
# Порог близости соседних абзацев (ниже — граница) и адаптивный процентиль.
CHUNKER_SEMANTIC_THRESHOLD=$(get_var_or CHUNKER_SEMANTIC_THRESHOLD 0.5)
CHUNKER_SEMANTIC_PERCENTILE=$(get_var_or CHUNKER_SEMANTIC_PERCENTILE 20)
# Метод llm (сегментация абзацев моделью): id модели из MODEL_ENDPOINTS
# (пусто — LLM_URL_MODEL/LLM_MODEL_NAME), окно в символах, параллельность,
# таймаут одного вызова, бюджет окон на документ, контекст, max_tokens ответа.
CHUNKER_LLM_SEG_MODEL_ID=$(sq "$(get_var_or CHUNKER_LLM_SEG_MODEL_ID "")")
CHUNKER_LLM_SEG_WINDOW_CHARS=$(get_var_or CHUNKER_LLM_SEG_WINDOW_CHARS 12000)
CHUNKER_LLM_SEG_CONCURRENCY=$(get_var_or CHUNKER_LLM_SEG_CONCURRENCY 2)
CHUNKER_LLM_SEG_WINDOW_TIMEOUT=$(get_var_or CHUNKER_LLM_SEG_WINDOW_TIMEOUT 240)
CHUNKER_LLM_SEG_MAX_WINDOWS=$(get_var_or CHUNKER_LLM_SEG_MAX_WINDOWS 400)
CHUNKER_LLM_SEG_CONTEXT_PARAS=$(get_var_or CHUNKER_LLM_SEG_CONTEXT_PARAS 3)
CHUNKER_LLM_SEG_MAX_TOKENS=$(get_var_or CHUNKER_LLM_SEG_MAX_TOKENS 3000)
CHUNKER_LLM_SEG_MAX_CHARS=$(get_var_or CHUNKER_LLM_SEG_MAX_CHARS 4000)
CHUNKER_LLM_SEG_INSET_MIN_CHARS=$(get_var_or CHUNKER_LLM_SEG_INSET_MIN_CHARS 80)

# Models
MODEL_ENDPOINTS=$(sq "$(get_var MODEL_ENDPOINTS)")

# Модели платформы: ArgoCD даёт список и статус развёрнутых моделей,
# LiteLLM-шлюз — адрес и ключ для их вызова (модели кластера доступны только через него).
ARGOCD_URL=$(sq "${ARGOCD_URL_VAL}")
ARGOCD_USERNAME=$(sq "$(get_var_or ARGOCD_USERNAME admin)")
# Discovery выключен, пока ARGOCD_PASSWORD пуст.
ARGOCD_PASSWORD=$(sq "$(get_var ARGOCD_PASSWORD)")
# Пусто — модели LiteLLM не опрашиваются.
LITELLM_BASE_URL=$(sq "$(get_var LITELLM_BASE_URL)")
# Виртуальный ключ GenA в LiteLLM (может быть пуст).
LITELLM_API_KEY=$(sq "$(get_var LITELLM_API_KEY)")

# Web — только *_DEV/_MAIN, иначе env-specific дефолт
WEB_PORT=$(get_isolated_var WEB_PORT "$WEB_PORT_DEFAULT")

# Docker — только *_DEV/_MAIN, иначе env-specific дефолт.
# Общие (без постфикса) GENA_NET / *_CONTAINER_NAME из GitLab Variables
# намеренно игнорируются, чтобы dev и main не пересекались.
GENA_NET=$(sq "$(get_isolated_var GENA_NET "gena_net_${ENV_SUFFIX}")")
AGENT_API_CONTAINER_NAME=$(sq "$(get_isolated_var AGENT_API_CONTAINER_NAME "agent_api_${ENV_SUFFIX}")")
GENA_FRONTEND_CONTAINER_NAME=$(sq "$(get_isolated_var GENA_FRONTEND_CONTAINER_NAME "gena_frontend_${ENV_SUFFIX}")")
DATASET_API_CONTAINER_NAME=$(sq "$(get_isolated_var DATASET_API_CONTAINER_NAME "dataset_api_${ENV_SUFFIX}")")
TASK_WORKER_CONTAINER_NAME=$(sq "$(get_isolated_var TASK_WORKER_CONTAINER_NAME "task_worker_${ENV_SUFFIX}")")
GENA_CHUNKER_CONTAINER_NAME=$(sq "$(get_isolated_var GENA_CHUNKER_CONTAINER_NAME "chunker_${ENV_SUFFIX}")")
GENA_EMBEDDER_CONTAINER_NAME=$(sq "$(get_isolated_var GENA_EMBEDDER_CONTAINER_NAME "embedder_${ENV_SUFFIX}")")
EOF

if grep -q "$SQ_INVALID_MARKER" .env; then
  echo "gen_env.sh: .env не создан — недопустимые значения у переменных:" >&2
  grep -oE "^[A-Z][A-Z0-9_]*=$SQ_INVALID_MARKER" .env | cut -d= -f1 >&2
  rm -f .env
  exit 1
fi

echo "✓ .env для окружения '$ENV' создан"
