import logging
import re
import uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from llm_factory import transient_llm_error_kind

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

_500_HINTS = (
    "401",
    "unauthorized",
    "timeout",
    "connection",
    "from completion",
    "429",
    "503",
    "rate limit",
    "quota",
)

# Константные details, которые задают эндпоинты сами (без текста исключения):
# по ним worker различает "таймаут" / "недоступен" / "перегружен" / "auth" в
# message_code. Общие — для /process_prompt/ и /rephrase_questions/, GATE_* — для
# /chunk_gate/.
LLM_TIMEOUT_DETAIL = "llm timeout"
LLM_UNREACHABLE_DETAIL = "llm unreachable"
LLM_BUSY_DETAIL = "llm busy"
LLM_AUTH_DETAIL = "llm auth failed"
GATE_LLM_TIMEOUT_DETAIL = "gate llm timeout"
GATE_LLM_UNREACHABLE_DETAIL = "gate llm unreachable"
GATE_LLM_BUSY_DETAIL = "gate llm busy"
GATE_LLM_AUTH_DETAIL = "gate llm auth failed"
# Нетранзиентная ошибка LLM в gate (400 context length, 404 модели и т.п.).
GATE_LLM_ERROR_DETAIL = "gate llm error"
# LLM ответил, но ответ не разобрать как вердикт gate.
GATE_PARSE_ERROR_DETAIL = "gate parse error"
# /validate_question/ (повторная проверка качества): нетранзиентная ошибка
# валидатора и непригодный для проверки вопрос.
VALIDATION_LLM_ERROR_DETAIL = "validation llm error"
VALIDATION_INPUT_ERROR_DETAIL = "validation input error"
# Выбранной модели-валидатора нет в реестре (подмены моделью по умолчанию нет).
UNKNOWN_VALIDATION_MODEL_DETAIL = "unknown_validation_model"
_SAFE_500_DETAILS = frozenset(
    {
        LLM_TIMEOUT_DETAIL,
        LLM_UNREACHABLE_DETAIL,
        LLM_BUSY_DETAIL,
        LLM_AUTH_DETAIL,
        GATE_LLM_TIMEOUT_DETAIL,
        GATE_LLM_UNREACHABLE_DETAIL,
        GATE_LLM_BUSY_DETAIL,
        GATE_LLM_AUTH_DETAIL,
        GATE_LLM_ERROR_DETAIL,
        GATE_PARSE_ERROR_DETAIL,
        VALIDATION_LLM_ERROR_DETAIL,
        VALIDATION_INPUT_ERROR_DETAIL,
    }
)

_LLM_ERROR_HTTP = {
    # kind → (status, detail для общих эндпоинтов, detail для gate)
    "timeout": (504, LLM_TIMEOUT_DETAIL, GATE_LLM_TIMEOUT_DETAIL),
    "unreachable": (502, LLM_UNREACHABLE_DETAIL, GATE_LLM_UNREACHABLE_DETAIL),
    "busy": (503, LLM_BUSY_DETAIL, GATE_LLM_BUSY_DETAIL),
    # 401 здесь — сбой доступа agent_api к LLM, а не авторизация пользователя.
    "auth": (401, LLM_AUTH_DETAIL, GATE_LLM_AUTH_DETAIL),
}


def llm_error_to_http(exc: BaseException, *, gate: bool) -> Optional[HTTPException]:
    """HTTPException с константным detail для инфраструктурной ошибки LLM.

    None — ошибка не инфраструктурная (парсинг, 400, 404 модели и т.п.);
    вызывающий сам решает, что с ней делать.
    """
    kind = transient_llm_error_kind(exc)
    if kind not in _LLM_ERROR_HTTP:
        return None
    status, detail, gate_detail = _LLM_ERROR_HTTP[kind]
    return HTTPException(status_code=status, detail=gate_detail if gate else detail)


def _sanitize_500_detail(detail: object) -> str:
    text = str(detail or "")
    if text in _SAFE_500_DETAILS:
        return text
    text = re.sub(r"(?i)(api[_-]?key|sk-|bearer)\s*[:=]?\s*\S+", "[redacted]", text)
    lowered = text.lower()
    hints = [h for h in _500_HINTS if h in lowered]
    if hints:
        return "Internal server error (" + ", ".join(hints) + ")"
    return "Internal server error"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def install_error_handlers(app, logger: logging.Logger) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        rid = request_id_ctx.get("")
        headers = {"X-Request-ID": rid} if rid else {}
        if exc.status_code >= 500:
            logger.error(
                "HTTP %s path=%s request_id=%s detail=%s",
                exc.status_code,
                request.url.path,
                rid,
                exc.detail,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": _sanitize_500_detail(exc.detail)},
                headers=headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        rid = request_id_ctx.get("")
        logger.exception("Unhandled error path=%s request_id=%s", request.url.path, rid)
        headers = {"X-Request-ID": rid} if rid else {}
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers=headers,
        )
