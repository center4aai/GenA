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
if [ -f "$LLM_DEFAULTS_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$LLM_DEFAULTS_FILE"
  set +a
fi

# Функция для подстановки: сначала пробуем с постфиксом, если нет — берем общую
get_var() {
  local var_name=$1
  local with_postfix="${var_name}${POSTFIX}"
  
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

# Генерируем .env
cat > .env << EOF
# MongoDB
MONGO_USERNAME=$(get_var MONGO_USERNAME)
MONGO_PASSWORD=$(get_var MONGO_PASSWORD)
MONGO_HOST=$(get_var MONGO_HOST)
MONGO_PORT=$(get_var MONGO_PORT)
MONGO_DB_NAME=$(get_isolated_var MONGO_DB_NAME "gena_${ENV_SUFFIX}")
MAX_MESSAGES_HISTORY=$(get_var MAX_MESSAGES_HISTORY)

# Milvus
MILVUS_HOST=$(get_var MILVUS_HOST)
MILVUS_PORT=$(get_var MILVUS_PORT)

# API
LLM_MODEL_NAME=$(get_var LLM_MODEL_NAME)
LLM_URL_MODEL=$(get_var LLM_URL_MODEL)
LLM_API_KEY=$(get_var LLM_API_KEY)
API_GEN_QUE_URL=$(get_var API_GEN_QUE_URL)
AGENT_API_URL=$(get_var AGENT_API_URL)

# Embedding
TEI_MODEL_NAME=$(get_var TEI_MODEL_NAME)
TEI_URL_EMBEDDER=$(get_var TEI_URL_EMBEDDER)
TEI_API_KEY=$(get_var TEI_API_KEY)
API_DATASET_URL=$(get_var API_DATASET_URL)
DATASET_API_URL=$(get_var API_DATASET_URL)
TASK_QUEUE_API_URL=$(get_var API_DATASET_URL)

# LLM
MAX_LEN_USER_PROMPT=$(get_var MAX_LEN_USER_PROMPT)
API_CHANKS_URL=$(get_var API_CHANKS_URL)
CHUNKS_DIR=$(get_var CHUNKS_DIR)
MODEL_NAME=$(get_var MODEL_NAME)

# GigaChat (Sber) — scope/model из config/llm_defaults.env (или override из Variables)
GIGACHAT_CREDENTIALS=$(get_var GIGACHAT_CREDENTIALS)
GIGACHAT_SCOPE=$(get_var GIGACHAT_SCOPE)
GIGACHAT_MODEL=$(get_var GIGACHAT_MODEL)

# YandexGPT — model из config/llm_defaults.env; ключ и folder из Variables
YANDEX_CLOUD_API_KEY=$(get_var YANDEX_CLOUD_API_KEY)
YANDEX_CLOUD_FOLDER=$(get_var YANDEX_CLOUD_FOLDER)
YANDEX_CLOUD_MODEL=$(get_var YANDEX_CLOUD_MODEL)

# Provider для chunker
LLM_PROVIDER=$(get_var LLM_PROVIDER)

# Models
MODEL_ENDPOINTS=$(get_var MODEL_ENDPOINTS)

# Web — только *_DEV/_MAIN, иначе env-specific дефолт
WEB_PORT=$(get_isolated_var WEB_PORT "$WEB_PORT_DEFAULT")

# Docker — только *_DEV/_MAIN, иначе env-specific дефолт.
# Общие (без постфикса) GENA_NET / *_CONTAINER_NAME из GitLab Variables
# намеренно игнорируются, чтобы dev и main не пересекались.
GENA_NET=$(get_isolated_var GENA_NET "gena_net_${ENV_SUFFIX}")
AGENT_API_CONTAINER_NAME=$(get_isolated_var AGENT_API_CONTAINER_NAME "agent-api-${ENV_SUFFIX}")
GENA_WEB_CONTAINER_NAME=$(get_isolated_var GENA_WEB_CONTAINER_NAME "gena-web-${ENV_SUFFIX}")
GENA_FRONTEND_CONTAINER_NAME=$(get_isolated_var GENA_FRONTEND_CONTAINER_NAME "gena_frontend_${ENV_SUFFIX}")
DATASET_API_CONTAINER_NAME=$(get_isolated_var DATASET_API_CONTAINER_NAME "dataset-api-${ENV_SUFFIX}")
TASK_WORKER_CONTAINER_NAME=$(get_isolated_var TASK_WORKER_CONTAINER_NAME "task-worker-${ENV_SUFFIX}")
GENA_CHUNKER_CONTAINER_NAME=$(get_isolated_var GENA_CHUNKER_CONTAINER_NAME "gena-chunker-${ENV_SUFFIX}")
RUADAPT_QWEN_SERVER_CONTAINER_NAME=$(get_isolated_var RUADAPT_QWEN_SERVER_CONTAINER_NAME "ruadapt-qwen-${ENV_SUFFIX}")
EOF

echo "✓ .env для окружения '$ENV' создан"
