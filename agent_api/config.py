import os
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from loguru import logger

# Load environment variables from .env file if it exists
load_dotenv()


def _llm_defaults_paths() -> list[Path]:
    """Репозиторий: gena/config/... ; образ: /app/config/... (см. Dockerfile)."""
    base = Path(__file__).resolve().parent
    return [
        base.parent / "config" / "llm_defaults.env",
        base / "config" / "llm_defaults.env",
    ]


def _load_llm_defaults_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path in _llm_defaults_paths():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
        break
    return out


_LLM_DEFAULTS = _load_llm_defaults_file()


def _env_llm(key: str, fallback: str = "") -> str:
    v = os.getenv(key)
    if v is not None and v != "":
        return v
    return _LLM_DEFAULTS.get(key, fallback)


MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PORT = os.getenv("MONGO_PORT", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "gena_db_test")
MONGO_DB_PATH = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/"


LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
LLM_URL_MODEL = os.getenv("LLM_URL_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = _env_llm("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = _env_llm("GIGACHAT_MODEL", "GigaChat-2")

YANDEX_CLOUD_API_KEY = os.getenv("YANDEX_CLOUD_API_KEY", "")
YANDEX_CLOUD_FOLDER = os.getenv("YANDEX_CLOUD_FOLDER", "")
YANDEX_CLOUD_MODEL = _env_llm("YANDEX_CLOUD_MODEL", "yandexgpt-5-lite/latest")

MAX_LEN_USER_PROMPT = os.getenv("MAX_LEN_USER_PROMPT")
