import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT_DIR / "data" / "ws_events"
RETENTION_DAYS = 7
MAX_BUFFER = 100

_buffer: deque[dict] = deque(maxlen=MAX_BUFFER)
_current_file = None
_current_date: str | None = None


async def start():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    log.info("event_recorder_started", log_dir=str(LOG_DIR))


async def stop():
    global _current_file
    if _current_file:
        _current_file.close()
        _current_file = None
    log.info("event_recorder_stopped")


def record(event_type: str, raw: dict):
    global _current_file, _current_date

    now = datetime.now(timezone.utc)
    entry = {
        "ts": now.isoformat(),
        "type": event_type,
        "raw": raw,
    }
    _buffer.append(entry)

    today = now.strftime("%Y-%m-%d")
    if today != _current_date:
        if _current_file:
            _current_file.close()
        _current_date = today
        filepath = LOG_DIR / f"{today}.jsonl"
        _current_file = open(filepath, "a", encoding="utf-8")

    if _current_file:
        try:
            _current_file.write(json.dumps(entry, default=str) + "\n")
            _current_file.flush()
        except Exception:
            log.error("event_record_write_failed", exc_info=True)


def get_recent(limit: int = 50) -> list[dict]:
    items = list(_buffer)
    return items[-limit:]


def get_today_file_size() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = LOG_DIR / f"{today}.jsonl"
    if filepath.exists():
        try:
            return filepath.stat().st_size
        except OSError:
            pass
    return 0


def _cleanup():
    if not LOG_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    removed = 0
    for f in LOG_DIR.glob("*.jsonl"):
        if f.stem < cutoff_str:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        log.info("event_recorder_cleanup", removed=removed)
