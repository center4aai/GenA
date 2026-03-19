#!/bin/bash
set -e

ENV=$1  # prod или dev

if [ "$ENV" = "prod" ]; then
  POSTFIX="_MAIN"
elif [ "$ENV" = "dev" ]; then
  POSTFIX="_DEV"
else
  echo "Usage: $0 {prod|dev}"
  exit 1
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

# Генерируем .env
cat > .env << EOF
# MongoDB
MONGO_USERNAME=$(get_var MONGO_USERNAME)
MONGO_PASSWORD=$(get_var MONGO_PASSWORD)
MONGO_HOST=$(get_var MONGO_HOST)
MONGO_PORT=$(get_var MONGO_PORT)
MONGO_DB_NAME=$(get_var MONGO_DB_NAME)
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

# LLM
MAX_LEN_USER_PROMPT=$(get_var MAX_LEN_USER_PROMPT)
API_CHANKS_URL=$(get_var API_CHANKS_URL)
CHUNKS_DIR=$(get_var CHUNKS_DIR)
MODEL_NAME=$(get_var MODEL_NAME)

# Models
MODEL_ENDPOINTS=$(get_var MODEL_ENDPOINTS)

# Docker
GENA_NET=$(get_var GENA_NET)
AGENT_API_CONTAINER_NAME=$(get_var AGENT_API_CONTAINER_NAME)
GENA_WEB_CONTAINER_NAME=$(get_var GENA_WEB_CONTAINER_NAME)
DATASET_API_CONTAINER_NAME=$(get_var DATASET_API_CONTAINER_NAME)
TASK_WORKER_CONTAINER_NAME=$(get_var TASK_WORKER_CONTAINER_NAME)
GENA_CHUNKER_CONTAINER_NAME=$(get_var GENA_CHUNKER_CONTAINER_NAME)
RUADAPT_QWEN_SERVER_CONTAINER_NAME=$(get_var RUADAPT_QWEN_SERVER_CONTAINER_NAME)
EOF

echo "✓ .env для окружения '$ENV' создан"
