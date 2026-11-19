"""Structured, privacy-aware logging with request-id correlation."""
from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_DIGITS = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_SECRET_KEY = re.compile(r"(key|secret|token|password|authorization|cookie)", re.I)


def redact(text: str) -> str:
    return _LONG_DIGITS.sub("[phone]", _EMAIL.sub("[email]", text))


def _redact_value(key: str, value: Any) -> Any:
    if _SECRET_KEY.search(key):
        return "***"
        
    if isinstance(value, str):
        return redact(value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = {k: _redact_value(k, v) for k, v in fields.items()}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {}) or {}
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        base = (
            f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:<8} "
            f"[{request_id_var.get()}] {record.name}: {record.getMessage()} {extra}"
        ).rstrip()
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else TextFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "faiss"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})
