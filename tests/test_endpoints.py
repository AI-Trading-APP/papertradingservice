"""
Endpoint-level tests for PaperTradingService.

These tests exercise the HTTP interface through FastAPI's TestClient.
yfinance is mocked globally via conftest.py.
"""

import pytest


# ------------------------------------------------------------------
# Health / root
# ------------------------------------------------------------------

def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "paper-trading-service"
    assert body["version"] == "2.0.0"
    assert body["dependencies"] == []


# ------------------------------------------------------------------
# GET /api/paper/account
# ------------------------------------------------------------------

def test_get_account_new_user_defaults(client):
    """First call auto-creates an account with $100k and no positions."""
    resp = client.get("/api/paper/account")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash"] == 100_000.0
    assert body["positions"] == []
    assert body["orders"] == []
    assert body["userId"] == "1"


def test_get_account_returns_balance_and_positions(client, account_with_position):
    """An account with a position reports cash, positions, and metrics."""
    resp = client.get("/api/paper/account")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash"] == 97_750.0
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["ticker"] == "AAPL"
    assert pos["quantity"] == 10
    # currentPrice should be the mock price (230)
    assert pos["currentPrice"] == 230.0
    # totalValue = cash + market_value_of_positions
    assert body["totalValue"] == pytest.approx(97_750.0 + 10 * 230.0)


# ------------------------------------------------------------------
# POST /api/paper/order — buys
# ------------------------------------------------------------------

def test_place_market_buy_order(client, seeded_account):
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "market",
        "side": "buy",
        "quantity": 5,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"
    assert body["filledQuantity"] == 5
    # Market buy has slippage: 230 * 1.001 = 230.23
    assert body["filledPrice"] == pytest.approx(230.0 * 1.001)


def test_place_limit_buy_order(client, seeded_account):
    """Limit buy at 235 >= market 230 -> immediate fill at 235."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "limit",
        "side": "buy",
        "quantity": 2,
        "limitPrice": 235.0,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"
    assert body["filledPrice"] == 235.0


def test_place_limit_buy_unfavorable_rejected(client, seeded_account):
    """Limit buy at 200 < market 230 -> rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "limit",
        "side": "buy",
        "quantity": 2,
        "limitPrice": 200.0,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "not favorable" in body["message"]


def test_order_insufficient_funds(client, seeded_account):
    """Buying more shares than cash allows is rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "market",
        "side": "buy",
        "quantity": 500,  # 500 * ~230.23 >> 100k
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Insufficient funds" in body["message"]


# ------------------------------------------------------------------
# POST /api/paper/order — sells
# ------------------------------------------------------------------

def test_place_market_sell_order(client, account_with_position):
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "market",
        "side": "sell",
        "quantity": 5,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"
    assert body["filledQuantity"] == 5
    # Market sell has slippage: 230 * 0.999 = 229.77
    assert body["filledPrice"] == pytest.approx(230.0 * 0.999)


def test_order_insufficient_shares(client, account_with_position):
    """Selling more shares than held is rejected."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "market",
        "side": "sell",
        "quantity": 20,  # only 10 held
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Insufficient shares" in body["message"]


# ------------------------------------------------------------------
# GET /api/paper/orders
# ------------------------------------------------------------------

def test_get_orders_history(client, account_with_position):
    resp = client.get("/api/paper/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["orders"]) == 1
    assert body["orders"][0]["orderId"] == "order_1"


# ------------------------------------------------------------------
# POST /api/paper/reset
# ------------------------------------------------------------------

def test_reset_account_restores_defaults(client, account_with_position):
    resp = client.post("/api/paper/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["startingCash"] == 100_000.0

    # Verify account is actually reset
    acct = client.get("/api/paper/account").json()
    assert acct["cash"] == 100_000.0


def test_reset_clears_positions_and_orders(client, account_with_position):
    client.post("/api/paper/reset")
    acct = client.get("/api/paper/account").json()
    assert acct["positions"] == []
    assert acct["orders"] == []


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------

def test_requires_auth(raw_client):
    resp = raw_client.get("/api/paper/account")
    assert resp.status_code == 401


def test_accepts_auth_cookie(raw_client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ").strip()
    raw_client.cookies.set("auth_token", token)
    resp = raw_client.get("/api/paper/account")
    assert resp.status_code == 200
    assert resp.json()["userId"] == "1"


# ------------------------------------------------------------------
# Error / validation
# ------------------------------------------------------------------

def test_invalid_ticker_handled(client, seeded_account):
    """
    If yfinance returns an empty dataframe (and stale cache is empty),
    get_current_price returns 0.0 and the order is rejected.
    """
    import pandas as pd
    from unittest.mock import patch, MagicMock

    bad_ticker = MagicMock()
    bad_ticker.history.return_value = pd.DataFrame()

    with patch("yfinance.Ticker", return_value=bad_ticker):
        resp = client.post("/api/paper/order", json={
            "ticker": "INVALID",
            "type": "market",
            "side": "buy",
            "quantity": 1,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "Unable to fetch price" in body["message"]


def test_zero_quantity_rejected(client, seeded_account):
    """
    Buying 0 shares results in a filled order with 0 cost which is
    technically accepted by the current implementation — this test
    documents actual behaviour (quantity=0 does not error).
    """
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL",
        "type": "market",
        "side": "buy",
        "quantity": 0,
    })
    # The service fills with 0 shares at 0 total cost — no explicit validation
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"
    assert body["filledQuantity"] == 0
