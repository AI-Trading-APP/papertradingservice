"""
StorageAdapter for PaperTradingService — dual-write abstraction.
Controlled by STORAGE_MODE env var: json_only | dual_write | pg_read | pg_only
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

from database import STORAGE_MODE, SessionLocal
from repository import PaperTradingRepository

logger = logging.getLogger(__name__)

PAPER_ACCOUNTS_FILE = os.getenv("PAPER_ACCOUNTS_FILE", "paper_accounts.json")

STARTING_CASH = 100000.0


class StorageAdapter:
    def __init__(self):
        self.mode = STORAGE_MODE
        logger.info(f"PaperTrading StorageAdapter initialized: {self.mode}")

    # JSON helpers
    def _load_json(self) -> Dict:
        if os.path.exists(PAPER_ACCOUNTS_FILE):
            with open(PAPER_ACCOUNTS_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save_json(self, data: Dict):
        with open(PAPER_ACCOUNTS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    # Read
    def get_account(self, user_id: str) -> Dict:
        if self.mode in ("json_only", "dual_write"):
            return self._get_account_json(user_id)
        return self._get_account_pg(user_id)

    def _get_account_json(self, user_id: str) -> Dict:
        accounts = self._load_json()
        if user_id not in accounts:
            accounts[user_id] = {
                "userId": user_id,
                "cash": STARTING_CASH,
                "positions": [],
                "orders": [],
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            self._save_json(accounts)
        return accounts[user_id]

    def _get_account_pg(self, user_id: str) -> Dict:
        db = SessionLocal()
        try:
            repo = PaperTradingRepository(db)
            account = repo.get_or_create_account(self._resolve_uid(user_id))
            db.commit()
            return repo.to_account_dict(account)
        except Exception as e:
            db.rollback()
            logger.error(f"PG read failed: {e}")
            raise
        finally:
            db.close()

    # Place order
    def place_order(self, user_id: str, ticker: str, order_type: str, side: str,
                    quantity: float, execution_price: float,
                    limit_price: Optional[float] = None) -> Dict:
        result = None
        if self.mode in ("json_only", "dual_write", "pg_read"):
            result = self._place_order_json(user_id, ticker, order_type, side,
                                            quantity, execution_price, limit_price)
        if self.mode in ("dual_write", "pg_read", "pg_only"):
            try:
                pg_result = self._place_order_pg(user_id, ticker, order_type, side,
                                                 quantity, execution_price, limit_price)
                if self.mode == "pg_only":
                    result = pg_result
            except Exception as e:
                logger.error(f"PG order failed: {e}")
                if self.mode == "pg_only":
                    raise
        return result

    def _place_order_json(self, user_id, ticker, order_type, side, quantity,
                          execution_price, limit_price) -> Dict:
        accounts = self._load_json()
        if user_id not in accounts:
            raise ValueError("Account not found")
        account = accounts[user_id]

        if side == "buy":
            total_cost = quantity * execution_price
            if account["cash"] < total_cost:
                raise ValueError("Insufficient funds")
            account["cash"] -= total_cost

            existing = next((p for p in account["positions"] if p["ticker"] == ticker), None)
            if existing:
                total_qty = existing["quantity"] + quantity
                total_basis = (existing["quantity"] * existing["avgCostBasis"]) + (quantity * execution_price)
                existing["avgCostBasis"] = total_basis / total_qty
                existing["quantity"] = total_qty
            else:
                account["positions"].append({
                    "ticker": ticker,
                    "quantity": quantity,
                    "avgCostBasis": execution_price,
                })
        else:
            position = next((p for p in account["positions"] if p["ticker"] == ticker), None)
            if not position:
                raise ValueError("No position to sell")
            if position["quantity"] < quantity:
                raise ValueError("Insufficient shares")
            proceeds = quantity * execution_price
            account["cash"] += proceeds
            position["quantity"] -= quantity
            if position["quantity"] == 0:
                account["positions"].remove(position)

        order_id = f"order_{len(account['orders']) + 1}"
        account["orders"].append({
            "orderId": order_id,
            "ticker": ticker,
            "type": order_type,
            "side": side,
            "quantity": quantity,
            "filledPrice": execution_price,
            "filledQuantity": quantity,
            "status": "filled",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save_json(accounts)
        return {
            "orderId": order_id,
            "status": "filled",
            "filledPrice": execution_price,
            "filledQuantity": quantity,
            "message": f"Order filled at ${execution_price:.2f}",
        }

    def _place_order_pg(self, user_id, ticker, order_type, side, quantity,
                        execution_price, limit_price) -> Dict:
        db = SessionLocal()
        try:
            repo = PaperTradingRepository(db)
            result = repo.place_order(
                self._resolve_uid(user_id), ticker, order_type, side,
                quantity, execution_price, limit_price,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Reset
    def reset_account(self, user_id: str) -> Dict:
        result = None
        if self.mode in ("json_only", "dual_write", "pg_read"):
            result = self._reset_json(user_id)
        if self.mode in ("dual_write", "pg_read", "pg_only"):
            try:
                pg_result = self._reset_pg(user_id)
                if self.mode == "pg_only":
                    result = pg_result
            except Exception as e:
                logger.error(f"PG reset failed: {e}")
                if self.mode == "pg_only":
                    raise
        return result

    def _reset_json(self, user_id: str) -> Dict:
        accounts = self._load_json()
        accounts[user_id] = {
            "userId": user_id,
            "cash": STARTING_CASH,
            "positions": [],
            "orders": [],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        self._save_json(accounts)
        return {"message": "Account reset successfully", "startingCash": STARTING_CASH}

    def _reset_pg(self, user_id: str) -> Dict:
        db = SessionLocal()
        try:
            repo = PaperTradingRepository(db)
            result = repo.reset_account(self._resolve_uid(user_id))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Orders
    def get_orders(self, user_id: str) -> List[Dict]:
        if self.mode in ("json_only", "dual_write"):
            return self._get_orders_json(user_id)
        return self._get_orders_pg(user_id)

    def _get_orders_json(self, user_id: str) -> List[Dict]:
        accounts = self._load_json()
        if user_id not in accounts:
            return []
        return accounts[user_id].get("orders", [])

    def _get_orders_pg(self, user_id: str) -> List[Dict]:
        db = SessionLocal()
        try:
            repo = PaperTradingRepository(db)
            account = repo.get_account(self._resolve_uid(user_id))
            if not account:
                return []
            orders = repo.get_orders(account.id)
            return [repo._order_to_dict(o) for o in orders]
        finally:
            db.close()

    @staticmethod
    def _resolve_uid(user_id_str: str) -> int:
        if user_id_str.startswith("user_"):
            return int(user_id_str.split("_", 1)[1])
        return int(user_id_str)
