import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

_dataset_api_fallback = os.getenv("API_DATASET_URL", "http://dataset_api:8789")

# Task Queue API Configuration (now part of dataset_api)
TASK_QUEUE_API_URL = os.getenv("TASK_QUEUE_API_URL", _dataset_api_fallback)

# Agent API Configuration
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://agent_api:8790")

# Chunker Configuration (async chunking runs in the worker instead of the browser)
CHUNKER_URL = os.getenv("CHUNKER_URL", "http://chunker:8517")
# Chunking a large document can take a while (docx parse + LLM doc-type call).
WORKER_CHUNK_TIMEOUT = int(os.getenv("WORKER_CHUNK_TIMEOUT", "1800"))
# chunking_method=llm: сегментация моделью занимает десятки минут на учебник;
# должен быть больше CHUNKER_PROCESS_TIMEOUT_LLM чанкера.
WORKER_CHUNK_TIMEOUT_LLM = int(os.getenv("WORKER_CHUNK_TIMEOUT_LLM", "7200"))

# Dataset API Configuration
DATASET_API_URL = os.getenv("DATASET_API_URL", _dataset_api_fallback)
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "")
# Worker Configuration
WORKER_POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))  # seconds
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "5"))
WORKER_MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))
# HTTP timeout to agent API (per attempt); retries on timeout only
WORKER_AGENT_TIMEOUT = int(os.getenv("WORKER_AGENT_TIMEOUT", "600"))
WORKER_AGENT_MAX_RETRIES = int(os.getenv("WORKER_AGENT_MAX_RETRIES", "2"))
# Экспоненциальный backoff между повторами HTTP-запросов воркера:
# BASE * FACTOR**attempt секунд (attempt = 0, 1, 2 → 1, 4, 16), но не больше MAX.
# Заголовок Retry-After сервера, если он числовой, имеет приоритет.
# Не читается из окружения: это модульные константы, а не деплой-настройка.
WORKER_RETRY_BACKOFF_BASE = 1
WORKER_RETRY_BACKOFF_FACTOR = 4
WORKER_RETRY_BACKOFF_MAX = 60

# How long a task may sit in ``processing`` without an ``updated_at`` refresh
# before it is considered orphaned and reclaimed by the recovery loop.
WORKER_STUCK_THRESHOLD_MINUTES = int(os.getenv("WORKER_STUCK_THRESHOLD_MINUTES", "15"))
# How often (seconds) the worker's main loop re-runs ``recover_stuck_tasks``.
# At startup recovery still happens once unconditionally.
WORKER_RECOVERY_INTERVAL_SECONDS = int(os.getenv("WORKER_RECOVERY_INTERVAL_SECONDS", "300"))
# Heartbeat period (seconds) for the in-flight task: while ``process_task`` is
# waiting for the agent we periodically refresh ``updated_at`` so the recovery
# loop does not yank a task that is genuinely still being processed.
WORKER_HEARTBEAT_SECONDS = int(os.getenv("WORKER_HEARTBEAT_SECONDS", "60"))
# Hard cap on how many times a single task may be requeued by recovery.
# Once exceeded the task is force-failed instead of returned to ``pending``.
WORKER_MAX_TASK_ATTEMPTS = int(os.getenv("WORKER_MAX_TASK_ATTEMPTS", "3"))
# Quality revalidation items checked at once (each is one validator call).
# 1 keeps the shared LLM host load as before; raise it for big runs on a
# model that serves parallel requests.
WORKER_REVALIDATION_CONCURRENCY = int(os.getenv("WORKER_REVALIDATION_CONCURRENCY", "1"))


DATASET_API_USER = os.getenv("DATASET_API_USER", "expert")
DATASET_API_PASS = os.getenv("DATASET_API_PASS", "")
WORKER_TOKEN_TTL = int(os.getenv("WORKER_TOKEN_TTL", "3600"))
