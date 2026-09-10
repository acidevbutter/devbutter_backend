import json
import logging.config
import time
from time import struct_time

# Attributes every stdlib LogRecord already carries -- anything else found on
# a record (i.e. passed via `logger.info(..., extra={...})`) is treated as a
# structured field and merged into the JSON payload.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """Renders one JSON object per log line.

    Every event carries timestamp/level/logger/message plus whatever
    structured fields the caller attached via `extra=`. Callers that want a
    named `event` (e.g. `app.request`'s per-request completion log) just pass
    `extra={"event": "http_request_completed", ...}` and it shows up as a top
    level key alongside the rest.
    """

    @staticmethod
    def _utc_converter(seconds: float | None = None) -> struct_time:
        return time.gmtime(seconds)

    converter = _utc_converter  # UTC timestamps in logs, matching CloudWatch.

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "json"},
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
                # uvicorn's own access log would otherwise print a second,
                # unstructured line for every request on top of the single
                # structured completion event RequestContextMiddleware
                # already emits (app.settings.middleware) -- quiet it instead
                # of double-logging.
                "uvicorn.access": {"level": "WARNING", "propagate": False},
                "botocore": {"level": "WARNING", "propagate": True},
                "httpx": {"level": "WARNING", "propagate": True},
            },
        }
    )
