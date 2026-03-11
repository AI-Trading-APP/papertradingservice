"""
Business-logic tests for PaperTradingService.

These verify slippage, position accounting, commission,
and order-history recording at the HTTP layer.
"""

import json
import pytest


# ------------------------------------------------------------------
# Slippage
# ------------------------------------------------------------------

def test_market_buy_slippage_adds_0_1_percent(client, seeded_account):
    """Market buy fills at price * 1.001."""
    resp = client.post("/api/paper/order", json={
        "ticker": "MSFT", "type": "market", "side": "buy", "quantity": 1,
    })
    body = resp.json()
    expected = 415.0 * 1.001  # 415.415
    assert body["filledPrice"] == pytest.approx(expected)


def test_market_sell_slippage_subtracts_0_1_percent(client, account_with_position):
    """Market sell fills at price * 0.999."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "sell", "quantity": 1,
    })
    body = resp.json()
    expected = 230.0 * 0.999  # 229.77
    assert body["filledPrice"] == pytest.approx(expected)


def test_slippage_calculation_correctness():
    """Unit-test apply_slippage directly."""
    from papertradingservice.main import apply_slippage

    assert apply_slippage(100.0, "buy") == pytest.approx(100.10)
    assert apply_slippage(100.0, "sell") == pytest.approx(99.90)
    assert apply_slippage(0.0, "buy") == pytest.approx(0.0)


# ------------------------------------------------------------------
# Limit orders
# ------------------------------------------------------------------

def test_limit_buy_executes_at_limit_price(client, seeded_account):
    """Limit buy at 240 (>= market 230) fills at the limit price itself."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "limit", "side": "buy",
        "quantity": 1, "limitPrice": 240.0,
    })
    assert resp.json()["filledPrice"] == 240.0


def test_limit_sell_executes_at_limit_price(client, account_with_position):
    """Limit sell at 220 (<= market 230) fills at the limit price."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "limit", "side": "sell",
        "quantity": 1, "limitPrice": 220.0,
    })
    assert resp.json()["filledPrice"] == 220.0


# ------------------------------------------------------------------
# Position accounting
# ------------------------------------------------------------------

def test_position_average_cost_multi_buy(client, seeded_account):
    """Two buys at different prices -> weighted-average cost basis."""
    # Buy 5 AAPL at ~230.23 (market + slippage)
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "buy", "quantity": 5,
    })
    # Buy 5 AAPL via limit at 235
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "limit", "side": "buy",
        "quantity": 5, "limitPrice": 235.0,
    })

    acct = client.get("/api/paper/account").json()
    pos = next(p for p in acct["positions"] if p["ticker"] == "AAPL")
    assert pos["quantity"] == 10

    expected_avg = (5 * 230.0 * 1.001 + 5 * 235.0) / 10
    assert pos["avgCostBasis"] == pytest.approx(expected_avg)


def test_position_removed_when_fully_sold(client, account_with_position):
    """Selling all shares removes the position from the list."""
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "sell", "quantity": 10,
    })
    acct = client.get("/api/paper/account").json()
    assert len(acct["positions"]) == 0


# ------------------------------------------------------------------
# Commission
# ------------------------------------------------------------------

def test_commission_is_zero(client, seeded_account):
    """COMMISSION_PER_TRADE is $0, so cost = qty * price exactly."""
    resp = client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "limit", "side": "buy",
        "quantity": 10, "limitPrice": 230.0,
    })
    assert resp.json()["status"] == "filled"

    acct = client.get("/api/paper/account").json()
    # cash should drop by exactly 10 * 230 = 2300
    assert acct["cash"] == pytest.approx(100_000.0 - 10 * 230.0)


# ------------------------------------------------------------------
# Starting capital
# ------------------------------------------------------------------

def test_account_starting_capital_100k(client):
    acct = client.get("/api/paper/account").json()
    assert acct["cash"] == 100_000.0


# ------------------------------------------------------------------
# Cash changes
# ------------------------------------------------------------------

def test_cash_reduces_after_buy(client, seeded_account):
    client.post("/api/paper/order", json={
        "ticker": "GOOGL", "type": "market", "side": "buy", "quantity": 10,
    })
    acct = client.get("/api/paper/account").json()
    fill_price = 175.0 * 1.001
    expected_cash = 100_000.0 - (10 * fill_price)
    assert acct["cash"] == pytest.approx(expected_cash)


def test_cash_increases_after_sell(client, account_with_position):
    before_cash = 97_750.0
    client.post("/api/paper/order", json={
        "ticker": "AAPL", "type": "market", "side": "sell", "quantity": 5,
    })
    acct = client.get("/api/paper/account").json()
    proceeds = 5 * 230.0 * 0.999  # sell slippage
    assert acct["cash"] == pytest.approx(before_cash + proceeds)


# ------------------------------------------------------------------
# Order history
# ------------------------------------------------------------------

def test_order_history_records_execution_details(client, seeded_account):
    client.post("/api/paper/order", json={
        "ticker": "MSFT", "type": "market", "side": "buy", "quantity": 3,
    })
    orders = client.get("/api/paper/orders").json()["orders"]
    last = orders[-1]
    assert last["ticker"] == "MSFT"
    assert last["side"] == "buy"
    assert last["quantity"] == 3
    assert last["status"] == "filled"
    assert last["filledPrice"] == pytest.approx(415.0 * 1.001)
    assert "timestamp" in last
