import asyncio
import time
from collections import deque

_buffer: deque[dict] = deque(maxlen=500)
_subscribers: set[asyncio.Queue] = set()


def capture_processor(logger, method_name: str, event_dict: dict) -> dict:
    entry = {
        "timestamp": event_dict.get("timestamp", ""),
        "level": event_dict.get("level", "info"),
        "event": event_dict.get("event", ""),
        "context": {
            k: _safe_str(v)
            for k, v in event_dict.items()
            if k not in ("timestamp", "level", "event", "_record")
        },
        "seq": time.monotonic_ns(),
    }
    _buffer.append(entry)
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except Exception:
            pass
    return event_dict


def get_logs(limit: int = 100) -> list[dict]:
    items = list(_buffer)
    return items[-limit:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    _subscribers.discard(q)


def _safe_str(v) -> str:
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:
        return "?"
