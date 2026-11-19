import ipaddress
import json
import logging
import os
from pathlib import Path
from typing import Dict
from urllib.parse import quote_plus, urlencode, urlparse

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

logger = logging.getLogger(__name__)


def _safe_int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Discovery моделей: ArgoCD (Application'ы vllm-* из llm-gitops) + LiteLLM —
# единственный внешний вход к Service моделей (они ClusterIP-only).
# Адреса приходят из конфигурации развёртывания (.env); пустой URL выключает
# соответствующую часть discovery. ArgoCD опрашивается только при ARGOCD_PASSWORD.
ARGOCD_URL = (os.getenv("ARGOCD_URL") or "").strip()
ARGOCD_USERNAME = (os.getenv("ARGOCD_USERNAME") or "admin").strip()
ARGOCD_PASSWORD = os.getenv("ARGOCD_PASSWORD", "")
LITELLM_BASE_URL = (os.getenv("LITELLM_BASE_URL") or "").strip()
# Пробный chat-запрос (max_tokens=1) к каждой модели для /models/health/, раз в столько секунд;
# 0 — выключен (тогда health видит только наличие модели в /models эндпоинта).
MODEL_SMOKE_INTERVAL_SEC = _safe_int_env("MODEL_SMOKE_INTERVAL_SEC", 900)
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "").strip()


def _non_ip_hostname(url: str) -> str | None:
    """Hostname из URL, если это имя, а не IP (IP проверяются через is_global)."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        return host


_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal"}
)


def _parse_allowed_cidrs(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Список сетей из строки через запятую; невалидные элементы пропускаются с предупреждением."""
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("OUTBOUND_ALLOWED_CIDRS: некорректная сеть %r пропущена", item)
    return nets


# Приватные сети через запятую, в которые разрешены исходящие запросы, несмотря на
# SSRF-фильтр (например, LLM-эндпоинт, опубликованный только во внутренней сети:
# OUTBOUND_ALLOWED_CIDRS=192.168.10.0/24). По умолчанию пусто — allowlist выключен,
# блокируются все не-глобальные адреса.
OUTBOUND_ALLOWED_CIDRS = os.getenv("OUTBOUND_ALLOWED_CIDRS", "")
_OUTBOUND_ALLOWED_NETWORKS = _parse_allowed_cidrs(OUTBOUND_ALLOWED_CIDRS)


def is_safe_outbound_url(url: str) -> bool:
    """Block SSRF to non-global IPs; hostnames only if allowlisted."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        host_lower = host.lower().rstrip(".")
        if host_lower in _BLOCKED_HOSTNAMES or host_lower.endswith(".localhost"):
            return False
        try:
            addr = ipaddress.ip_address(host_lower)
            if any(addr in net for net in _OUTBOUND_ALLOWED_NETWORKS):
                return True
            return addr.is_global
        except ValueError:
            return host_lower in _OUTBOUND_ALLOWED_HOSTS
    except Exception:
        return False


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


def _env_llm_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is not None and str(v).strip() != "":
        return _safe_int_env(key, default)
    file_val = _LLM_DEFAULTS.get(key, "")
    if file_val:
        try:
            return int(str(file_val).strip())
        except ValueError:
            return default
    return default


MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_HOST = os.getenv("MONGO_HOST", "")
MONGO_PORT = os.getenv("MONGO_PORT", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "gena_db_test")


def build_mongo_uri(username: str, password: str, host: str, port: str | int, **params) -> str:
    """``mongodb://user:pass@host:port/[?params]``.

    username/password экранируются quote_plus (пароль с ``@:/%`` иначе ломает URI).
    Пустой username → ``mongodb://host:port/`` (``mongodb://:@...`` pymongo не принимает).
    ``params`` (authSource=…) — query-строка; None и "" пропускаются.
    """
    userinfo = f"{quote_plus(username)}:{quote_plus(password or '')}@" if username else ""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"mongodb://{userinfo}{host}:{port}/" + (f"?{query}" if query else "")


MONGO_DB_PATH = build_mongo_uri(MONGO_USERNAME, MONGO_PASSWORD, MONGO_HOST, MONGO_PORT)


LLM_MODEL_NAME = _env_llm("LLM_MODEL_NAME", "")
LLM_URL_MODEL = _env_llm("LLM_URL_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
MODEL_ENDPOINTS_JSON = _env_llm("MODEL_ENDPOINTS", "")


def _endpoint_hostnames(raw_json: str) -> set[str]:
    """Hostnames of MODEL_ENDPOINTS base URLs; a malformed list contributes none."""
    try:
        endpoints = json.loads(raw_json) if raw_json else []
        urls = [ep.get("base_url") or "" for ep in endpoints]
    except (ValueError, TypeError, AttributeError):
        return set()
    return {h for h in map(_non_ip_hostname, urls) if h}


# Hostnames are trusted only when the operator configured them: a model container
# on the compose network (MODEL_ENDPOINTS, LLM_URL_MODEL), LiteLLM and ArgoCD.
# IP addresses go through is_global / OUTBOUND_ALLOWED_CIDRS instead.
_OUTBOUND_ALLOWED_HOSTS = frozenset(
    {"ai.api.cloud.yandex.net"}
    | {h for h in map(_non_ip_hostname, (LITELLM_BASE_URL, ARGOCD_URL, LLM_URL_MODEL)) if h}
    | _endpoint_hostnames(MODEL_ENDPOINTS_JSON)
)

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = _env_llm("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = _env_llm("GIGACHAT_MODEL", "GigaChat-2")

YANDEX_CLOUD_API_KEY = os.getenv("YANDEX_CLOUD_API_KEY", "")
YANDEX_CLOUD_FOLDER = os.getenv("YANDEX_CLOUD_FOLDER", "")
YANDEX_CLOUD_MODEL = _env_llm("YANDEX_CLOUD_MODEL", "yandexgpt-5-lite/latest")

MAX_LEN_USER_PROMPT = str(_env_llm_int("MAX_LEN_USER_PROMPT", 32000))
# The gate is the first real LLM call of a run: a cold 32B model can take well
# over a minute to load, so its timeout must cover the load, not just inference.
GATE_LLM_TIMEOUT = _env_llm_int("GATE_LLM_TIMEOUT", 180)
# Default per-call timeout and SDK retry count for every other LLM chain
# (generation, validation, refine, assessments, rephrase). SDK retries multiply
# load on an already busy host, so they default to a single retry; the queue
# (task_worker retry-failed) is the place for real retries.
LLM_TIMEOUT = _env_llm_int("LLM_TIMEOUT", 90)
LLM_MAX_RETRIES = _env_llm_int("LLM_MAX_RETRIES", 1)
LLM_PROVIDER = _env_llm("LLM_PROVIDER", "openai")
GIGACHAT_VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
MILVUS_ENABLED = os.getenv("MILVUS_ENABLED", "false").lower() in ("1", "true", "yes")
MILVUS_HOST = os.getenv("MILVUS_HOST", "")
MILVUS_PORT = _safe_int_env("MILVUS_PORT", 19530)
TEI_MODEL_NAME = os.getenv("TEI_MODEL_NAME", "")
TEI_URL_EMBEDDER = os.getenv("TEI_URL_EMBEDDER", "")
TEI_API_KEY = os.getenv("TEI_API_KEY", "")
