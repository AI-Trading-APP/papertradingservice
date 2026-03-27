"""
Circuit Breaker pattern for external API calls.

States: CLOSED (normal) -> OPEN (failing, skip calls) -> HALF_OPEN (test one call)

Usage:
    from circuit_breaker import yfinance_breaker
    result = yfinance_breaker.call(yf.Ticker("AAPL").history, period="1d", fallback=lambda: cached_data)
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, name: str = "default", failure_threshold: int = 3, reset_timeout: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.state = "CLOSED"
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def call(self, func, *args, fallback=None, **kwargs):
        with self._lock:
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = "HALF_OPEN"
                    logger.info(f"[CB:{self.name}] HALF_OPEN — testing one call")
                else:
                    logger.debug(f"[CB:{self.name}] OPEN — skipping call, using fallback")
                    return fallback() if callable(fallback) else fallback

        try:
            result = func(*args, **kwargs)
            with self._lock:
                if self.state == "HALF_OPEN":
                    logger.info(f"[CB:{self.name}] CLOSED — call succeeded")
                self.failures = 0
                self.state = "CLOSED"
            return result
        except Exception as e:
            with self._lock:
                self.failures += 1
                self.last_failure_time = time.time()
                if self.failures >= self.failure_threshold:
                    self.state = "OPEN"
                    logger.warning(f"[CB:{self.name}] OPEN — {self.failures} failures: {e}")
                else:
                    logger.debug(f"[CB:{self.name}] failure {self.failures}/{self.failure_threshold}: {e}")
            return fallback() if callable(fallback) else fallback

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self.state,
                "failures": self.failures,
                "threshold": self.failure_threshold,
            }


# Singleton instances
yfinance_breaker = CircuitBreaker(name="yfinance", failure_threshold=3, reset_timeout=60)
