import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Task Queue API Configuration (now part of dataset_api)
#TASK_QUEUE_API_URL = os.getenv("TASK_QUEUE_API_URL", "http://dataset-api:8789")
TASK_QUEUE_API_URL = os.getenv("TASK_QUEUE_API_URL", "http://dataset-api:8789")

# Agent API Configuration
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://agent-api:8790")

# Dataset API Configuration
#DATASET_API_URL = os.getenv("DATASET_API_URL", "http://dataset-api:8789")
DATASET_API_URL = os.getenv("DATASET_API_URL", "http://dataset-api:8789")
# Worker Configuration
WORKER_POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))  # seconds
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "5"))
WORKER_MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))

DATASET_API_USER = os.getenv("DATASET_API_USER", "expert")
DATASET_API_PASS = os.getenv("DATASET_API_PASS", "expert123")
WORKER_TOKEN_TTL=3600