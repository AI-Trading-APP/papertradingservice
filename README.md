# Paper Trading Service

Simulated trading environment for AI Trading Platform with realistic execution and fees.

## Features

- Virtual cash account ($100,000 starting capital)
- Simulated order execution (market & limit orders)
- Realistic slippage (0.1%)
- Commission-free trading (like Robinhood)
- Performance tracking
- Order history
- Reset capability
- Real-time price data from yfinance

## API Endpoints

### Account Management
- `GET /api/paper/account` - Get paper trading account (requires auth)
- `POST /api/paper/reset` - Reset account to starting state (requires auth)

### Trading
- `POST /api/paper/order` - Place order (market or limit) (requires auth)
- `GET /api/paper/orders` - Get order history (requires auth)

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the service:
```bash
python main.py
```

The service will start on `http://localhost:8005`

## Example Usage

### Get Account
```bash
curl http://localhost:8005/api/paper/account \
  -H "Authorization: Bearer <token>"
```

### Place Market Buy Order
```bash
curl -X POST http://localhost:8005/api/paper/order \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "ticker": "AAPL",
    "type": "market",
    "side": "buy",
    "quantity": 10
  }'
```

### Place Limit Sell Order
```bash
curl -X POST http://localhost:8005/api/paper/order \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "ticker": "AAPL",
    "type": "limit",
    "side": "sell",
    "quantity": 5,
    "limitPrice": 155.00
  }'
```

### Reset Account
```bash
curl -X POST http://localhost:8005/api/paper/reset \
  -H "Authorization: Bearer <token>"
```

## Trading Configuration

- **Starting Cash:** $100,000
- **Slippage:** 0.1% (buy orders filled higher, sell orders filled lower)
- **Commission:** $0 per trade
- **Order Types:** Market, Limit
- **Execution:** Immediate (simulated)

## Order Execution Logic

### Market Orders
- Buy orders: Filled at current price + 0.1% slippage
- Sell orders: Filled at current price - 0.1% slippage

### Limit Orders
- Buy limit: Executed if limit price >= market price
- Sell limit: Executed if limit price <= market price
- Otherwise rejected (in real trading, would be queued)

## Data Storage

Account data (positions, orders, balances) is stored in PostgreSQL, managed via the shared database layer.
```

## Notes

- Perfect for testing trading strategies without risk
- Uses real market prices from yfinance
- Slippage simulates real-world execution
- Can reset account anytime to start fresh
- Supports fractional shares

