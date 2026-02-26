"""
In-memory write-through price cache with TTL-based expiry.

Write-through pattern:
  1. Caller checks cache via get()
  2. If miss/stale, caller fetches from yfinance
  3. Caller writes result via set()
  4. On yfinance failure, get_stale() returns last known value

Thread-safe via threading.Lock. No external dependencies.
"""

import time
import threading
from typing import Optional, Any, Dict, Tuple


class PriceCache:
    def __init__(self):
        self._store: Dict[str, Tuple[Any, float, float]] = {}  # key → (value, timestamp, ttl)
        self._lock = threading.Lock()

    def get(self, key: str) -> Tuple[Optional[Any], bool]:
        """Return (value, is_fresh). value is None if key never cached."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            value, ts, ttl = entry
            return value, (time.time() - ts) < ttl

    def get_stale(self, key: str) -> Optional[Any]:
        """Return cached value regardless of age. None if never cached."""
        with self._lock:
            entry = self._store.get(key)
            return entry[0] if entry else None

    def set(self, key: str, value: Any, ttl: float) -> None:
        """Write value with TTL in seconds."""
        with self._lock:
            self._store[key] = (value, time.time(), ttl)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            now = time.time()
            total = len(self._store)
            fresh = sum(1 for _, (_, ts, ttl) in self._store.items() if (now - ts) < ttl)
            return {"total_entries": total, "fresh_entries": fresh, "stale_entries": total - fresh}


# --- Singleton instances ---
price_cache = PriceCache()      # Stock prices (short TTL)
company_cache = PriceCache()    # Company names/metadata (long TTL)
screener_cache = PriceCache()   # Full screener result sets (medium TTL)

# --- TTL constants (seconds) ---
PRICE_TTL = 30           # Current prices
COMPANY_NAME_TTL = 86400 # 24 hours
METADATA_TTL = 3600      # 1 hour (sector, marketCap, PE)
SCREENER_RESULT_TTL = 60 # Full screener result blob
HISTORY_TTL = 60         # Historical chart data
