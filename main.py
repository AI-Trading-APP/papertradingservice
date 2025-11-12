"""
Paper Trading Service
Simulated trading environment with realistic execution and fees
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime
import json
import os
import yfinance as yf
import random

app = FastAPI(
    title="Paper Trading Service",
    description="Simulated trading environment",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data file
PAPER_ACCOUNTS_FILE = "paper_accounts.json"

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
    createdAt: str

# Helper functions
def load_accounts() -> Dict:
    """Load paper trading accounts from file"""
    if os.path.exists(PAPER_ACCOUNTS_FILE):
        with open(PAPER_ACCOUNTS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_accounts(accounts: Dict):
    """Save paper trading accounts to file"""
    with open(PAPER_ACCOUNTS_FILE, 'w') as f:
        json.dump(accounts, f, indent=2)

def get_current_price(ticker: str) -> float:
    """Get current stock price from yfinance with retry logic"""
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            # Try to get today's data first
            data = stock.history(period="1d")
            if not data.empty:
                return float(data['Close'].iloc[-1])

            # If today's data is not available, try 5 days
            data = stock.history(period="5d")
            if not data.empty:
                return float(data['Close'].iloc[-1])

            # If still no data, wait and retry
            if attempt < max_retries - 1:
                time.sleep(1)
                continue

            return 0.0
        except Exception as e:
            print(f"Error fetching price for {ticker} (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return 0.0
    return 0.0

def apply_slippage(price: float, side: str) -> float:
    """Apply realistic slippage to order execution"""
    slippage = price * (SLIPPAGE_PERCENT / 100)
    if side == "buy":
        # Buy orders get filled slightly higher
        return price + slippage
    else:
        # Sell orders get filled slightly lower
        return price - slippage

def calculate_account_metrics(account: Dict) -> Dict:
    """Calculate account metrics with current prices"""
    total_market_value = account['cash']
    initial_value = STARTING_CASH
    
    for position in account['positions']:
        # Get current price
        current_price = get_current_price(position['ticker'])
        position['currentPrice'] = current_price
        
        # Calculate market value
        market_value = position['quantity'] * current_price
        position['marketValue'] = market_value
        
        # Calculate cost basis
        cost_basis = position['quantity'] * position['avgCostBasis']
        
        # Calculate unrealized P&L
        unrealized_pl = market_value - cost_basis
        position['unrealizedPL'] = unrealized_pl
        position['unrealizedPLPercent'] = (unrealized_pl / cost_basis * 100) if cost_basis > 0 else 0
        
        total_market_value += market_value
    
    # Calculate total P&L
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
    """Health check endpoint"""
    return {
        "service": "Paper Trading Service",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/api/paper/account", response_model=PaperAccount)
def get_account(token_data: dict = Depends(verify_token)):
    """Get paper trading account"""
    user_id = token_data.get("user_id")
    accounts = load_accounts()

    # Initialize account if doesn't exist
    if user_id not in accounts:
        accounts[user_id] = {
            "userId": user_id,
            "cash": STARTING_CASH,
            "positions": [],
            "orders": [],
            "createdAt": datetime.utcnow().isoformat()
        }
        save_accounts(accounts)

    account = accounts[user_id]
    account = calculate_account_metrics(account)

    return account

@app.post("/api/paper/order", response_model=OrderResponse)
def place_order(order: Order, token_data: dict = Depends(verify_token)):
    """Place a paper trading order"""
    user_id = token_data.get("user_id")
    accounts = load_accounts()

    if user_id not in accounts:
        raise HTTPException(status_code=404, detail="Account not found")

    account = accounts[user_id]

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
        # For paper trading, execute limit orders immediately if price is favorable
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

    # Execute order
    if order.side == "buy":
        total_cost = order.quantity * execution_price + COMMISSION_PER_TRADE

        if account['cash'] < total_cost:
            return OrderResponse(
                orderId="",
                status="rejected",
                message="Insufficient funds"
            )

        # Deduct cash
        account['cash'] -= total_cost

        # Find or create position
        existing_position = None
        for pos in account['positions']:
            if pos['ticker'] == order.ticker:
                existing_position = pos
                break

        if existing_position:
            # Update average cost basis
            total_quantity = existing_position['quantity'] + order.quantity
            total_cost_basis = (existing_position['quantity'] * existing_position['avgCostBasis']) + (order.quantity * execution_price)
            existing_position['avgCostBasis'] = total_cost_basis / total_quantity
            existing_position['quantity'] = total_quantity
        else:
            # Add new position
            account['positions'].append({
                "ticker": order.ticker,
                "quantity": order.quantity,
                "avgCostBasis": execution_price
            })

    else:  # sell
        # Find position
        position = None
        for pos in account['positions']:
            if pos['ticker'] == order.ticker:
                position = pos
                break

        if not position:
            return OrderResponse(
                orderId="",
                status="rejected",
                message="No position to sell"
            )

        if position['quantity'] < order.quantity:
            return OrderResponse(
                orderId="",
                status="rejected",
                message="Insufficient shares"
            )

        # Calculate proceeds
        proceeds = (order.quantity * execution_price) - COMMISSION_PER_TRADE

        # Add cash
        account['cash'] += proceeds

        # Update or remove position
        position['quantity'] -= order.quantity

        if position['quantity'] == 0:
            account['positions'].remove(position)

    # Record order
    order_id = f"order_{len(account['orders']) + 1}"
    account['orders'].append({
        "orderId": order_id,
        "ticker": order.ticker,
        "type": order.type,
        "side": order.side,
        "quantity": order.quantity,
        "filledPrice": execution_price,
        "filledQuantity": order.quantity,
        "status": "filled",
        "timestamp": datetime.utcnow().isoformat()
    })

    save_accounts(accounts)

    return OrderResponse(
        orderId=order_id,
        status="filled",
        filledPrice=execution_price,
        filledQuantity=order.quantity,
        message=f"Order filled at ${execution_price:.2f}"
    )

@app.post("/api/paper/reset")
def reset_account(token_data: dict = Depends(verify_token)):
    """Reset paper trading account to starting state"""
    user_id = token_data.get("user_id")
    accounts = load_accounts()

    accounts[user_id] = {
        "userId": user_id,
        "cash": STARTING_CASH,
        "positions": [],
        "orders": [],
        "createdAt": datetime.utcnow().isoformat()
    }

    save_accounts(accounts)

    return {"message": "Account reset successfully", "startingCash": STARTING_CASH}

@app.get("/api/paper/orders")
def get_orders(token_data: dict = Depends(verify_token)):
    """Get order history"""
    user_id = token_data.get("user_id")
    accounts = load_accounts()

    if user_id not in accounts:
        return {"orders": []}

    return {"orders": accounts[user_id]['orders']}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)

