"""
Correlation ID middleware — generates or propagates X-Request-ID across services.

The correlation ID is stored in structlog contextvars so every log line
emitted during a request automatically includes it.
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


logger = structlog.get_logger(__name__)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Extract or generate X-Request-ID header; bind to structlog context."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Bind correlation context for all downstream log calls
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            endpoint=f"{request.method} {request.url.path}",
        )

        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 1)

        response.headers["X-Request-ID"] = correlation_id

        # Access log
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=request.client.host if request.client else None,
        )

        return response
