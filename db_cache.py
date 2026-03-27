"""
DB-backed cache layer for prices.
Layer 2 in: Memory (PriceCache) -> DB (cached_prices) -> yfinance
"""

import logging
from sqlalchemy import text
from database import engine

logger = logging.getLogger(__name__)


def load_cached_prices_from_db() -> list:
    """Bulk load all cached prices from DB. Used for startup warm."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT ticker, price, change, change_pct, name, updated_at "
                "FROM cached_prices"
            )).fetchall()
            return [
                {
                    "ticker": r[0], "price": float(r[1]) if r[1] else 0.0,
                    "change": float(r[2]) if r[2] else 0.0,
                    "change_pct": float(r[3]) if r[3] else 0.0,
                    "name": r[4],
                    "updated_at": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.error(f"Failed to load cached prices from DB: {e}")
        return []


def get_price_from_db(ticker: str) -> float | None:
    """Get a single cached price from DB. Returns price or None."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT price FROM cached_prices WHERE ticker = :ticker"
            ), {"ticker": ticker}).fetchone()
            if row and row[0]:
                return float(row[0])
    except Exception as e:
        logger.error(f"Failed to get price from DB for {ticker}: {e}")
    return None


def save_prices_batch_to_db(entries: list) -> None:
    """Batch upsert prices to DB. entries: list of dicts {ticker, price, change, change_pct, name, ...}"""
    if not entries:
        return
    try:
        with engine.begin() as conn:
            for entry in entries:
                conn.execute(text("""
                    INSERT INTO cached_prices (ticker, price, change, change_pct, name, sector, market_cap, updated_at)
                    VALUES (:ticker, :price, :change, :change_pct, :name, :sector, :market_cap, NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                        price = EXCLUDED.price,
                        change = COALESCE(EXCLUDED.change, cached_prices.change),
                        change_pct = COALESCE(EXCLUDED.change_pct, cached_prices.change_pct),
                        name = COALESCE(EXCLUDED.name, cached_prices.name),
                        sector = COALESCE(EXCLUDED.sector, cached_prices.sector),
                        market_cap = COALESCE(EXCLUDED.market_cap, cached_prices.market_cap),
                        updated_at = NOW()
                """), {
                    "ticker": entry.get("ticker"),
                    "price": entry.get("price", 0),
                    "change": entry.get("change", 0),
                    "change_pct": entry.get("change_pct", 0),
                    "name": entry.get("name"),
                    "sector": entry.get("sector"),
                    "market_cap": entry.get("market_cap"),
                })
    except Exception as e:
        logger.error(f"Failed to batch save prices to DB: {e}")
