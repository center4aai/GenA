import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, ValidationError
from fastapi import HTTPException, Depends, status, Header
from pymongo import MongoClient, ASCENDING

from config import JWT_SECRET, ACCESS_TOKEN_EXPIRE_MINUTES
from config import MONGO_HOST, MONGO_PORT, MONGO_USERNAME, MONGO_PASSWORD, MONGO_DB_NAME, build_mongo_uri
from config import EXPERT_USERNAME, EXPERT_PASSWORD, USER_USERNAME, USER_PASSWORD

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

Role = Literal["expert", "user"]
JWT_ALGO = "HS256"

AUTH_DISABLED = os.getenv("AUTH_DISABLED", "true").lower() in ("1", "true", "yes")
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "")

logger = logging.getLogger(__name__)
if AUTH_DISABLED:
    logger.critical(
        "AUTH_DISABLED=true: the API is fully open as expert. "
        "Set AUTH_DISABLED=false for production."
    )
elif not (JWT_SECRET or "").strip():
    raise RuntimeError("JWT_SECRET is required when AUTH_DISABLED=false")

class TokenData(BaseModel):
    username: str
    role: Role

_mongo_client: Optional[MongoClient] = None


def get_mongo_client() -> MongoClient:
    global _mongo_client
    if _mongo_client is None:
        # Credentials are used only when both are set; an empty pair means no auth
        uri = build_mongo_uri(
            MONGO_USERNAME if MONGO_PASSWORD else "", MONGO_PASSWORD, MONGO_HOST, MONGO_PORT
        )
        _mongo_client = MongoClient(uri)
    return _mongo_client


def users_collection():
    return get_mongo_client()[MONGO_DB_NAME]["users"]

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)

def create_access_token(username: str, role: Role) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGO],
            options={"require_exp": True, "leeway": 30},
        )
        username = payload.get("sub")
        role = payload.get("role")
        if not isinstance(username, str) or not isinstance(role, str):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        username = username.strip()
        if not username or not role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return TokenData(username=username, role=role)
    except (JWTError, ValidationError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

async def get_current_user(authorization: Optional[str] = Header(None)) -> TokenData:
    if AUTH_DISABLED:
        return TokenData(username="public", role="expert")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return decode_token(token)

def require_role(required: Role):
    async def _dep(user: TokenData = Depends(get_current_user)) -> TokenData:
        if AUTH_DISABLED:
            return user
        if user.role != required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return _dep

async def verify_worker_key(x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key")) -> None:
    if AUTH_DISABLED:
        return
    if not WORKER_API_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Worker API key not configured")
    if not x_worker_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid worker key")
    if len(x_worker_key) != len(WORKER_API_KEY) or not secrets.compare_digest(x_worker_key, WORKER_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid worker key")

def seed_users_locked():
    if not EXPERT_PASSWORD or not USER_PASSWORD:
        return
    col = users_collection()
    try:
        col.create_index([("username", ASCENDING)], unique=True)
    except Exception:
        pass

    col.update_one(
        {"username": EXPERT_USERNAME},
        {"$set": {
            "username": EXPERT_USERNAME,
            "password_hash": hash_password(EXPERT_PASSWORD),
            "role": "expert"
        }},
        upsert=True,
    )

    col.update_one(
        {"username": USER_USERNAME},
        {"$set": {
            "username": USER_USERNAME,
            "password_hash": hash_password(USER_PASSWORD),
            "role": "user"
        }},
        upsert=True,
    )

    col.delete_many({"username": {"$nin": [EXPERT_USERNAME, USER_USERNAME]}})
