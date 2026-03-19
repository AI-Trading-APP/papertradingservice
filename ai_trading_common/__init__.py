"""
AI Trading Common — Shared observability module for all backend services.

Usage:
    from ai_trading_common import (
        setup_logging, get_logger,
        CorrelationMiddleware,
        health_router, DependencyCheck, configure_health,
        MetricsMiddleware, metrics_endpoint,
        setup_sentry,
    )
"""

from ai_trading_common.logging_config import setup_logging, get_logger
from ai_trading_common.correlation import CorrelationMiddleware
from ai_trading_common.health import health_router, DependencyCheck, configure_health
from ai_trading_common.metrics import MetricsMiddleware, metrics_endpoint
from ai_trading_common.sentry_setup import setup_sentry

__all__ = [
    "setup_logging",
    "get_logger",
    "CorrelationMiddleware",
    "health_router",
    "DependencyCheck",
    "configure_health",
    "MetricsMiddleware",
    "metrics_endpoint",
    "setup_sentry",
]
