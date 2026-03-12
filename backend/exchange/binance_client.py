from decimal import Decimal

import structlog
from binance import AsyncClient
from binance.exceptions import BinanceAPIException

from backend.core.config import settings

log = structlog.get_logger()

_client: AsyncClient | None = None


async def init_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await AsyncClient.create(
            api_key=settings.binance_api_key.get_secret_value(),
            api_secret=settings.binance_secret_key.get_secret_value(),
        )
        log.info("binance_client_initialized")
    return _client


async def get_client() -> AsyncClient:
    if _client is None:
        raise RuntimeError("Binance client not initialized")
    return _client


async def close_client():
    global _client
    if _client:
        await _client.close_connection()
        _client = None
        log.info("binance_client_closed")


async def get_spot_balances() -> list[dict]:
    client = await get_client()
    try:
        account = await client.get_account()
        return account["balances"]
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_account", code=e.code, msg=e.message)
        raise


async def get_cross_margin_balances() -> list[dict]:
    client = await get_client()
    try:
        account = await client.get_margin_account()
        return account["userAssets"]
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_margin_account", code=e.code, msg=e.message)
        raise


async def get_isolated_margin_balances() -> list[dict]:
    client = await get_client()
    try:
        account = await client.get_isolated_margin_account()
        return account.get("assets", [])
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_isolated_margin_account", code=e.code, msg=e.message)
        raise


async def get_open_orders(symbol: str | None = None) -> list[dict]:
    client = await get_client()
    try:
        kwargs = {}
        if symbol:
            kwargs["symbol"] = symbol
        return await client.get_open_orders(**kwargs)
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_open_orders", code=e.code, msg=e.message)
        raise


async def get_margin_open_orders(symbol: str | None = None, is_isolated: bool = False) -> list[dict]:
    client = await get_client()
    try:
        kwargs = {}
        if symbol:
            kwargs["symbol"] = symbol
        if is_isolated:
            kwargs["isIsolated"] = "TRUE"
        return await client.get_open_margin_orders(**kwargs)
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_open_margin_orders", code=e.code, msg=e.message)
        raise


async def get_ticker_price(symbol: str) -> Decimal | None:
    """Fetch current price via REST API (fallback when WS price is stale)."""
    from decimal import Decimal
    client = await get_client()
    try:
        ticker = await client.get_symbol_ticker(symbol=symbol)
        price_str = ticker.get("price", "0")
        price = Decimal(price_str)
        return price if price > 0 else None
    except Exception:
        log.warning("ticker_price_failed", symbol=symbol, exc_info=True)
        return None


async def get_my_trades(symbol: str, limit: int = 500) -> list[dict]:
    client = await get_client()
    try:
        return await client.get_my_trades(symbol=symbol, limit=limit)
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_my_trades", symbol=symbol, code=e.code, msg=e.message)
        raise


async def get_margin_trades(symbol: str, limit: int = 500, is_isolated: bool = False) -> list[dict]:
    client = await get_client()
    try:
        kwargs = {"symbol": symbol, "limit": limit}
        if is_isolated:
            kwargs["isIsolated"] = "TRUE"
        return await client.get_margin_trades(**kwargs)
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_margin_trades", symbol=symbol, code=e.code, msg=e.message)
        raise


async def place_order(**kwargs) -> dict:
    client = await get_client()
    try:
        result = await client.create_order(**kwargs)
        log.info("order_placed", **{k: str(v) for k, v in kwargs.items()})
        return result
    except BinanceAPIException as e:
        log.error("order_failed", code=e.code, msg=e.message, **{k: str(v) for k, v in kwargs.items()})
        raise


async def place_margin_order(**kwargs) -> dict:
    client = await get_client()
    try:
        result = await client.create_margin_order(**kwargs)
        log.info("margin_order_placed", **{k: str(v) for k, v in kwargs.items()})
        return result
    except BinanceAPIException as e:
        log.error("margin_order_failed", code=e.code, msg=e.message, **{k: str(v) for k, v in kwargs.items()})
        raise


