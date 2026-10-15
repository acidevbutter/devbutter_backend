import logging.config

from app.settings.config import settings
from app.settings.constants import SERVICE_NAME
from app.settings.terminal_logging import ServiceContextFilter, TerminalFormatter


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "terminal": {"()": TerminalFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "terminal",
                    "filters": ["service_context"],
                },
            },
            "filters": {
                "service_context": {
                    "()": ServiceContextFilter,
                    "service_name": SERVICE_NAME,
                    "environment": settings.environment,
                }
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
