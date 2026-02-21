import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
from collections import defaultdict
from typing import Any, Callable, Coroutine

import httpx
import structlog
import websockets

from backend.config import settings

log = structlog.get_logger()

Callback = Callable[..., Coroutine[Any, Any, None]]

EVENT_EXECUTION_REPORT = "execution_report"
EVENT_ACCOUNT_UPDATE = "account_update"
EVENT_BALANCE_UPDATE = "balance_update"
EVENT_LIST_STATUS = "list_status"
EVENT_PRICE_UPDATE = "price_update"
EVENT_KLINE_UPDATE = "kline_update"

BINANCE_WS_URL = "wss://stream.binance.com:9443"
BINANCE_WS_API_URL = "wss://ws-api.binance.com:443/ws-api/v3"
BINANCE_API_URL = "https://api.binance.com"

MAX_BACKOFF = 60
STABLE_CONNECTION_RESET = 300
TOKEN_REFRESH_INTERVAL = 1800


class WSManager:
    def __init__(self):
        self._callbacks: dict[str, list[Callback]] = defaultdict(list)
        self._subscribed_symbols: set[str] = set()
        self._subscribed_klines: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._listen_token: str | None = None
        self._price_ws: Any = None
        self._msg_id = 0

    def on(self, event_type: str, callback: Callback):
        self._callbacks[event_type].append(callback)

    async def _dispatch(self, event_type: str, data: dict):
        for cb in self._callbacks.get(event_type, []):
            try:
                await cb(data)
            except Exception:
                log.error("callback_error", event_type=event_type, exc_info=True)

    async def start(self):
        self._running = True
        self._subscribed_symbols = set(settings.watchlist)
        self._tasks.append(asyncio.create_task(self._run_user_stream()))
        self._tasks.append(asyncio.create_task(self._run_price_stream()))
        self._tasks.append(asyncio.create_task(self._run_token_refresh()))
        log.info("ws_manager_started", symbols=list(self._subscribed_symbols))

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("ws_manager_stopped")

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    # --- Dynamic symbol subscription ---

    async def subscribe_symbol(self, symbol: str):
        if symbol in self._subscribed_symbols:
            return
        self._subscribed_symbols.add(symbol)
        if self._price_ws:
            msg = {
                "method": "SUBSCRIBE",
                "params": [f"{symbol.lower()}@ticker"],
                "id": self._next_id(),
            }
            try:
                await self._price_ws.send(json.dumps(msg))
                log.info("price_stream_subscribed", symbol=symbol)
            except Exception:
                log.warning("price_stream_subscribe_failed", symbol=symbol)

    async def unsubscribe_symbol(self, symbol: str):
        self._subscribed_symbols.discard(symbol)
        if self._price_ws:
            msg = {
                "method": "UNSUBSCRIBE",
                "params": [f"{symbol.lower()}@ticker"],
                "id": self._next_id(),
            }
            try:
                await self._price_ws.send(json.dumps(msg))
                log.info("price_stream_unsubscribed", symbol=symbol)
            except Exception:
                log.warning("price_stream_unsubscribe_failed", symbol=symbol)

    # --- Dynamic kline subscription ---

    async def subscribe_kline(self, symbol: str, interval: str):
        stream = f"{symbol.lower()}@kline_{interval}"
        if stream in self._subscribed_klines:
            return
        self._subscribed_klines.add(stream)
        if self._price_ws:
            try:
                await self._price_ws.send(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": [stream],
                    "id": self._next_id(),
                }))
                log.info("kline_stream_subscribed", symbol=symbol, interval=interval)
            except Exception:
                log.warning("kline_stream_subscribe_failed", symbol=symbol, interval=interval)

    async def unsubscribe_kline(self, symbol: str, interval: str):
        stream = f"{symbol.lower()}@kline_{interval}"
        self._subscribed_klines.discard(stream)
        if self._price_ws:
            try:
                await self._price_ws.send(json.dumps({
                    "method": "UNSUBSCRIBE",
                    "params": [stream],
                    "id": self._next_id(),
                }))
                log.info("kline_stream_unsubscribed", symbol=symbol, interval=interval)
            except Exception:
                log.warning("kline_stream_unsubscribe_failed", symbol=symbol, interval=interval)

    # --- Listen Token ---

    def _sign_params(self, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            settings.binance_secret_key.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _obtain_listen_token(self) -> str:
        headers = {"X-MBX-APIKEY": settings.binance_api_key}
        params = {"timestamp": str(int(time.time() * 1000))}
        params = self._sign_params(params)
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{BINANCE_API_URL}/sapi/v1/userListenToken",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("listenKey") or data.get("token") or data.get("listenToken")
            if not token:
                log.error("listen_token_unexpected_response", response=data)
                raise ValueError(f"No token in response: {data}")
            log.info("listen_token_obtained")
            return token

    # --- User Data Stream ---

    async def _run_user_stream(self):
        backoff = 1
        connected_at: float | None = None

        while self._running:
            try:
                self._listen_token = await self._obtain_listen_token()

                async with websockets.connect(BINANCE_WS_API_URL) as ws:
                    # Subscribe via the new listenToken method
                    subscribe_msg = {
                        "id": self._next_id(),
                        "method": "userDataStream.subscribe.listenToken",
                        "params": {"listenToken": self._listen_token},
                    }
                    await ws.send(json.dumps(subscribe_msg))

                    # Wait for subscribe response
                    resp_raw = await ws.recv()
                    resp = json.loads(resp_raw)
                    if "error" in resp:
                        raise RuntimeError(f"Subscribe failed: {resp['error']}")

                    connected_at = time.monotonic()
                    backoff = 1
                    log.info("user_data_stream_connected", method="listenToken")

                    async for raw in ws:
                        if not self._running:
                            break
                        msg = json.loads(raw)
                        # WS API wraps events in {"subscriptionId": ..., "event": {...}}
                        event_data = msg.get("event")
                        if isinstance(event_data, dict) and "e" in event_data:
                            await self._handle_user_event(event_data)
                        elif "e" in msg:
                            await self._handle_user_event(msg)

                        if connected_at and (time.monotonic() - connected_at) > STABLE_CONNECTION_RESET:
                            backoff = 1
                            connected_at = time.monotonic()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                log.warning("user_stream_disconnected", error=str(e), reconnect_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _handle_user_event(self, msg: dict):
        event_type = msg.get("e")
        if event_type == "executionReport":
            await self._dispatch(EVENT_EXECUTION_REPORT, msg)
        elif event_type == "outboundAccountPosition":
            await self._dispatch(EVENT_ACCOUNT_UPDATE, msg)
        elif event_type == "balanceUpdate":
            await self._dispatch(EVENT_BALANCE_UPDATE, msg)
        elif event_type == "listStatus":
            await self._dispatch(EVENT_LIST_STATUS, msg)

    # --- Token Refresh ---

    async def _keepalive_listen_key(self):
        if not self._listen_token:
            return
        headers = {"X-MBX-APIKEY": settings.binance_api_key}
        params = {"timestamp": str(int(time.time() * 1000))}
        params = self._sign_params(params)
        async with httpx.AsyncClient() as http:
            resp = await http.put(
                f"{BINANCE_API_URL}/sapi/v1/userListenToken",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

    async def _run_token_refresh(self):
        while self._running:
            try:
                await asyncio.sleep(TOKEN_REFRESH_INTERVAL)
                if not self._running:
                    break
                await self._keepalive_listen_key()
                log.info("listen_token_refreshed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("listen_token_refresh_failed", error=str(e))

    # --- Price Streams ---

    async def _run_price_stream(self):
        backoff = 1
        connected_at: float | None = None

        while self._running:
            try:
                streams = [f"{s.lower()}@ticker" for s in self._subscribed_symbols if s]
                if not streams:
                    await asyncio.sleep(5)
                    continue

                stream_path = "/".join(streams)
                url = f"{BINANCE_WS_URL}/stream?streams={stream_path}"

                async with websockets.connect(url) as ws:
                    self._price_ws = ws
                    connected_at = time.monotonic()
                    backoff = 1
                    log.info("price_stream_connected", symbols=list(self._subscribed_symbols))

                    async for raw in ws:
                        if not self._running:
                            break
                        msg = json.loads(raw)
                        data = msg.get("data", {})
                        if data.get("e") == "24hrTicker":
                            await self._dispatch(EVENT_PRICE_UPDATE, data)
                        elif data.get("e") == "kline":
                            k = data.get("k", {})
                            await self._dispatch(EVENT_KLINE_UPDATE, {
                                "symbol": data.get("s"),
                                "interval": k.get("i"),
                                "open_time": k.get("t"),
                                "open": k.get("o"),
                                "high": k.get("h"),
                                "low": k.get("l"),
                                "close": k.get("c"),
                                "volume": k.get("v"),
                                "is_closed": k.get("x"),
                            })

                        if connected_at and (time.monotonic() - connected_at) > STABLE_CONNECTION_RESET:
                            backoff = 1
                            connected_at = time.monotonic()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                log.warning("price_stream_disconnected", error=str(e), reconnect_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

        self._price_ws = None


# Singleton
_manager = WSManager()

on = _manager.on
subscribe_symbol = _manager.subscribe_symbol
unsubscribe_symbol = _manager.unsubscribe_symbol
subscribe_kline = _manager.subscribe_kline
unsubscribe_kline = _manager.unsubscribe_kline


async def start():
    await _manager.start()


async def stop():
    await _manager.stop()
