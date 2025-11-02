import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# Load environment variables from .env file if it exists
load_dotenv()

MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PORT = os.getenv("MONGO_PORT", "")
MONGO_DB_PATH = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/"


LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
LLM_URL_MODEL = os.getenv("LLM_URL_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

MAX_LEN_USER_PROMPT = os.getenv("MAX_LEN_USER_PROMPT")