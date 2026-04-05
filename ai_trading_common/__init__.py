"""
AI Trading Common - Shared observability module for all backend services.
"""

from ai_trading_common.logging_config import setup_logging, get_logger
from ai_trading_common.correlation import CorrelationMiddleware, get_correlation_headers, get_correlation_id
from ai_trading_common.errors import register_exception_handlers
from ai_trading_common.health import health_router, DependencyCheck, configure_health
from ai_trading_common.metrics import MetricsMiddleware, metrics_endpoint
from ai_trading_common.sentry_setup import setup_sentry

__all__ = [
    "setup_logging",
    "get_logger",
    "CorrelationMiddleware",
    "get_correlation_headers",
    "get_correlation_id",
    "health_router",
    "DependencyCheck",
    "configure_health",
    "MetricsMiddleware",
    "metrics_endpoint",
    "register_exception_handlers",
    "setup_sentry",
]
