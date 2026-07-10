"""
Фабрика LLM: создаёт нужный BaseChatModel по имени провайдера.

Поддерживаемые провайдеры:
  - "openai"   — ChatOpenAI (vLLM, llama.cpp, OpenAI-compatible)
  - "gigachat" — GigaChat через langchain-gigachat (OAuth автообновление)
  - "yandex"   — YandexGPT через ChatOpenAI + совместимый endpoint Yandex AI
"""

import logging
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config import (
    LLM_MODEL_NAME,
    LLM_URL_MODEL,
    LLM_API_KEY,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_SCOPE,
    GIGACHAT_MODEL,
    YANDEX_CLOUD_API_KEY,
    YANDEX_CLOUD_FOLDER,
    YANDEX_CLOUD_MODEL,
)

logger = logging.getLogger(__name__)

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"


def create_chat_llm(
    provider: str = "openai",
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: int = 90,
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
        **extra: доп. параметры провайдера (scope, folder_id, …)
    """
    provider = (provider or "openai").lower().strip()

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
            folder_id=extra.get("folder_id"),
        )

    return _create_openai_compatible(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def _create_openai_compatible(
    model_name: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or LLM_MODEL_NAME,
        openai_api_base=base_url or LLM_URL_MODEL,
        openai_api_key=api_key or LLM_API_KEY,
        temperature=temperature,
        max_retries=3,
        streaming=False,
        timeout=timeout,
        max_tokens=max_tokens,
        model_kwargs={
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
        },
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
        verify_ssl_certs=False,
    )


def _create_yandex(
    model_name: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: int,
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

    return ChatOpenAI(
        model=full_model,
        openai_api_base=base_url or YANDEX_BASE_URL,
        openai_api_key=key,
        temperature=temperature,
        max_retries=3,
        streaming=False,
        timeout=timeout,
        max_tokens=max_tokens,
        default_headers={"x-folder-id": folder},
    )
