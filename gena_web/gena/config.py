import os
from pathlib import Path

from dotenv import load_dotenv
try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
load_dotenv()

# Milvus
MILVUS_HOST = os.getenv("MILVUS_HOST")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME")

# API и директории
AGENT_API_URL = os.getenv("AGENT_API_URL","")
API_CHANKS_URL = os.getenv("API_CHANKS_URL")
CHUNKS_DIR = os.getenv("CHUNKS_DIR")
API_GEN_QUE_URL = os.getenv("API_GEN_QUE_URL")
API_DATASET_URL = os.getenv("API_DATASET_URL")
MAX_LEN_USER_PROMPT = os.getenv("MAX_LEN_USER_PROMPT")

# Paths
PROJ_ROOT = Path(__file__).resolve().parents[1]
logger.info(f"PROJ_ROOT path is: {PROJ_ROOT}")

DATA_DIR = PROJ_ROOT / "data"
DOCS_DIR = PROJ_ROOT / "docs"

LOGO = PROJ_ROOT / "extensions" / "logo" / "logo.png"

MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PORT = os.getenv("MONGO_PORT", "")
MONGO_DB_PATH = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/"

LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
LLM_URL_MODEL = os.getenv("LLM_URL_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

#AUTH_API_URL    = os.getenv("AUTH_API_URL",    "http://dataset-api:8789")
AUTH_API_URL    = os.getenv("AUTH_API_URL",    "")

#API_DATASET_URL = os.getenv("API_DATASET_URL", "http://dataset-api:8789")
API_DATASET_URL = os.getenv("API_DATASET_URL", "")
