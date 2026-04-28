"""
Shared fixtures for PaperTradingService tests.

Key design decisions:
- Uses SQLite in-memory database for test isolation (no JSON files).
- Generates real JWTs matching the production auth dependency.
- yfinance is patched globally so no network calls happen.
- price_cache is a stub that always reports a cache miss.
"""

import os
import sys
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from jose import jwt

# Ensure the parent directory is on sys.path so 'papertradingservice.main' is importable
_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Also add the service directory itself so bare imports (storage, database, etc.) resolve
_SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

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

# Force SQLite for tests (must be set before importing database)
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "paper-test-secret"
os.environ["JWT_ALGORITHM"] = "HS256"

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database as db_mod
from models import PaperAccountDB, PaperPositionDB, PaperOrderDB

# Override engine with StaticPool so all connections share the same in-memory DB
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@event.listens_for(test_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Monkey-patch the database module so StorageAdapter uses our test engine
db_mod.engine = test_engine
db_mod.SessionLocal = TestSessionLocal

# Re-import after patching
Base = db_mod.Base
engine = test_engine
SessionLocal = TestSessionLocal


# ---------------------------------------------------------------------------
# Mock price map — used by the yfinance patch
# ---------------------------------------------------------------------------
MOCK_PRICES = {
    "AAPL": 230.0,
    "MSFT": 415.0,
    "GOOGL": 175.0,
}

DEFAULT_MOCK_PRICE = 100.0  # fallback for any unknown ticker
TEST_JWT_SECRET = os.environ["JWT_SECRET_KEY"]
TEST_JWT_ALGORITHM = os.environ["JWT_ALGORITHM"]


def _make_yf_ticker(ticker_symbol: str):
    """Return a fake yf.Ticker whose .history() yields our mock prices."""
    price = MOCK_PRICES.get(ticker_symbol, DEFAULT_MOCK_PRICE)
    mock_ticker = MagicMock()
    mock_df = pd.DataFrame({"Close": [price]})
    mock_ticker.history.return_value = mock_df
    return mock_ticker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_database():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _patch_yfinance():
    """Globally patch yfinance.Ticker for every test."""
    with patch("yfinance.Ticker", side_effect=_make_yf_ticker):
        yield


@pytest.fixture()
def client(auth_headers):
    """TestClient that talks to the PaperTradingService app."""
    from papertradingservice.main import app
    with TestClient(app) as c:
        c.headers.update(auth_headers)
        yield c


@pytest.fixture()
def raw_client():
    """Bare TestClient without auth headers for authentication tests."""
    from papertradingservice.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed_client(client):
    """Authenticated client alias used by older tests."""
    return client


@pytest.fixture()
def auth_headers():
    token = jwt.encode(
        {"sub": "user@example.com", "user_id": 1},
        TEST_JWT_SECRET,
        algorithm=TEST_JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def missing_user_id_headers():
    token = jwt.encode(
        {"sub": "user@example.com"},
        TEST_JWT_SECRET,
        algorithm=TEST_JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seeded_account():
    """
    Pre-seed an account for user_id=1 with starting cash and no positions.
    """
    db = SessionLocal()
    account = PaperAccountDB(
        user_id=1,
        cash=Decimal("100000.00"),
        starting_cash=Decimal("100000.00"),
        version=1,
    )
    db.add(account)
    db.commit()
    db.close()


@pytest.fixture()
def account_with_position():
    """
    Pre-seed an account that already holds 10 shares of AAPL at $225 avg cost.
    Cash = 100000 - (10 * 225) = 97750
    """
    db = SessionLocal()
    account = PaperAccountDB(
        user_id=1,
        cash=Decimal("97750.00"),
        starting_cash=Decimal("100000.00"),
        version=1,
    )
    db.add(account)
    db.flush()

    position = PaperPositionDB(
        account_id=account.id,
        ticker="AAPL",
        quantity=Decimal("10"),
        avg_cost_basis=Decimal("225.00"),
        added_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(position)

    order = PaperOrderDB(
        account_id=account.id,
        ticker="AAPL",
        order_type="market",
        side="buy",
        quantity=Decimal("10"),
        filled_price=Decimal("225.00"),
        filled_quantity=Decimal("10"),
        status="filled",
        timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        filled_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(order)
    db.commit()
    db.close()
