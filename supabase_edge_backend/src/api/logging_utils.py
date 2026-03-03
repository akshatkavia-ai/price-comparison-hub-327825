import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

# Per-request correlation id (set by middleware)
_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    """Formats logs as one-line JSON suitable for ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _request_id_ctx.get(),
        }

        # Attach any optional structured extras (best-effort)
        for key in ("event", "method", "path", "status_code", "duration_ms", "client_ip", "job_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


# PUBLIC_INTERFACE
def set_request_id(request_id: Optional[str]) -> None:
    """Set correlation id for the current context."""
    _request_id_ctx.set(request_id)


# PUBLIC_INTERFACE
def get_request_id() -> str:
    """Get correlation id for the current context, generating one if missing."""
    rid = _request_id_ctx.get()
    if rid:
        return rid
    rid = str(uuid.uuid4())
    _request_id_ctx.set(rid)
    return rid


# PUBLIC_INTERFACE
def init_logging(level: str = "INFO") -> None:
    """Initialize application-wide structured logging."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    # Replace handlers to avoid duplicate logs under uvicorn reload
    root.handlers = [handler]
    root.propagate = False
