"""
Structured JSON logging via structlog.

Provides consistent log format across all services:
  {"timestamp": "...", "level": "info", "service": "...", "correlation_id": "...", "event": "..."}
"""

import logging
import os
import sys

import structlog


# Keys whose values must never appear in logs
_MASKED_KEYS = {"password", "secret", "token", "authorization", "cookie", "email", "ssn"}


def _mask_pii(_logger, _method_name, event_dict):
    """Replace sensitive field values with ***MASKED***."""
    for key in list(event_dict.keys()):
        if any(masked in key.lower() for masked in _MASKED_KEYS):
            event_dict[key] = "***MASKED***"
    return event_dict


def _add_service_name(_logger, _method_name, event_dict):
    """Inject the service name bound at setup time."""
    event_dict.setdefault("service", _SERVICE_NAME)
    return event_dict


_SERVICE_NAME = "unknown"


def setup_logging(service_name: str, log_level: str | None = None):
    """Configure structured JSON logging for the calling service.

    Call this once at service startup, before any log statements.
    """
    global _SERVICE_NAME
    _SERVICE_NAME = service_name

    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    use_json = os.getenv("LOG_FORMAT", "json").lower() == "json"

    renderer = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _add_service_name,
            _mask_pii,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog so third-party libs also emit JSON
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level, logging.INFO),
    )

    # Quieten noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    """Return a structlog bound logger."""
    return structlog.get_logger(name)
