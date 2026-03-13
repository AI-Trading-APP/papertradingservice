"""
StorageAdapter for PaperTradingService — PostgreSQL-only.
"""

import logging
from typing import Dict, Optional, List

import database
from repository import PaperTradingRepository

logger = logging.getLogger(__name__)

STARTING_CASH = 100000.0


class StorageAdapter:
    def __init__(self):
        logger.info("PaperTrading StorageAdapter initialized (pg_only)")

    # Read
    def get_account(self, user_id: str) -> Dict:
        db = database.SessionLocal()
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
        db = database.SessionLocal()
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
        db = database.SessionLocal()
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
        db = database.SessionLocal()
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
