from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_is_active", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    sl_order_id: Mapped[str | None] = mapped_column(String)
    tp_order_id: Mapped[str | None] = mapped_column(String)
    oco_order_list_id: Mapped[str | None] = mapped_column(String)
    entry_fees_usd: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"))
    entry_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    exit_fees_usd: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric)
    realized_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_symbol_executed", "symbol", "executed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binance_trade_id: Mapped[str | None] = mapped_column(String, unique=True)
    binance_order_id: Mapped[str | None] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quote_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    commission: Mapped[Decimal | None] = mapped_column(Numeric)
    commission_asset: Mapped[str | None] = mapped_column(String)
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    is_maker: Mapped[bool | None] = mapped_column(Boolean)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric)
    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_position_id", "position_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binance_order_id: Mapped[str | None] = mapped_column(String, unique=True)
    binance_order_list_id: Mapped[str | None] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    order_type: Mapped[str] = mapped_column("type", String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"))
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str | None] = mapped_column(String)
    position_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("positions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (Index("ix_balances_snapshot_at", "snapshot_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False)
    free: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    locked: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    borrowed: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"))
    interest: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    wallet_type: Mapped[str] = mapped_column(String, nullable=False)
    usd_value: Mapped[Decimal | None] = mapped_column(Numeric)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (Index("ix_prices_symbol_recorded", "symbol", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String, default="ticker")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Kline(Base):
    __tablename__ = "klines"
    __table_args__ = (
        Index("uq_klines_sym_intv_time", "symbol", "interval", "open_time", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[str] = mapped_column(String, nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    quote_volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    taker_buy_base_vol: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    taker_buy_quote_vol: Mapped[Decimal] = mapped_column(Numeric, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class OpportunityRecord(Base):
    __tablename__ = "opportunity_records"
    __table_args__ = (Index("ix_opp_records_detected", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sl_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    tp_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    status: Mapped[str] = mapped_column(String, default="detected")
    outcome_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class PriceAlert(Base):
    __tablename__ = "price_alerts"
    __table_args__ = (Index("ix_price_alerts_active", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "above" or "below"
    note: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class TradeSnapshot(Base):
    __tablename__ = "trade_snapshots"
    __table_args__ = (
        Index("ix_snapshots_position_id", "position_id"),
        Index("ix_snapshots_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(Integer, ForeignKey("positions.id"), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    exit_reason: Mapped[str | None] = mapped_column(String)
    data: Mapped[str] = mapped_column(String, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
