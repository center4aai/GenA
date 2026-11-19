import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from auth_utils import AUTH_DISABLED, users_collection, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 10
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_login_rate_limit(client_ip: str, username: str) -> None:
    now = time.monotonic()
    window_start = now - _LOGIN_WINDOW_SECONDS
    keys = (client_ip, f"{client_ip}:{username}")
    for key in keys:
        attempts = [t for t in _login_attempts[key] if t >= window_start]
        _login_attempts[key] = attempts
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts, try again later",
            )
    for key in keys:
        _login_attempts[key].append(now)


class UserLogin(BaseModel):
    username: str
    password: str

@router.post("/login")
async def login(creds: UserLogin, request: Request):
    if AUTH_DISABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    client_ip = _client_ip(request)
    _check_login_rate_limit(client_ip, creds.username)

    col = users_collection()
    doc = col.find_one({"username": creds.username})
    password_hash = doc.get("password_hash") if doc else None
    if not doc or not password_hash or not verify_password(creds.password, password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad credentials")
    role = doc.get("role")
    if role not in ("expert", "user"):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid user role configured")
    token = create_access_token(username=doc["username"], role=role)
    return {"access_token": token, "token_type": "bearer", "role": role}
