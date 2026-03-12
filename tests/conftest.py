"""
Shared fixtures for PaperTradingService tests.

- STORAGE_MODE defaults to json_only for all existing tests (no DB required).
- Patches yfinance so no real network calls are made.
- storage.PAPER_ACCOUNTS_FILE is patched to write into a tmp directory per test.
- Provides authenticated TestClient and helper functions.
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Ensure the parent directory is on sys.path so 'papertradingservice.main' is importable
_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Also add the service directory itself so bare imports (storage, database, etc.) resolve
_SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

# Force json_only mode for tests (no DB required)
os.environ.setdefault("STORAGE_MODE", "json_only")

# ---------------------------------------------------------------------------
# Inject a mock 'price_cache' module into sys.modules BEFORE importing
# papertradingservice.main.  The real price_cache is a sibling module that
# isn't on sys.path during testing, so the lazy import inside
# get_current_price (``from price_cache import ...``) would fail.
# ---------------------------------------------------------------------------
import types as _types

_mock_price_cache_module = _types.ModuleType("price_cache")

class _FakePriceCache:
    """Minimal stub that always reports a cache miss."""
    def get(self, key):
        return None, False
    def get_stale(self, key):
        return None
    def set(self, key, value, ttl):
        pass
    def stats(self):
        return {"hits": 0, "misses": 0, "size": 0}
    def clear(self):
        pass

_mock_price_cache_module.price_cache = _FakePriceCache()
_mock_price_cache_module.PRICE_TTL = 30
sys.modules.setdefault("price_cache", _mock_price_cache_module)

# ---------------------------------------------------------------------------
# Database module will be imported by storage.py but won't connect in
# json_only mode. We set DATABASE_URL to sqlite to avoid needing psycopg2.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite:///test_paper_trading.db")

# ---------------------------------------------------------------------------
# Mock price map — used by the yfinance patch
# ---------------------------------------------------------------------------
MOCK_PRICES = {
    "AAPL": 230.0,
    "MSFT": 415.0,
    "GOOGL": 175.0,
}

DEFAULT_MOCK_PRICE = 100.0  # fallback for any unknown ticker


def _make_yf_ticker(ticker_symbol: str):
    """Return a fake yf.Ticker whose .history() yields our mock prices."""
    import pandas as pd

    price = MOCK_PRICES.get(ticker_symbol, DEFAULT_MOCK_PRICE)

    mock_ticker = MagicMock()
    mock_df = pd.DataFrame({"Close": [price]})
    mock_ticker.history.return_value = mock_df
    return mock_ticker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_yfinance():
    """Globally patch yfinance.Ticker for every test."""
    with patch("yfinance.Ticker", side_effect=_make_yf_ticker):
        yield


@pytest.fixture(autouse=True)
def tmp_accounts_file(tmp_path, monkeypatch):
    """
    Redirect PAPER_ACCOUNTS_FILE to a temp directory so tests are isolated
    and never touch real data. Patches both storage and main modules.
    """
    tmp_file = str(tmp_path / "paper_accounts.json")
    import papertradingservice.main as main_mod
    monkeypatch.setattr(main_mod, "PAPER_ACCOUNTS_FILE", tmp_file)
    # Patch the storage module (bare import used by main.py)
    import storage as storage_mod
    monkeypatch.setattr(storage_mod, "PAPER_ACCOUNTS_FILE", tmp_file)
    return tmp_file


@pytest.fixture()
def client():
    """TestClient that talks to the PaperTradingService app."""
    from papertradingservice.main import app
    return TestClient(app)


@pytest.fixture()
def authed_client(client):
    """
    The service's verify_token always returns {"user_id": "user_1"} so no
    special headers are needed, but this fixture makes intent explicit.
    """
    return client


@pytest.fixture()
def seeded_account(tmp_accounts_file):
    """
    Pre-seed an account for user_1 with starting cash and no positions,
    then return the path so tests can inspect the file directly.
    """
    accounts = {
        "user_1": {
            "userId": "user_1",
            "cash": 100000.0,
            "positions": [],
            "orders": [],
            "createdAt": "2025-01-01T00:00:00",
        }
    }
    with open(tmp_accounts_file, "w") as f:
        json.dump(accounts, f)
    return tmp_accounts_file


@pytest.fixture()
def account_with_position(tmp_accounts_file):
    """
    Pre-seed an account that already holds 10 shares of AAPL at $225 avg cost.
    """
    accounts = {
        "user_1": {
            "userId": "user_1",
            "cash": 97750.0,
            "positions": [
                {"ticker": "AAPL", "quantity": 10, "avgCostBasis": 225.0}
            ],
            "orders": [
                {
                    "orderId": "order_1",
                    "ticker": "AAPL",
                    "type": "market",
                    "side": "buy",
                    "quantity": 10,
                    "filledPrice": 225.0,
                    "filledQuantity": 10,
                    "status": "filled",
                    "timestamp": "2025-01-15T10:00:00",
                }
            ],
            "createdAt": "2025-01-01T00:00:00",
        }
    }
    with open(tmp_accounts_file, "w") as f:
        json.dump(accounts, f)
    return tmp_accounts_file
