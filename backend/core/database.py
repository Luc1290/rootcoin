from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(settings.database_path) if settings.database_path else ROOT_DIR / "data" / "rootcoin.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_MIGRATIONS = [
    ("positions", "entry_quantity", "NUMERIC"),
    ("positions", "exit_price", "NUMERIC"),
    ("positions", "exit_fees_usd", "NUMERIC DEFAULT 0"),
    ("positions", "realized_pnl", "NUMERIC"),
    ("positions", "realized_pnl_pct", "NUMERIC"),
    ("positions", "closed_at", "DATETIME"),
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_positions_is_active ON positions (is_active)",
    "CREATE INDEX IF NOT EXISTS ix_trades_symbol_executed ON trades (symbol, executed_at)",
    "CREATE INDEX IF NOT EXISTS ix_orders_position_id ON orders (position_id)",
    "CREATE INDEX IF NOT EXISTS ix_balances_snapshot_at ON balances (snapshot_at)",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_position_id ON trade_snapshots (position_id)",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_captured_at ON trade_snapshots (captured_at)",
]


async def init_db():
    from backend.core.models import Base

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, col_type in _MIGRATIONS:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            except Exception:
                pass
        for idx_sql in _INDEXES:
            await conn.execute(text(idx_sql))


async def close_db():
    await engine.dispose()
