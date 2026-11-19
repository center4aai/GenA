import os
from pathlib import Path
import secrets
from urllib.parse import quote_plus, urlencode

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PORT = os.getenv("MONGO_PORT", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "gena_db_test")


def build_mongo_uri(username: str, password: str, host: str, port: str | int, **params) -> str:
    """Build a MongoDB URI with percent-escaped credentials.

    Credentials are added only when ``username`` is set, so ``@ : / %`` in the
    password no longer break parsing. Non-empty ``params`` become the query string.
    """
    userinfo = f"{quote_plus(username)}:{quote_plus(password or '')}@" if username else ""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"mongodb://{userinfo}{host}:{port}/" + (f"?{query}" if query else "")


MONGO_DB_PATH = build_mongo_uri(MONGO_USERNAME, MONGO_PASSWORD, MONGO_HOST, MONGO_PORT)

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
try:
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
except ValueError:
    logger.warning("Invalid ACCESS_TOKEN_EXPIRE_MINUTES, using default 120")
    ACCESS_TOKEN_EXPIRE_MINUTES = 120

EXPERT_USERNAME = os.getenv("EXPERT_USERNAME", "admin")
EXPERT_PASSWORD = os.getenv("EXPERT_PASSWORD", "")
USER_USERNAME   = os.getenv("USER_USERNAME", "user")
USER_PASSWORD   = os.getenv("USER_PASSWORD", "")


if not EXPERT_PASSWORD or not USER_PASSWORD:
    logger.warning("EXPERT_PASSWORD and USER_PASSWORD are not set; user seeding is skipped")
