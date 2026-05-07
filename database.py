"""
Database configuration for PaperTradingService.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
for import_path in (CURRENT_DIR, PROJECT_ROOT):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from ai_trading_common.logging_config import get_logger

logger = get_logger()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ai_trading:ai_trading@localhost:5439/ai_trading_test"
)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=5,
        pool_recycle=300,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _sqlite_cached_prices_ddl() -> str:
    return """
        CREATE TABLE IF NOT EXISTS cached_prices (
            ticker TEXT PRIMARY KEY,
            price REAL NOT NULL,
            change REAL,
            change_pct REAL,
            name TEXT,
            sector TEXT,
            market_cap REAL,
            updated_at TIMESTAMP NOT NULL
        )
    """


def _postgres_cached_prices_ddl() -> str:
    return """
        CREATE TABLE IF NOT EXISTS cached_prices (
            ticker VARCHAR(16) PRIMARY KEY,
            price NUMERIC(15, 4) NOT NULL,
            change NUMERIC(15, 4),
            change_pct NUMERIC(15, 4),
            name TEXT,
            sector TEXT,
            market_cap NUMERIC(20, 4),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def ensure_cached_prices_table() -> None:
    """Create the DB cache table if it does not exist yet."""
    ddl = _sqlite_cached_prices_ddl() if engine.dialect.name == "sqlite" else _postgres_cached_prices_ddl()
    with engine.begin() as conn:
        conn.execute(text(ddl))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("papertrading_db_connection_check_failed", error=str(e))
        return False
