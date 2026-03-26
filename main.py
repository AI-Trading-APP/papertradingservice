"""
Paper Trading Service
Simulated trading environment with realistic execution and fees
"""

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import uuid
import structlog
import yfinance as yf

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
for import_path in (CURRENT_DIR, PROJECT_ROOT):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from ai_trading_common.logging_config import setup_logging, get_logger
from storage import StorageAdapter

setup_logging("papertradingservice")
logger = get_logger()

app = FastAPI(
    title="Paper Trading Service",
    description="Simulated trading environment",
    version="2.0.0"
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status_code = locals().get("response").status_code if "response" in locals() else 500
        logger.info(
            "request_completed",
            method=request.method,
            endpoint=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        structlog.contextvars.clear_contextvars()
    return response

# CORS configuration from environment
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Storage adapter
storage = StorageAdapter()


@app.on_event("startup")
async def startup_event():
    logger.info(
        "papertradingservice_startup",
        version="2.0.0",
        cors_origins=CORS_ORIGINS,
    )

# Trading configuration
SLIPPAGE_PERCENT = 0.1  # 0.1% slippage
COMMISSION_PER_TRADE = 0.0  # $0 commission (like Robinhood)
STARTING_CASH = 100000.0  # $100,000 starting capital

# Models
class Order(BaseModel):
    ticker: str
    type: str  # "market" or "limit"
    side: str  # "buy" or "sell"
    quantity: float
    limitPrice: Optional[float] = None

class OrderResponse(BaseModel):
    orderId: str
    status: str  # "filled", "rejected"
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


def get_current_price(ticker: str) -> float:
    """Get current stock price with write-through cache and retry."""
    import time
    from price_cache import price_cache, PRICE_TTL

    cache_key = f"price:{ticker}"
    cached, is_fresh = price_cache.get(cache_key)
    if cached is not None and is_fresh:
        return cached

    max_retries = 3
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period="1d")
            if not data.empty:
                price = float(data['Close'].iloc[-1])
                price_cache.set(cache_key, price, PRICE_TTL)
                return price

            data = stock.history(period="5d")
            if not data.empty:
                price = float(data['Close'].iloc[-1])
                price_cache.set(cache_key, price, PRICE_TTL)
                return price

            if attempt < max_retries - 1:
                time.sleep(1)
                continue
        except Exception as e:
            logger.warning(
                "paper_price_fetch_failed",
                ticker=ticker,
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(e),
            )
            if attempt < max_retries - 1:
                time.sleep(1)

    # All retries failed — serve stale cache
    stale = price_cache.get_stale(cache_key)
    if stale is not None:
        logger.info("paper_stale_price_served", ticker=ticker, price=stale)
        return stale
    return 0.0

def apply_slippage(price: float, side: str) -> float:
    """Apply realistic slippage to order execution"""
    slippage = price * (SLIPPAGE_PERCENT / 100)
    if side == "buy":
        return price + slippage
    else:
        return price - slippage

def calculate_account_metrics(account: Dict) -> Dict:
    """Calculate account metrics with current prices"""
    total_market_value = account['cash']
    initial_value = STARTING_CASH

    for position in account['positions']:
        current_price = get_current_price(position['ticker'])
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

def verify_token(authorization: Optional[str] = None) -> dict:
    """Simple token verification"""
    return {"user_id": "user_1"}  # Mock user

# Routes
@app.get("/")
def read_root():
    return {
        "service": "Paper Trading Service",
        "status": "running",
        "version": "2.0.0",
        "storage_mode": "pg_only",
    }

@app.get("/health")
def health_check():
    from price_cache import price_cache
    from database import check_db_connection
    return {
        "status": "healthy",
        "service": "paper-trading-service",
        "cache": price_cache.stats(),
        "storage_mode": "pg_only",
        "db_connected": check_db_connection(),
    }

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
            return OrderResponse(
                orderId="",
                status="rejected",
                message="Limit price not favorable for immediate execution"
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
    logger.info("papertradingservice_started_successfully")
    uvicorn.run(app, host="0.0.0.0", port=8005)
