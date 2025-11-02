import json
from datetime import datetime, timedelta
import requests
import streamlit as st
import extra_streamlit_components as stx
from .config import API_DATASET_URL

BASE = (API_DATASET_URL or "").rstrip("/")

def _url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{BASE}/{path.lstrip('/')}"

def _headers(extra: dict | None = None) -> dict:
    h = {"Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h

def get(path: str, **kw):
    return requests.get(_url(path), headers=_headers(), **kw)

def post(path: str, **kw):
    return requests.post(_url(path), headers=_headers(), **kw)

def put(path: str, **kw):
    return requests.put(_url(path), headers=_headers(), **kw)

def delete(path: str, **kw):
    return requests.delete(_url(path), headers=_headers(), **kw)


def get_cookie_manager():
    """Возвращает mounted CookieManager (в st.session_state)."""
    if "cookie_manager" not in st.session_state:
        st.session_state["cookie_manager"] = stx.CookieManager(key="cookie_mgr_mount")
    return st.session_state["cookie_manager"]

def cm_set_auth(token: str, role: str, username: str, days: int = 30):
    """
    Сохраняет единую куку gena_auth = JSON({token, role, user}).
    expires_at должен быть datetime (CookieManager внутри вызывает .isoformat()).
    """
    cm = get_cookie_manager()
    auth = {"token": token, "role": role, "user": username}
    expire_dt = datetime.utcnow() + timedelta(days=days)
    cm.set("gena_auth", json.dumps(auth), expires_at=expire_dt, path="/", secure=False, same_site="Lax")

def cm_get_auth():
    """
    Возвращает dict {'token','role','user'} или None.
    CookieManager.get возвращает строк (значение куки) или None.
    """
    cm = get_cookie_manager()
    val = cm.get("gena_auth")
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None

def cm_del_auth():
    cm = get_cookie_manager()
    cm.delete("gena_auth")

def cm_get(name, default=None):
    cm = get_cookie_manager()
    val = cm.get(name)
    return val if val is not None else default

def cm_set(name, value, **kwargs):
    cm = get_cookie_manager()
    v = json.dumps(value) if not isinstance(value, str) else value
    cm.set(name, v, **kwargs)

def cm_del(name):
    cm = get_cookie_manager()
    cm.delete(name)