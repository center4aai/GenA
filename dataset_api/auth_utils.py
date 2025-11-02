from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel
from fastapi import HTTPException, Depends, status
from pymongo import MongoClient, ASCENDING

from config import JWT_SECRET, JWT_ALGO, ACCESS_TOKEN_EXPIRE_MINUTES
from config import MONGO_DB_PATH
from config import EXPERT_USERNAME, EXPERT_PASSWORD, USER_USERNAME, USER_PASSWORD

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

Role = Literal["expert", "user"]

class TokenData(BaseModel):
    username: str
    role: Role

def get_mongo_client() -> MongoClient:
    return MongoClient(MONGO_DB_PATH)

def users_collection():
    return get_mongo_client()["gena_db"]["users"]

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
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return TokenData(username=payload["sub"], role=payload["role"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

async def get_current_user() -> TokenData:
    return TokenData(username="public", role="expert")

def require_role(required: Role):
    async def _dep(user: TokenData = Depends(get_current_user)) -> TokenData:
        return user
    return _dep

def seed_users_locked():
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