"""
PaperTradingRepository — ACID-compliant data access for paper trading.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

try:
    from models import PaperAccountDB, PaperOrderDB, PaperPositionDB
except ImportError:  # pragma: no cover - supports package imports
    from .models import PaperAccountDB, PaperOrderDB, PaperPositionDB

STARTING_CASH = Decimal("100000.00")


class PaperTradingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_account(self, user_id: int) -> Optional[PaperAccountDB]:
        return (
            self.db.query(PaperAccountDB)
            .filter(PaperAccountDB.user_id == user_id)
            .first()
        )

    def get_or_create_account(self, user_id: int) -> PaperAccountDB:
        account = self.get_account(user_id)
        if account is None:
            account = PaperAccountDB(
                user_id=user_id,
                cash=STARTING_CASH,
                starting_cash=STARTING_CASH,
                version=1,
            )
            self.db.add(account)
            self.db.flush()
        return account

    def get_position(self, account_id: int, ticker: str) -> Optional[PaperPositionDB]:
        return (
            self.db.query(PaperPositionDB)
            .filter(and_(
                PaperPositionDB.account_id == account_id,
                PaperPositionDB.ticker == ticker,
            ))
            .first()
        )

    def place_order(self, user_id: int, ticker: str, order_type: str, side: str,
                    quantity: float, execution_price: float,
                    limit_price: Optional[float] = None) -> Dict:
        """Execute an order: update cash, upsert position, record order."""
        qty = Decimal(str(quantity))
        px = Decimal(str(execution_price))

        account = self.get_or_create_account(user_id)
        old_version = account.version

        if side == "buy":
            total_cost = qty * px
            if account.cash < total_cost:
                raise ValueError("Insufficient funds")

            account.cash -= total_cost
            account.version += 1
            self.db.flush()

            # Upsert position
            position = self.get_position(account.id, ticker)
            if position:
                total_qty = position.quantity + qty
                total_basis = (position.quantity * position.avg_cost_basis) + (qty * px)
                position.avg_cost_basis = total_basis / total_qty
                position.quantity = total_qty
            else:
                position = PaperPositionDB(
                    account_id=account.id,
                    ticker=ticker,
                    quantity=qty,
                    avg_cost_basis=px,
                )
                self.db.add(position)

        else:  # sell
            position = self.get_position(account.id, ticker)
            if not position:
                raise ValueError("No position to sell")
            if position.quantity < qty:
                raise ValueError("Insufficient shares")

            proceeds = qty * px
            account.cash += proceeds
            account.version += 1
            self.db.flush()

            position.quantity -= qty
            if position.quantity == 0:
                self.db.delete(position)

        # Record order
        now = datetime.now(timezone.utc)
        order = PaperOrderDB(
            account_id=account.id,
            ticker=ticker,
            order_type=order_type,
            side=side,
            quantity=qty,
            limit_price=Decimal(str(limit_price)) if limit_price else None,
            filled_price=px,
            filled_quantity=qty,
            status="filled",
            timestamp=now,
            filled_at=now,
        )
        self.db.add(order)
        self.db.flush()

        return {
            "orderId": f"order_{order.id}",
            "status": "filled",
            "filledPrice": float(px),
            "filledQuantity": float(qty),
            "message": f"Order filled at ${float(px):.2f}",
        }

    def create_pending_order(self, user_id: int, ticker: str, side: str,
                              quantity: float, limit_price: float) -> Dict:
        """Create a pending limit order without executing (no cash/position change)."""
        qty = Decimal(str(quantity))
        lp = Decimal(str(limit_price))

        account = self.get_or_create_account(user_id)
        now = datetime.now(timezone.utc)

        order = PaperOrderDB(
            account_id=account.id,
            ticker=ticker,
            order_type="limit",
            side=side,
            quantity=qty,
            limit_price=lp,
            filled_price=None,
            filled_quantity=None,
            status="pending",
            timestamp=now,
            filled_at=None,
        )
        self.db.add(order)
        self.db.flush()

        return {
            "orderId": f"order_{order.id}",
            "status": "pending",
            "filledPrice": None,
            "filledQuantity": None,
            "message": f"Limit order pending — {side} {quantity} {ticker} @ ${limit_price:.2f}",
        }

    def reset_account(self, user_id: int) -> Dict:
        """Reset account: delete positions/orders, restore cash."""
        account = self.get_account(user_id)
        if account:
            # Delete all positions and orders
            self.db.query(PaperPositionDB).filter(
                PaperPositionDB.account_id == account.id
            ).delete()
            self.db.query(PaperOrderDB).filter(
                PaperOrderDB.account_id == account.id
            ).delete()
            account.cash = STARTING_CASH
            account.reset_at = datetime.now(timezone.utc)
            account.version += 1
        else:
            account = PaperAccountDB(
                user_id=user_id,
                cash=STARTING_CASH,
                starting_cash=STARTING_CASH,
                version=1,
            )
            self.db.add(account)
        self.db.flush()
        return {"message": "Account reset successfully", "startingCash": float(STARTING_CASH)}

    def get_orders(self, account_id: int) -> List[PaperOrderDB]:
        return (
            self.db.query(PaperOrderDB)
            .filter(PaperOrderDB.account_id == account_id)
            .order_by(PaperOrderDB.timestamp.desc())
            .all()
        )

    # Format conversion
    def to_account_dict(self, account: PaperAccountDB) -> Dict:
        return {
            "userId": str(account.user_id),
            "cash": float(account.cash),
            "positions": [self._pos_to_dict(p) for p in account.positions],
            "orders": [self._order_to_dict(o) for o in account.orders],
            "createdAt": account.created_at.isoformat() if account.created_at else None,
        }

    @staticmethod
    def _pos_to_dict(p: PaperPositionDB) -> Dict:
        return {
            "ticker": p.ticker,
            "quantity": float(p.quantity),
            "avgCostBasis": float(p.avg_cost_basis),
        }

    @staticmethod
    def _order_to_dict(o: PaperOrderDB) -> Dict:
        return {
            "orderId": f"order_{o.id}",
            "ticker": o.ticker,
            "type": o.order_type,
            "side": o.side,
            "quantity": float(o.quantity),
            "filledPrice": float(o.filled_price) if o.filled_price else None,
            "filledQuantity": float(o.filled_quantity) if o.filled_quantity else None,
            "status": o.status,
            "timestamp": o.timestamp.isoformat() if o.timestamp else None,
        }
