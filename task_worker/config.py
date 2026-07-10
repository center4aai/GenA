import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

_dataset_api_fallback = os.getenv("API_DATASET_URL", "http://dataset-api:8789")

# Task Queue API Configuration (now part of dataset_api)
TASK_QUEUE_API_URL = os.getenv("TASK_QUEUE_API_URL", _dataset_api_fallback)

# Agent API Configuration
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://agent-api:8790")

# Dataset API Configuration
DATASET_API_URL = os.getenv("DATASET_API_URL", _dataset_api_fallback)
# Worker Configuration
WORKER_POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))  # seconds
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "5"))
WORKER_MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))
# HTTP timeout to agent API (per attempt); retries on timeout only
WORKER_AGENT_TIMEOUT = int(os.getenv("WORKER_AGENT_TIMEOUT", "600"))
WORKER_AGENT_MAX_RETRIES = int(os.getenv("WORKER_AGENT_MAX_RETRIES", "2"))

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

DATASET_API_USER = os.getenv("DATASET_API_USER", "expert")
DATASET_API_PASS = os.getenv("DATASET_API_PASS", "")
WORKER_TOKEN_TTL=3600