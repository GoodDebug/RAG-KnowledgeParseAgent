# -*- coding: utf-8 -*-
"""
全局异常体系（Spec-A）：统一 JSON 错误契约。

- AppError：业务异常（自定义 status_code + error_code + detail）
- 三个 FastAPI 全局 handler：AppError / RequestValidationError / 未捕获 Exception
  （未捕获异常记堆栈、不向客户端泄漏）
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常：携带 HTTP 状态码与业务错误码。"""

    def __init__(self, status_code: int, error_code: str, detail: str = "") -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail or error_code
        super().__init__(self.detail)


def _error_body(error_code: str, detail: str) -> dict[str, Any]:
    return {"detail": detail, "error_code": error_code}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.error_code, exc.detail),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning(
        "参数校验失败 | path=%s | errors=%s", request.url.path, exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content=_error_body("VALIDATION_ERROR", "参数校验失败"),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "未捕获异常 | method=%s path=%s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content=_error_body("INTERNAL_ERROR", "服务器内部错误"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把全局异常 handler 挂到 FastAPI app（HTTPException 仍走默认 handler）。"""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
