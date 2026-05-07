"""
Paper Trading Service
Simulated trading environment with realistic execution and fees
"""

import os
import logging
import importlib
from typing import Dict, List, Optional

import yfinance as yf
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel
import threading
import time
from pathlib import Path
import sys

import yfinance as yf

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
for import_path in (CURRENT_DIR, PROJECT_ROOT):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)
from datetime import datetime, timezone

from ai_trading_common import (
    CorrelationMiddleware,
    DependencyCheck,
    MetricsMiddleware,
    configure_health,
    get_logger,
    metrics_endpoint,
    register_exception_handlers,
    setup_logging,
    setup_sentry,
)

try:
    from database import SessionLocal, check_db_connection, ensure_cached_prices_table
except ImportError:  # pragma: no cover - supports package imports
    from .database import SessionLocal, check_db_connection, ensure_cached_prices_table

try:
    from health_checks import check_postgresql
except ImportError:  # pragma: no cover - supports package imports
    from .health_checks import check_postgresql

try:
    from storage import StorageAdapter
except ImportError:  # pragma: no cover - supports package imports
    from .storage import StorageAdapter

try:
    from circuit_breaker import yfinance_breaker
except ImportError:  # pragma: no cover - supports package imports
    from .circuit_breaker import yfinance_breaker

try:
    from db_cache import load_cached_prices_from_db, get_price_from_db, save_prices_batch_to_db
except ImportError:  # pragma: no cover - supports package imports
    from .db_cache import load_cached_prices_from_db, get_price_from_db, save_prices_batch_to_db

try:
    from price_cache import PRICE_TTL, price_cache
except ImportError:  # pragma: no cover - supports package imports
    from .price_cache import PRICE_TTL, price_cache

APP_VERSION = "2.0.0"

setup_logging("papertradingservice")
logger = get_logger(__name__)
setup_sentry(service_name="papertradingservice", version=APP_VERSION)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
security = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Paper Trading Service",
    description="Simulated trading environment",
    version=APP_VERSION
)

