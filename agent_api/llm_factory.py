"""
Фабрика LLM: создаёт нужный BaseChatModel по имени провайдера.

Поддерживаемые провайдеры:
  - "openai"   — ChatOpenAI (vLLM, llama.cpp, OpenAI-compatible)
  - "gigachat" — GigaChat через langchain-gigachat (OAuth автообновление)
  - "yandex"   — YandexGPT через ChatOpenAI + совместимый endpoint Yandex AI
"""

import logging
from typing import List, Optional, Tuple, Type

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    LLM_MODEL_NAME,
    LLM_URL_MODEL,
    LLM_API_KEY,
    LLM_PROVIDER,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
    GIGACHAT_MODEL,
    GIGACHAT_VERIFY_SSL,
    YANDEX_CLOUD_API_KEY,
    YANDEX_CLOUD_FOLDER,
    YANDEX_CLOUD_MODEL,
    is_safe_outbound_url,
)

logger = logging.getLogger(__name__)

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"



# ---------------------------------------------------------------------------
# Классификация ошибок LLM-вызова (общая для gate, генерации, оценок, refine)
# ---------------------------------------------------------------------------

def _transient_llm_errors() -> Tuple[Type[BaseException], ...]:
    """Ошибки, которые цепочка НЕ должна превращать в результат.

    Таймаут, недоступность, 429/5xx — транзиентные, их переживает ретрай на
    уровне очереди. AuthenticationError транзиентной не является, но тоже
    здесь: неверный ключ — инфраструктурный сбой, а не вердикт по содержанию.
    """
    errors: List[Type[BaseException]] = [TimeoutError]
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
            InternalServerError,
        )

        errors.extend([
            APITimeoutError,
            APIConnectionError,
            AuthenticationError,
            RateLimitError,
            InternalServerError,
        ])
    except ImportError:
        pass
    try:
        errors.append(httpx.TimeoutException)
    except Exception:  # pragma: no cover
        pass
    return tuple(errors)


TRANSIENT_LLM_ERRORS = _transient_llm_errors()


def transient_llm_error_kind(exc: BaseException) -> Optional[str]:
    """Какой класс инфраструктурной LLM-ошибки перед нами.

    "timeout" | "unreachable" | "busy" | "auth" | None — API-слой по этому
    выбирает HTTP-статус (504/502/503/401) и константное, различимое для
    worker'а тело ответа. None — ошибка не инфраструктурная (парсинг, 400,
    404 модели и т.п.).
    """
    timeout_types: List[Type[BaseException]] = [TimeoutError, httpx.TimeoutException]
    connection_types: List[Type[BaseException]] = [httpx.ConnectError]
    busy_types: List[Type[BaseException]] = []
    auth_types: List[Type[BaseException]] = []
    status_error_types: List[Type[BaseException]] = []
    busy_statuses = (429, 503)
    auth_statuses = (401, 403)
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )

        timeout_types.append(APITimeoutError)
        connection_types.append(APIConnectionError)
        busy_types.append(RateLimitError)
        auth_types.extend([AuthenticationError, PermissionDeniedError])
        status_error_types.append(APIStatusError)
    except ImportError:
        pass
    # APITimeoutError наследует APIConnectionError — сначала проверяем таймаут.
    if isinstance(exc, tuple(timeout_types)):
        return "timeout"
    if isinstance(exc, tuple(connection_types)):
        return "unreachable"
    if auth_types and isinstance(exc, tuple(auth_types)):
        return "auth"
    if isinstance(exc, tuple(busy_types)):
        return "busy"
    if status_error_types and isinstance(exc, tuple(status_error_types)):
        status = getattr(exc, "status_code", None)
        if status in busy_statuses:
            return "busy"
        if status in auth_statuses:
            return "auth"
    return None


