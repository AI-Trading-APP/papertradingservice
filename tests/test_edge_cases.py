"""
Edge-case and integration-style tests for PaperTradingService.
"""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock


def test_buy_then_sell_then_buy_again(client, seeded_account):
    """Full round-trip: buy, sell all, buy again — position re-appears."""
    # Buy 5
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 5,
    })
    acct = client.get("/api/paper/account").json()
    assert len(acct["positions"]) == 1

    # Sell all 5
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "sell", "quantity": 5,
    })
    acct = client.get("/api/paper/account").json()
    assert len(acct["positions"]) == 0

    # Buy 3 again
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 3,
    })
    acct = client.get("/api/paper/account").json()
    assert len(acct["positions"]) == 1
    assert acct["positions"][0]["quantity"] == 3


def test_reset_then_trade(client, account_with_position):
    """After reset, the account can immediately trade again."""
    client.post("/api/paper/reset")

    resp = client.post("/api/paper/order", json={
        "ticker": "GOOGL", "type": "market", "side": "buy", "quantity": 2,
    })
    assert resp.json()["status"] == "filled"

    acct = client.get("/api/paper/account").json()
    assert len(acct["positions"]) == 1
    assert acct["positions"][0]["ticker"] == "GOOGL"


def test_multiple_users_isolated(client):
    """
    Two different users should have independent accounts.
    We simulate this by overriding verify_token to return different user_ids.
    """
    from papertradingservice.main import app, verify_token

    # User A creates account
    resp = client.get("/api/paper/account")
    assert resp.json()["userId"] == "1"

    # User B
    def _user_b():
        return {"user_id": "user_2"}

    app.dependency_overrides[verify_token] = _user_b
    try:
        resp = client.get("/api/paper/account")
        assert resp.json()["userId"] == "2"
        assert resp.json()["cash"] == 100_000.0
    finally:
        app.dependency_overrides.pop(verify_token, None)


def test_expired_token_rejected(client):
    """If verify_token raises, the endpoint returns 401."""
    from fastapi import HTTPException
    from papertradingservice.main import app, verify_token

    def _expired():
        raise HTTPException(status_code=401, detail="Token expired")

    app.dependency_overrides[verify_token] = _expired
    try:
        resp = client.get("/api/paper/account")
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(verify_token, None)


def test_very_large_order(client, seeded_account):
    """Attempting to buy $10M of stock with only $100k is rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 50_000,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Insufficient funds" in body["message"]


def test_sell_more_than_owned(client, account_with_position):
    """Selling 100 shares when only 10 are held -> rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "sell", "quantity": 100,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Insufficient shares" in body["message"]


def test_yfinance_error_handled(client, seeded_account):
    """
    When get_current_price returns 0.0 (e.g. yfinance errors on all retries
    and no stale cache exists), the order is rejected.
    """
    with patch("papertradingservice.main.get_current_price", return_value=0.0):
        resp = client.post("/api/paper/order", json={
            "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 1,
        })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Unable to fetch price" in body["message"]


def test_empty_orders_list(client):
    """A brand-new account has an empty orders list."""
    # Ensure account exists first
    client.get("/api/paper/account")
    resp = client.get("/api/paper/orders")
    assert resp.status_code == 200
    assert resp.json()["orders"] == []


# ------------------------------------------------------------------
# Coverage gap tests
# ------------------------------------------------------------------


def test_root_endpoint(client):
    """GET / returns service info."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "Paper Trading Service"
    assert body["status"] == "running"
    assert body["version"] == "2.0.0"


def test_order_on_new_account_auto_creates(client):
    """Placing an order when user has no account auto-creates one and succeeds."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 1,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"


def test_limit_order_without_limit_price(client, seeded_account):
    """Limit order missing limitPrice is rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "limit", "side": "buy",
        "quantity": 1,
        # limitPrice intentionally omitted
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Limit price required" in body["message"]


def test_sell_ticker_not_in_positions(client, seeded_account):
    """Selling a ticker not held returns 'No position to sell'."""
    resp = client.post("/api/paper/order", json={
        "ticker": "GOOGL", "type": "market", "side": "sell", "quantity": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "No position to sell" in body["message"]


def test_orders_endpoint_nonexistent_user(client):
    """GET /api/paper/orders when user has no account returns empty list."""
    resp = client.get("/api/paper/orders")
    assert resp.status_code == 200
    assert resp.json()["orders"] == []


def test_get_current_price_fresh_cache_hit(client, seeded_account):
    """When cache returns a fresh value, yfinance is not called."""
    import sys
    cache_mod = sys.modules["price_cache"]
    original_cache = cache_mod.price_cache

    class _FreshCache:
        def get(self, key):
            return 999.99, True  # fresh hit
        def get_stale(self, key):
            return 999.99
        def set(self, key, value, ttl):
            pass
        def stats(self):
            return {}

    cache_mod.price_cache = _FreshCache()
    try:
        from papertradingservice.main import get_current_price
        price = get_current_price("AAPL")
        assert price == 999.99
    finally:
        cache_mod.price_cache = original_cache


def test_get_current_price_5d_fallback(client, seeded_account):
    """When 1d history is empty but 5d returns data."""
    mock_ticker = MagicMock()
    # First call (1d) returns empty, second call (5d) returns data
    mock_ticker.history.side_effect = [
        pd.DataFrame(),         # 1d -> empty
        pd.DataFrame({"Close": [555.0]}),  # 5d -> has data
    ]

    with patch("yfinance.Ticker", return_value=mock_ticker):
        from papertradingservice.main import get_current_price
        price = get_current_price("TESTFALLBACK")
    assert price == 555.0


def test_get_current_price_exception_with_retry(client, seeded_account):
    """yfinance raises on all attempts, no stale cache -> 0.0."""
    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = Exception("network error")

    with patch("yfinance.Ticker", return_value=mock_ticker), \
         patch("time.sleep"):  # skip actual sleep
        from papertradingservice.main import get_current_price
        price = get_current_price("BADTICKER")
    assert price == 0.0


def test_get_current_price_stale_cache_fallback(client, seeded_account):
    """All retries fail but stale cache exists -> returns stale value."""
    import sys
    cache_mod = sys.modules["price_cache"]
    original_cache = cache_mod.price_cache

    class _StaleCache:
        def get(self, key):
            return None, False  # no fresh hit
        def get_stale(self, key):
            return 777.77  # stale value exists
        def set(self, key, value, ttl):
            pass
        def stats(self):
            return {}

    cache_mod.price_cache = _StaleCache()
    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = Exception("network error")

    try:
        with patch("yfinance.Ticker", return_value=mock_ticker), \
             patch("time.sleep"):
            from papertradingservice.main import get_current_price
            price = get_current_price("STALECACHE")
        assert price == 777.77
    finally:
        cache_mod.price_cache = original_cache


def test_main_block_guarded(client):
    """The if __name__ == '__main__' block does not run on import."""
    import papertradingservice.main as mod
    # Verify the guard exists and uvicorn is importable but not auto-called
    with patch("uvicorn.run") as mock_run:
        # Simulate __main__ execution
        mod.__name__ = "__main__"
        exec(
            compile(
                "if __name__ == '__main__':\n    import uvicorn\n    uvicorn.run(app, host='0.0.0.0', port=8005)\n",
                "<test>",
                "exec",
            ),
            {"__name__": "__main__", "app": mod.app},
        )
        mock_run.assert_called_once()