app.add_middleware(MetricsMiddleware, service_name="paper-trading-service")
app.add_middleware(CorrelationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

configure_health(app, "paper-trading-service", APP_VERSION)
DependencyCheck.clear()
DependencyCheck.register("postgresql", check_postgresql)

app.add_route("/metrics", metrics_endpoint, methods=["GET"])
register_exception_handlers(app)

# Storage adapter
storage = StorageAdapter()

# Trading configuration
SLIPPAGE_PERCENT = 0.1  # 0.1% slippage
COMMISSION_PER_TRADE = 0.0  # $0 commission (like Robinhood)
STARTING_CASH = 100000.0  # $100,000 starting capital

# Limit order fill checker shutdown flag
_fill_checker_stop = threading.Event()

# Models
class Order(BaseModel):
    ticker: str
    type: str  # "market" or "limit"
    side: str  # "buy" or "sell"
    quantity: float
    limitPrice: Optional[float] = None

class OrderResponse(BaseModel):
    orderId: str
    status: str  # "filled", "pending", "rejected"
    filledPrice: Optional[float] = None
    filledQuantity: Optional[float] = None
    message: str

class Position(BaseModel):
    ticker: str
    quantity: float
    avgCostBasis: float
    currentPrice: Optional[float] = None
    marketValue: Optional[float] = None
    unrealizedPL: Optional[float] = None
    unrealizedPLPercent: Optional[float] = None

class PaperAccount(BaseModel):
    userId: str
    cash: float
    positions: List[Position]
    orders: List[Dict]
    totalValue: Optional[float] = None
    totalPL: Optional[float] = None
    totalPLPercent: Optional[float] = None
    createdAt: Optional[str] = None


def _get_price_cache():
    """
    Resolve the active cache object at call time so tests that swap
    `sys.modules["price_cache"].price_cache` are honored.
    """
    try:
        return importlib.import_module("price_cache").price_cache
    except Exception:
        return price_cache


def get_current_price(ticker: str) -> float:
    """Get current stock price via 3-layer cache: Memory -> DB -> yfinance."""
    import time as _time

    cache_key = f"price:{ticker}"
    current_price_cache = _get_price_cache()

    # Layer 1: Memory cache
    cached, is_fresh = current_price_cache.get(cache_key)
    if cached is not None and is_fresh:
        return cached

    # Layer 2: DB cache
    db_price = get_price_from_db(ticker)
    if db_price is not None and db_price > 0:
        current_price_cache.set(cache_key, db_price, PRICE_TTL)
        return db_price

    # Layer 3: yfinance with circuit breaker + retry
    max_retries = 3
    for attempt in range(max_retries):
        def _fetch_1d():
            stock = yf.Ticker(ticker)
            data = stock.history(period="1d")
            if not data.empty:
                return float(data['Close'].iloc[-1])
            return None

        price = yfinance_breaker.call(_fetch_1d, fallback=lambda: None)
        if price is not None:
            current_price_cache.set(cache_key, price, PRICE_TTL)
            save_prices_batch_to_db([{"ticker": ticker, "price": price}])
            return price

        # Try 5d fallback
        def _fetch_5d():
            stock = yf.Ticker(ticker)
            data = stock.history(period="5d")
            if not data.empty:
                return float(data['Close'].iloc[-1])
            return None

        price = yfinance_breaker.call(_fetch_5d, fallback=lambda: None)
        if price is not None:
            current_price_cache.set(cache_key, price, PRICE_TTL)
            save_prices_batch_to_db([{"ticker": ticker, "price": price}])
            return price

        if attempt < max_retries - 1:
            _time.sleep(1)

    # All retries failed — serve stale cache
    stale = current_price_cache.get_stale(cache_key)
    if stale is not None:
        logger.info(f"Serving stale cached price for {ticker}: {stale}")
        return stale
    return 0.0

def apply_slippage(price: float, side: str) -> float:
    """Apply realistic slippage to order execution"""
    slippage = price * (SLIPPAGE_PERCENT / 100)
    if side == "buy":
        return price + slippage
    else:
        return price - slippage

def get_batch_prices(tickers: list) -> dict:
    """Fetch prices for multiple tickers via 3-layer cache: Memory -> DB -> yfinance."""
    results = {}
    to_fetch_db = []
    current_price_cache = _get_price_cache()

    # Layer 1: Memory cache
    for ticker in tickers:
        cache_key = f"price:{ticker}"
        cached, is_fresh = current_price_cache.get(cache_key)
        if cached is not None and is_fresh:
            results[ticker] = cached
        else:
            to_fetch_db.append(ticker)

    if not to_fetch_db:
        return results

    # Layer 2: DB cache
    to_fetch_yf = []
    for ticker in to_fetch_db:
        db_price = get_price_from_db(ticker)
        if db_price is not None and db_price > 0:
            current_price_cache.set(f"price:{ticker}", db_price, PRICE_TTL)
            results[ticker] = db_price
        else:
            to_fetch_yf.append(ticker)

    if not to_fetch_yf:
        return results

    # Layer 3: yfinance with circuit breaker
    def _batch_download():
        return yf.download(to_fetch_yf, period="5d", threads=True, progress=False)

    df = yfinance_breaker.call(_batch_download, fallback=lambda: None)
    db_entries = []

    if df is not None:
        for ticker in to_fetch_yf:
            try:
                closes = df["Close"].dropna() if len(to_fetch_yf) == 1 else df[ticker]["Close"].dropna()
                if not closes.empty:
                    price = float(closes.iloc[-1])
                    current_price_cache.set(f"price:{ticker}", price, PRICE_TTL)
                    results[ticker] = price
                    db_entries.append({"ticker": ticker, "price": price})
                else:
                    results[ticker] = current_price_cache.get_stale(f"price:{ticker}") or 0.0
            except Exception:
                results[ticker] = current_price_cache.get_stale(f"price:{ticker}") or 0.0
    else:
        for ticker in to_fetch_yf:
            results[ticker] = current_price_cache.get_stale(f"price:{ticker}") or 0.0

    if db_entries:
        save_prices_batch_to_db(db_entries)

    return results


def calculate_account_metrics(account: Dict) -> Dict:
    """Calculate account metrics with batch-fetched current prices."""
    total_market_value = account['cash']
    initial_value = STARTING_CASH

    # Batch fetch all prices at once
    tickers = [p['ticker'] for p in account['positions']]
    prices = get_batch_prices(tickers) if tickers else {}

    for position in account['positions']:
        current_price = prices.get(position['ticker'], 0.0)
        position['currentPrice'] = current_price

        market_value = position['quantity'] * current_price
        position['marketValue'] = market_value

        cost_basis = position['quantity'] * position['avgCostBasis']

        unrealized_pl = market_value - cost_basis
        position['unrealizedPL'] = unrealized_pl
        position['unrealizedPLPercent'] = (unrealized_pl / cost_basis * 100) if cost_basis > 0 else 0

        total_market_value += market_value

    account['totalValue'] = total_market_value
    account['totalPL'] = total_market_value - initial_value
    account['totalPLPercent'] = (account['totalPL'] / initial_value * 100) if initial_value > 0 else 0

    return account


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Verify user JWT from cookie or Bearer header."""
    cookie_token = request.cookies.get("auth_token")
    if cookie_token:
        return _decode_token(cookie_token)

    if credentials and credentials.credentials:
        return _decode_token(credentials.credentials)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


# --- Startup: warm memory cache from DB ---
@app.on_event("startup")
def startup_warm_cache():
    """Load cached prices from DB into memory cache on startup."""
    try:
        ensure_cached_prices_table()
        current_price_cache = _get_price_cache()
        rows = load_cached_prices_from_db()
        count = 0
        for row in rows:
            ticker = row.get("ticker")
            price = row.get("price", 0.0)
            if ticker and price > 0:
                current_price_cache.set(f"price:{ticker}", price, PRICE_TTL)
                count += 1
        logger.info(f"Startup cache warm: loaded {count} prices from DB into memory")
    except Exception as e:
        logger.error(f"Startup cache warm failed: {e}")


# --- Startup: limit order fill checker daemon ---
@app.on_event("startup")
def start_fill_checker():
    """Start background daemon thread to check pending limit orders every 30s."""
    _fill_checker_stop.clear()
    t = threading.Thread(target=_limit_order_fill_loop, daemon=True, name="fill-checker")
    t.start()
    logger.info("Limit order fill checker daemon started")


@app.on_event("shutdown")
def stop_fill_checker():
    """Signal the fill checker daemon to stop."""
    _fill_checker_stop.set()
    logger.info("Limit order fill checker daemon stopped")


def _limit_order_fill_loop():
    """Background loop: check pending limit orders every 30 seconds."""
    from sqlalchemy import text as sa_text
    from database import engine

    while not _fill_checker_stop.is_set():
        try:
            _check_and_fill_pending_orders(engine, sa_text)
        except Exception as e:
            logger.error(f"Fill checker error: {e}")
        _fill_checker_stop.wait(30)


def _check_and_fill_pending_orders(engine, sa_text):
    """Query pending orders and fill those whose limit price is now favorable."""
    from decimal import Decimal

    with engine.begin() as conn:
        rows = conn.execute(sa_text(
            "SELECT o.id, o.account_id, o.ticker, o.side, o.quantity, o.limit_price "
            "FROM paper_orders o WHERE o.status = 'pending'"
        )).fetchall()

        if not rows:
            return

        # Collect tickers and fetch current prices
        tickers = list({r[2] for r in rows})
        prices = get_batch_prices(tickers)

        for row in rows:
            order_id, account_id, ticker, side, quantity, limit_price = row
            current_price = prices.get(ticker, 0.0)
            if current_price <= 0:
                continue

            limit_px = float(limit_price)
            should_fill = False
            if side == "buy" and current_price <= limit_px:
                should_fill = True
            elif side == "sell" and current_price >= limit_px:
                should_fill = True

            if not should_fill:
                continue

            qty = Decimal(str(float(quantity)))
            px = Decimal(str(limit_px))
            now = datetime.now(timezone.utc)

            if side == "buy":
                total_cost = qty * px
                # Check sufficient cash
                acct = conn.execute(sa_text(
                    "SELECT cash FROM paper_accounts WHERE id = :aid"
                ), {"aid": account_id}).fetchone()
                if not acct or Decimal(str(float(acct[0]))) < total_cost:
                    continue

                # Deduct cash
                conn.execute(sa_text(
                    "UPDATE paper_accounts SET cash = cash - :cost, version = version + 1 WHERE id = :aid"
                ), {"cost": float(total_cost), "aid": account_id})

                # Upsert position
                pos = conn.execute(sa_text(
                    "SELECT id, quantity, avg_cost_basis FROM paper_positions "
                    "WHERE account_id = :aid AND ticker = :t"
                ), {"aid": account_id, "t": ticker}).fetchone()
                if pos:
                    old_qty = Decimal(str(float(pos[1])))
                    old_basis = Decimal(str(float(pos[2])))
                    new_qty = old_qty + qty
                    new_basis = ((old_qty * old_basis) + (qty * px)) / new_qty
                    conn.execute(sa_text(
                        "UPDATE paper_positions SET quantity = :q, avg_cost_basis = :b WHERE id = :pid"
                    ), {"q": float(new_qty), "b": float(new_basis), "pid": pos[0]})
                else:
                    conn.execute(sa_text(
                        "INSERT INTO paper_positions (account_id, ticker, quantity, avg_cost_basis) "
                        "VALUES (:aid, :t, :q, :b)"
                    ), {"aid": account_id, "t": ticker, "q": float(qty), "b": float(px)})

            else:  # sell
                # Check sufficient shares
                pos = conn.execute(sa_text(
                    "SELECT id, quantity FROM paper_positions "
                    "WHERE account_id = :aid AND ticker = :t"
                ), {"aid": account_id, "t": ticker}).fetchone()
                if not pos or Decimal(str(float(pos[1]))) < qty:
                    continue

                proceeds = qty * px
                conn.execute(sa_text(
                    "UPDATE paper_accounts SET cash = cash + :p, version = version + 1 WHERE id = :aid"
                ), {"p": float(proceeds), "aid": account_id})

                new_qty = Decimal(str(float(pos[1]))) - qty
                if new_qty == 0:
                    conn.execute(sa_text(
                        "DELETE FROM paper_positions WHERE id = :pid"
                    ), {"pid": pos[0]})
                else:
                    conn.execute(sa_text(
                        "UPDATE paper_positions SET quantity = :q WHERE id = :pid"
                    ), {"q": float(new_qty), "pid": pos[0]})

            # Mark order as filled
            conn.execute(sa_text(
                "UPDATE paper_orders SET status = 'filled', filled_price = :px, "
                "filled_quantity = :qty, filled_at = :now WHERE id = :oid"
            ), {"px": float(px), "qty": float(qty), "now": now, "oid": order_id})

            logger.info(f"Filled pending order {order_id}: {side} {float(qty)} {ticker} @ ${float(px):.2f}")


# Routes
@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    return await metrics_endpoint(request)

@app.get("/")
def read_root():
    return {
        "service": "Paper Trading Service",
        "status": "running",
        "version": APP_VERSION,
        "storage_mode": "pg_only",
    }

# /health, /health/ready, /health/live provided by ai_trading_common health_router

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "paper-trading-service",
        "version": APP_VERSION,
        "dependencies": [],
    }

app.router.routes = [
    route for route in app.router.routes
    if getattr(route, "path", None) != "/health" or getattr(route, "endpoint", None) == health
]

@app.get("/api/paper/account", response_model=PaperAccount)
def get_account(token_data: dict = Depends(verify_token)):
    """Get paper trading account"""
    user_id = str(token_data.get("user_id"))
    account = storage.get_account(user_id)
    account = calculate_account_metrics(account)
    return account

@app.post("/api/paper/order", response_model=OrderResponse)
def place_order(order: Order, token_data: dict = Depends(verify_token)):
    """Place a paper trading order"""
    user_id = str(token_data.get("user_id"))

    if order.quantity <= 0:
        return OrderResponse(
            orderId="",
            status="rejected",
            message="Quantity must be greater than zero",
        )

    # Ensure account exists
    storage.get_account(user_id)

    # Get current market price
    market_price = get_current_price(order.ticker)
    if market_price == 0:
        return OrderResponse(
            orderId="",
            status="rejected",
            message=f"Unable to fetch price for {order.ticker}"
        )

    # Determine execution price
    if order.type == "market":
        execution_price = apply_slippage(market_price, order.side)
    else:  # limit order
        if order.limitPrice is None:
            return OrderResponse(
                orderId="",
                status="rejected",
                message="Limit price required for limit orders"
            )
        if order.side == "buy" and order.limitPrice >= market_price:
            execution_price = order.limitPrice
        elif order.side == "sell" and order.limitPrice <= market_price:
            execution_price = order.limitPrice
        else:
            # Limit price not immediately favorable — create pending order
            try:
                result = storage.create_pending_order(
                    user_id=user_id,
                    ticker=order.ticker,
                    side=order.side,
                    quantity=order.quantity,
                    limit_price=order.limitPrice,
                )
                return OrderResponse(**result)
            except Exception as e:
                return OrderResponse(
                    orderId="",
                    status="rejected",
                    message=f"Failed to create pending order: {e}",
                )

    # Execute order via storage adapter
    try:
        result = storage.place_order(
            user_id=user_id,
            ticker=order.ticker,
            order_type=order.type,
            side=order.side,
            quantity=order.quantity,
            execution_price=execution_price,
            limit_price=order.limitPrice,
        )
        return OrderResponse(**result)
    except ValueError as e:
        return OrderResponse(
            orderId="",
            status="rejected",
            message=str(e),
        )

@app.post("/api/paper/reset")
def reset_account(token_data: dict = Depends(verify_token)):
    """Reset paper trading account to starting state"""
    user_id = str(token_data.get("user_id"))
    result = storage.reset_account(user_id)
    return result

@app.get("/api/paper/orders")
def get_orders(token_data: dict = Depends(verify_token)):
    """Get order history"""
    user_id = str(token_data.get("user_id"))
    orders = storage.get_orders(user_id)
    return {"orders": orders}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)



