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


import os
from pathlib import Path
import secrets

SECRETS_DIR = Path(__file__).resolve().parent / ".secrets"
SECRETS_DIR.mkdir(exist_ok=True)
SECRET_FILE = SECRETS_DIR / "jwt_secret.txt"

def _load_or_create_secret() -> str:
    if "JWT_SECRET" in os.environ and os.environ["JWT_SECRET"]:
        return os.environ["JWT_SECRET"]
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    value = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(value)
    return value

JWT_SECRET = _load_or_create_secret()
JWT_ALGO   = os.getenv("JWT_ALGO", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))

EXPERT_USERNAME = os.getenv("EXPERT_USERNAME", "admin")
EXPERT_PASSWORD = os.getenv("EXPERT_PASSWORD", "admin123")

USER_USERNAME   = os.getenv("USER_USERNAME", "user")
USER_PASSWORD   = os.getenv("USER_PASSWORD", "user123")