async def place_oco_order(**kwargs) -> dict:
    client = await get_client()
    try:
        # POST /api/v3/orderList/oco
        result = await client._post("orderList/oco", True, data=kwargs)
        log.info("oco_placed", **{k: str(v) for k, v in kwargs.items()})
        return result
    except BinanceAPIException as e:
        log.error("oco_failed", code=e.code, msg=e.message, **{k: str(v) for k, v in kwargs.items()})
        raise


async def place_margin_oco_order(**kwargs) -> dict:
    client = await get_client()
    try:
        # POST /sapi/v1/margin/order/oco
        result = await client._request_margin_api("post", "margin/order/oco", True, data=kwargs)
        log.info("margin_oco_placed", **{k: str(v) for k, v in kwargs.items()})
        return result
    except BinanceAPIException as e:
        log.error("margin_oco_failed", code=e.code, msg=e.message, **{k: str(v) for k, v in kwargs.items()})
        raise


async def cancel_order(symbol: str, order_id: str) -> dict:
    client = await get_client()
    try:
        result = await client.cancel_order(symbol=symbol, orderId=order_id)
        log.info("order_cancelled", symbol=symbol, order_id=order_id)
        return result
    except BinanceAPIException as e:
        if e.code == -2011:
            log.debug("order_already_gone", symbol=symbol, order_id=order_id)
            return {"orderId": order_id, "status": "CANCELED"}
        log.error("cancel_order_failed", symbol=symbol, order_id=order_id, code=e.code, msg=e.message)
        raise


async def cancel_margin_order(symbol: str, order_id: str, is_isolated: bool = False) -> dict:
    client = await get_client()
    try:
        kwargs = {"symbol": symbol, "orderId": order_id}
        if is_isolated:
            kwargs["isIsolated"] = "TRUE"
        result = await client.cancel_margin_order(**kwargs)
        log.info("margin_order_cancelled", symbol=symbol, order_id=order_id)
        return result
    except BinanceAPIException as e:
        if e.code == -2011:
            log.debug("margin_order_already_gone", symbol=symbol, order_id=order_id)
            return {"orderId": order_id, "status": "CANCELED"}
        log.error("cancel_margin_order_failed", symbol=symbol, order_id=order_id, code=e.code, msg=e.message)
        raise


async def cancel_oco_order(symbol: str, order_list_id: str) -> dict:
    client = await get_client()
    try:
        result = await client._delete("orderList", True, data={
            "symbol": symbol, "orderListId": order_list_id,
        })
        log.info("oco_cancelled", symbol=symbol, order_list_id=order_list_id)
        return result
    except BinanceAPIException as e:
        log.error("cancel_oco_failed", symbol=symbol, order_list_id=order_list_id,
                  code=e.code, msg=e.message)
        raise


async def cancel_margin_oco_order(
    symbol: str, order_list_id: str, is_isolated: bool = False,
) -> dict:
    client = await get_client()
    try:
        kwargs = {"symbol": symbol, "orderListId": order_list_id}
        if is_isolated:
            kwargs["isIsolated"] = "TRUE"
        result = await client._request_margin_api(
            "delete", "margin/orderList", True, data=kwargs,
        )
        log.info("margin_oco_cancelled", symbol=symbol, order_list_id=order_list_id)
        return result
    except BinanceAPIException as e:
        log.error("cancel_margin_oco_failed", symbol=symbol, order_list_id=order_list_id,
                  code=e.code, msg=e.message)
        raise


async def repay_margin_loan(
    asset: str, amount, is_isolated: bool = False, symbol: str | None = None,
) -> dict:
    client = await get_client()
    try:
        kwargs = {"asset": asset, "amount": str(amount)}
        if is_isolated and symbol:
            kwargs["isIsolated"] = "TRUE"
            kwargs["symbol"] = symbol
        result = await client.repay_margin_loan(**kwargs)
        log.info("margin_loan_repaid", asset=asset, amount=str(amount),
                 is_isolated=is_isolated)
        return result
    except BinanceAPIException as e:
        log.error("margin_repay_failed", asset=asset, amount=str(amount),
                  code=e.code, msg=e.message)
        raise


async def get_exchange_info() -> dict:
    client = await get_client()
    try:
        return await client.get_exchange_info()
    except BinanceAPIException as e:
        log.error("binance_api_error", endpoint="get_exchange_info", code=e.code, msg=e.message)
        raise
