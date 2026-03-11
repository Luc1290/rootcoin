from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select

from backend.core.database import async_session
from backend.core.models import Setting
from backend.services import telegram_notifier
from backend.trading import trailing_manager

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    async with async_session() as session:
        result = await session.execute(select(Setting))
        rows = result.scalars().all()
    return {r.key: r.value for r in rows}


@router.get("/{key}")
async def get_setting(key: str):
    async with async_session() as session:
        setting = await session.get(Setting, key)
    if not setting:
        return {"key": key, "value": None}
    return {"key": setting.key, "value": setting.value}


@router.put("/{key}")
async def put_setting(key: str, body: dict):
    value = body.get("value", "")
    async with async_session() as session:
        setting = await session.get(Setting, key)
        if setting:
            setting.value = str(value)
            setting.updated_at = datetime.now(timezone.utc)
        else:
            session.add(Setting(
                key=key,
                value=str(value),
                updated_at=datetime.now(timezone.utc),
            ))
        await session.commit()
    # Hot-reload trailing settings without restart
    if key.startswith("trailing_"):
        await trailing_manager._load_settings()

    return {"key": key, "value": str(value)}


@router.get("/trailing/mode")
async def get_trailing_mode():
    return {"mode": trailing_manager.get_mode()}


@router.post("/trailing/mode")
async def set_trailing_mode(body: dict):
    mode = body.get("mode", "auto")
    await trailing_manager.set_mode(mode)
    return {"mode": trailing_manager.get_mode()}


@router.get("/trailing/pending")
async def get_trailing_pending():
    return trailing_manager.get_pending()


@router.post("/trailing/pending/{pos_id}/confirm")
async def confirm_trailing_pending(pos_id: int):
    ok = await trailing_manager.confirm_pending(pos_id, source="dashboard")
    return {"ok": ok}


@router.post("/trailing/pending/{pos_id}/reject")
async def reject_trailing_pending(pos_id: int):
    ok = await trailing_manager.reject_pending(pos_id)
    return {"ok": ok}


@router.post("/telegram/toggle")
async def toggle_telegram(body: dict):
    enabled = body.get("enabled", False)
    await telegram_notifier.set_enabled(bool(enabled))
    return {
        "enabled": telegram_notifier.is_enabled(),
        "configured": telegram_notifier.is_configured(),
    }


@router.post("/telegram/category")
async def toggle_telegram_category(body: dict):
    key = body.get("key", "")
    enabled = body.get("enabled", False)
    await telegram_notifier.set_category_enabled(key, bool(enabled))
    return {"categories": telegram_notifier.get_categories()}


@router.post("/telegram/test")
async def test_telegram():
    if not telegram_notifier.is_configured():
        return {"ok": False, "error": "Token ou chat_id non configure"}
    ok = await telegram_notifier.test_connection()
    return {"ok": ok}
