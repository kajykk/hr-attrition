"""结构化日志配置（P2-11：支持 JSON 输出 + request_id 注入）.

- LOG_FORMAT=text（默认）：人类可读格式
- LOG_FORMAT=json：生产推荐，单行 JSON 便于日志采集（request_id 随 record 输出）
"""
import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.core.request_id import RequestIdFilter

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOGGING_FIELDS = ("request_id",)


class JsonFormatter(logging.Formatter):
    """单行 JSON 格式化器（含 request_id 等附加字段）."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _LOGGING_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    """初始化日志配置（JSON 或文本格式，均带 request_id filter）."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(level)
    # 第三方库降噪
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger."""
    return logging.getLogger(name)
