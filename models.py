"""
SQLAlchemy ORM models for paper trading tables.
"""

from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Text, ForeignKey, CheckConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from database import Base


class PaperAccountDB(Base):
    __tablename__ = "paper_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, unique=True, nullable=False)
    cash = Column(Numeric(15, 2), nullable=False, default=100000.00)
    starting_cash = Column(Numeric(15, 2), nullable=False, default=100000.00)
    total_value = Column(Numeric(15, 2))
    total_pl = Column(Numeric(15, 4))
    total_pl_percent = Column(Numeric(10, 4))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reset_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, nullable=False, default=1)

    positions = relationship("PaperPositionDB", back_populates="account",
                             cascade="all, delete-orphan", lazy="selectin")
    orders = relationship("PaperOrderDB", back_populates="account",
                          cascade="all, delete-orphan", lazy="selectin",
                          order_by="PaperOrderDB.timestamp.desc()")


class PaperPositionDB(Base):
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False)
    ticker = Column(String(10), nullable=False)
    quantity = Column(Numeric(15, 4), nullable=False)
    avg_cost_basis = Column(Numeric(15, 4), nullable=False)
    current_price = Column(Numeric(15, 4))
    market_value = Column(Numeric(15, 4))
    unrealized_pl = Column(Numeric(15, 4))
    unrealized_pl_percent = Column(Numeric(10, 4))
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    account = relationship("PaperAccountDB", back_populates="positions")

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_paper_pos_qty"),
        {"extend_existing": True},
    )


class PaperOrderDB(Base):
    __tablename__ = "paper_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False)
    ticker = Column(String(10), nullable=False)
    order_type = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    quantity = Column(Numeric(15, 4), nullable=False)
    limit_price = Column(Numeric(15, 4))
    filled_price = Column(Numeric(15, 4))
    filled_quantity = Column(Numeric(15, 4))
    status = Column(String(20), nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    filled_at = Column(DateTime)
    rejection_reason = Column(Text)

    account = relationship("PaperAccountDB", back_populates="orders")

    __table_args__ = (
        CheckConstraint("order_type IN ('market', 'limit')", name="ck_paper_order_type"),
        CheckConstraint("side IN ('buy', 'sell')", name="ck_paper_order_side"),
        {"extend_existing": True},
    )
