# -*- coding: utf-8 -*-
"""
统一日志配置（Spec-A）：dictConfig 接管 main_fastapi 的 basicConfig。

- 控制台 handler + RotatingFileHandler（logs/app.log）
- request_id：contextvar + HTTP 中间件注入，用于请求级日志关联
"""
import contextvars
import logging
import logging.config
import logging.handlers
import os
import uuid
from pathlib import Path

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class RequestIdFilter(logging.Filter):
    """为每条日志注入当前请求的 request_id。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup() -> None:
    """配置根 logger：控制台 + 轮转文件。重复调用安全（幂等）。"""
    log_level = os.getenv("LOG_LEVEL", "INFO")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s - %(levelname)s [%(request_id)s] "
                        "%(name)s - %(message)s"
                    )
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_id"],
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": str(_LOG_DIR / "app.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 3,
                    "encoding": "utf-8",
                    "formatter": "default",
                    "filters": ["request_id"],
                },
            },
            "root": {"level": log_level, "handlers": ["console", "file"]},
        }
    )


def install_request_id_middleware(app) -> None:
    """为 FastAPI app 挂 request_id 中间件（取请求头或生成，回写响应头）。"""

    @app.middleware("http")
    async def _request_id_middleware(request, call_next):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = rid
            return response
        finally:
            request_id_var.reset(token)
