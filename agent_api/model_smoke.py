"""
Пробный chat-запрос к модели (smoke): проходит ли через неё инференс.

``GET /models`` отвечает 200 и тогда, когда сама модель не вызывается: бэкенд
LiteLLM не запущен, ключ облака истёк, сертификат не проходит проверку. Поэтому
реестр изредка делает настоящий запрос тем же путём, что и генерация
(``llm_factory.create_chat_llm``), и показывает в health короткую причину провала.
"""

import logging
import ssl
from typing import Optional

from llm_factory import create_chat_llm, transient_llm_error_kind

logger = logging.getLogger(__name__)

SMOKE_TIMEOUT = 20
SMOKE_PROMPT = "ping"

REASON_SSL = "ошибка SSL"
_KIND_REASONS = {"timeout": "таймаут", "unreachable": "сервер недоступен"}
# 429 — модель жива, просто занята: провалом не считаем
_STATUS_REASONS = {
    401: "ключ отклонён (401/403)",
    403: "ключ отклонён (401/403)",
    404: "модель не найдена (404)",
    429: "",
}


def _is_ssl_error(exc: Optional[BaseException]) -> bool:
    """SSL прячется в цепочке: openai.APIConnectionError ← httpx.ConnectError ← ssl.SSLError."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _status_reason(status: int) -> str:
    if status in _STATUS_REASONS:
        return _STATUS_REASONS[status]
    if status >= 500:
        return "ошибка бэкенда (5xx)"
    return f"ошибка запроса (HTTP {status})"


def smoke_failure_reason(exc: BaseException) -> str:
    """Причина для health по исключению пробного запроса; "" — модель отвечает."""
    if _is_ssl_error(exc):
        return REASON_SSL
    kind = transient_llm_error_kind(exc)
    if kind in _KIND_REASONS:
        return _KIND_REASONS[kind]
    # status_code есть и у openai.APIStatusError, и у gigachat.exceptions.ResponseError
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return _status_reason(status)
    return f"ошибка запроса ({type(exc).__name__})"


def run_smoke(model) -> str:
    """Минимальный запрос к модели реестра (LLMModelConfig); "" — успех, иначе причина."""
    try:
        llm = create_chat_llm(
            provider=model.provider,
            model_name=model.model_name,
            base_url=model.base_url,
            api_key=model.api_key,
            max_tokens=1,
            timeout=SMOKE_TIMEOUT,
            max_retries=0,
            **model.extra,
        )
        llm.invoke(SMOKE_PROMPT)
    except Exception as exc:  # провал smoke — это результат проверки, а не сбой probe
        reason = smoke_failure_reason(exc)
        logger.info(f"Model smoke {model.id}: {reason or 'ok'} ({type(exc).__name__})")
        return reason
    return ""
