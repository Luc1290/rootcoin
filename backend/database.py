from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "rootcoin.db"
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


async def init_db():
    from backend.models import Base

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, col_type in _MIGRATIONS:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            except Exception:
                pass


async def close_db():
    await engine.dispose()
