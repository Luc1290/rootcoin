from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter
from sqlalchemy import select

from backend.core.database import async_session
from backend.core.models import PriceAlert

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def get_alerts(symbol: str | None = None, active_only: bool = True):
    async with async_session() as session:
        q = select(PriceAlert)
        if active_only:
            q = q.where(PriceAlert.is_active == True)
        if symbol:
            q = q.where(PriceAlert.symbol == symbol.upper())
        q = q.order_by(PriceAlert.created_at.desc())
        result = await session.execute(q)
        rows = result.scalars().all()
    return [_to_dict(r) for r in rows]


@router.post("")
async def create_alert(body: dict):
    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol:
        return {"error": "symbol requis"}
    try:
        target_price = Decimal(str(body.get("target_price", "")))
    except (InvalidOperation, ValueError):
        return {"error": "prix invalide"}
    if target_price <= 0:
        return {"error": "prix doit etre > 0"}

    direction = (body.get("direction") or "").lower()
    if direction not in ("above", "below"):
        return {"error": "direction doit etre 'above' ou 'below'"}

    note = body.get("note", "")

    alert = PriceAlert(
        symbol=symbol,
        target_price=target_price,
        direction=direction,
        note=note or None,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
    return _to_dict(alert)


@router.delete("/{alert_id}")
async def delete_alert(alert_id: int):
    async with async_session() as session:
        alert = await session.get(PriceAlert, alert_id)
        if not alert:
            return {"error": "alerte introuvable"}
        await session.delete(alert)
        await session.commit()
    return {"ok": True}


def _to_dict(a: PriceAlert) -> dict:
    return {
        "id": a.id,
        "symbol": a.symbol,
        "target_price": str(a.target_price),
        "direction": a.direction,
        "note": a.note,
        "is_active": a.is_active,
        "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
