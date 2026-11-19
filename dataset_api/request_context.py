import logging
import uuid
from contextvars import ContextVar

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_ctx.reset(token)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request_id_ctx.get("")


def install_error_handlers(app, logger: logging.Logger) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        rid = _request_id(request)
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
                content={"detail": "Internal server error"},
                headers=headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        rid = _request_id(request)
        logger.exception("Unhandled error path=%s request_id=%s", request.url.path, rid)
        headers = {"X-Request-ID": rid} if rid else {}
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers=headers,
        )
