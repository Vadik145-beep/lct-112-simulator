"""Uniform error responses: {"error": {"code": "...", "message": "текст по-русски"}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger

log = get_logger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def _payload(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


_DEFAULT_MESSAGES = {
    401: "Требуется вход в систему.",
    403: "Нет доступа.",
    404: "Не найдено.",
    405: "Метод не поддерживается.",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content=_payload(exc.code, exc.message), headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        message = detail or _DEFAULT_MESSAGES.get(exc.status_code, "Ошибка запроса.")
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code, content=_payload(code, message), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(
            ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path"))
            for e in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content=_payload("validation_error", f"Проверьте поля: {fields or 'запрос'}."),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "Внутренняя ошибка сервера. Попробуйте ещё раз."),
        )