def _blank(value: Optional[str]) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _resolve_provider(
    provider: Optional[str],
    model_name: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> str:
    """Honor LLM_PROVIDER when callers pass only the openai sentinel with env defaults."""
    provider_norm = (provider or "").lower().strip()
    if provider_norm and provider_norm != "openai":
        return provider_norm
    if _blank(model_name) and _blank(base_url) and _blank(api_key):
        return (LLM_PROVIDER or "openai").lower().strip()
    return provider_norm or (LLM_PROVIDER or "openai").lower().strip()


def _assert_safe_base_url(base_url: str) -> None:
    if base_url and not is_safe_outbound_url(base_url):
        raise ValueError(f"Unsafe LLM base_url blocked: {base_url}")


def create_chat_llm(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None,
    **extra,
) -> BaseChatModel:
    """
    Возвращает BaseChatModel для заданного провайдера.

    Args:
        provider: "openai" | "gigachat" | "yandex"
        model_name: имя модели (у каждого провайдера своё)
        base_url: базовый URL endpoint-а
        api_key: ключ / credentials
        temperature, max_tokens, timeout: параметры генерации
        max_retries: SDK-ретраи транзиентных ошибок (только openai-совместимые)
        **extra: доп. параметры провайдера (scope, folder_id, …)

    timeout/max_retries по умолчанию берутся из config.LLM_TIMEOUT /
    config.LLM_MAX_RETRIES (env), чтобы политика была одна на все цепочки.
    """
    if timeout is None:
        timeout = LLM_TIMEOUT
    if max_retries is None:
        max_retries = LLM_MAX_RETRIES
    provider = _resolve_provider(provider, model_name, base_url, api_key)

    if provider == "gigachat":
        return _create_gigachat(
            model_name=model_name,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            scope=extra.get("scope"),
        )

    if provider == "yandex":
        return _create_yandex(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            folder_id=extra.get("folder_id"),
        )

    return _create_openai_compatible(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        reasoning_effort=extra.get("reasoning_effort"),
    )


def _create_openai_compatible(
    model_name: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: int,
    max_retries: int = 1,
    reasoning_effort: Optional[str] = None,
) -> ChatOpenAI:
    resolved_base = base_url or LLM_URL_MODEL
    _assert_safe_base_url(resolved_base)
    # chat_template_kwargs понимает llama.cpp/vLLM; Ollama (OpenAI-compat) его
    # игнорирует и для thinking-моделей (qwen3.6) отключает рассуждения только
    # через reasoning_effort="none" — задаётся в MODEL_ENDPOINTS[].extra.
    extra_body: dict = {"chat_template_kwargs": {"enable_thinking": False}}
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(
        model=model_name or LLM_MODEL_NAME,
        openai_api_base=resolved_base,
        openai_api_key=api_key or LLM_API_KEY or "none",
        temperature=temperature,
        max_retries=max_retries,
        streaming=False,
        timeout=timeout,
        max_tokens=max_tokens,
        model_kwargs={"extra_body": extra_body},
    )


def _create_gigachat(
    model_name: Optional[str],
    api_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: int,
    scope: Optional[str] = None,
) -> BaseChatModel:
    from langchain_gigachat import GigaChat

    credentials = api_key or GIGACHAT_CREDENTIALS
    if not credentials:
        raise ValueError(
            "GigaChat credentials not configured. "
            "Set GIGACHAT_CREDENTIALS env or pass api_key."
        )

    return GigaChat(
        credentials=credentials,
        scope=scope or GIGACHAT_SCOPE,
        model=model_name or GIGACHAT_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        verify_ssl_certs=GIGACHAT_VERIFY_SSL,
    )


def _create_yandex(
    model_name: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: int,
    max_retries: int = 1,
    folder_id: Optional[str] = None,
) -> ChatOpenAI:
    folder = folder_id or YANDEX_CLOUD_FOLDER
    model = model_name or YANDEX_CLOUD_MODEL
    key = api_key or YANDEX_CLOUD_API_KEY

    if not folder or not key:
        raise ValueError(
            "YandexGPT not configured. "
            "Set YANDEX_CLOUD_FOLDER + YANDEX_CLOUD_API_KEY env or pass them explicitly."
        )

    full_model = model if model.startswith("gpt://") else f"gpt://{folder}/{model}"
    resolved_base = base_url or YANDEX_BASE_URL
    _assert_safe_base_url(resolved_base)

    return ChatOpenAI(
        model=full_model,
        openai_api_base=resolved_base,
        openai_api_key=key,
        temperature=temperature,
        max_retries=max_retries,
        streaming=False,
        timeout=timeout,
        max_tokens=max_tokens,
        default_headers={"x-folder-id": folder},
    )
