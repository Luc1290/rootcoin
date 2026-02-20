from fastapi import APIRouter, HTTPException

from backend import order_manager

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.delete("/{order_id}")
async def cancel_order(order_id: int):
    try:
        result = await order_manager.cancel_order(order_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))
