import asyncio
from datetime import datetime, timezone
from xml.etree import ElementTree

import aiohttp
import structlog

from backend.config import settings

log = structlog.get_logger()

FEEDS = {
    "coindesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "lang": "en",
        "category": "crypto",
    },
    "cointelegraph": {
        "url": "https://cointelegraph.com/rss",
        "lang": "en",
        "category": "crypto",
    },
    "google_crypto": {
        "url": "https://news.google.com/rss/search?q=crypto+bitcoin+ethereum&hl=fr&gl=FR&ceid=FR:fr",
        "lang": "fr",
        "category": "crypto",
    },
    "google_macro": {
        "url": "https://news.google.com/rss/search?q=bourse+OR+fed+OR+tarifs+OR+inflation+OR+recession+OR+krach&hl=fr&gl=FR&ceid=FR:fr",
        "lang": "fr",
        "category": "macro",
    },
}

_news_cache: list[dict] = []
_fetched_at: datetime | None = None
_refresh_task: asyncio.Task | None = None
_translate_cache: dict[str, str] = {}
_TRANSLATE_CACHE_MAX = 500


async def start():
    global _refresh_task
    _refresh_task = asyncio.create_task(_run_refresh())
    log.info("news_tracker_started")


async def stop():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    log.info("news_tracker_stopped")


def get_news() -> dict:
    global _fetched_at
    is_stale = True
    if _fetched_at:
        age = (datetime.now(timezone.utc) - _fetched_at).total_seconds()
        is_stale = age > 900
    return {
        "items": _news_cache[:settings.news_max_items],
        "fetched_at": _fetched_at.isoformat() if _fetched_at else None,
        "is_stale": is_stale,
    }


async def _run_refresh():
    while True:
        try:
            await _fetch_all()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("news_fetch_failed", exc_info=True)
        await asyncio.sleep(settings.news_refresh_interval)


async def _fetch_all():
    global _news_cache, _fetched_at

    all_items = []
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20),
        headers={"User-Agent": "RootCoin/1.0"},
    ) as session:
        tasks = [_fetch_feed(session, name, feed) for name, feed in FEEDS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            log.warning("news_feed_error", error=str(result))
            continue
        all_items.extend(result)

    # Sort by date desc
    all_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    # Translate EN items
    en_items = [it for it in all_items if it.get("lang") == "en"]
    if en_items:
        await _translate_items(en_items)

    _news_cache = all_items
    _fetched_at = datetime.now(timezone.utc)
    log.info("news_refreshed", count=len(all_items))


async def _fetch_feed(session: aiohttp.ClientSession, name: str, feed: dict) -> list[dict]:
    async with session.get(feed["url"]) as resp:
        if resp.status != 200:
            log.warning("news_feed_http_error", feed=name, status=resp.status)
            return []
        text = await resp.text()

    items = []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        log.warning("news_feed_parse_error", feed=name)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item")[:15]:
        title = _text(item, "title") or ""
        link = _text(item, "link") or ""
        desc = _text(item, "description") or ""
        pub_date = _text(item, "pubDate") or ""
        source_el = item.find("source")
        source = source_el.text if source_el is not None and source_el.text else name

        # Parse category (CoinDesk has it)
        cat_el = item.find("category")
        category = cat_el.text if cat_el is not None and cat_el.text else feed["category"]

        # Clean Google News description (contains HTML)
        if "google.com" in feed["url"]:
            desc = ""

        # Parse date
        published_at = _parse_rss_date(pub_date)

        items.append({
            "title": title.strip(),
            "title_fr": title.strip() if feed["lang"] == "fr" else None,
            "description": desc.strip(),
            "description_fr": desc.strip() if feed["lang"] == "fr" else None,
            "link": link,
            "source": source,
            "category": category,
            "feed": name,
            "lang": feed["lang"],
            "published_at": published_at,
        })

    return items


async def _translate_items(items: list[dict]):
    loop = asyncio.get_event_loop()
    to_translate = []

    for it in items:
        if it["title"] and it["title"] not in _translate_cache:
            to_translate.append(it["title"])
        if it["description"] and it["description"] not in _translate_cache:
            to_translate.append(it["description"])

    if to_translate:
        translated = await loop.run_in_executor(None, _sync_translate_batch, to_translate)
        for orig, tr in zip(to_translate, translated):
            _translate_cache[orig] = tr
        # Evict oldest entries if cache exceeds max size
        if len(_translate_cache) > _TRANSLATE_CACHE_MAX:
            excess = len(_translate_cache) - _TRANSLATE_CACHE_MAX
            for _ in range(excess):
                _translate_cache.pop(next(iter(_translate_cache)))

    # Apply translations
    for it in items:
        if it["title"]:
            it["title_fr"] = _translate_cache.get(it["title"], it["title"])
        if it["description"]:
            it["description_fr"] = _translate_cache.get(it["description"], it["description"])


def _sync_translate_batch(texts: list[str]) -> list[str]:
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="en", target="fr")
        results = []
        # deep-translator batch max ~5000 chars, translate one by one for safety
        for text in texts:
            try:
                results.append(translator.translate(text[:500]))
            except Exception:
                results.append(text)
        return results
    except Exception:
        log.warning("translate_failed", exc_info=True)
        return texts


def _text(el: ElementTree.Element, tag: str) -> str | None:
    child = el.find(tag)
    return child.text if child is not None else None


def _parse_rss_date(date_str: str) -> str:
    if not date_str:
        return ""
    # RFC 822: "Sun, 22 Feb 2026 18:00:00 +0000" or "GMT"
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return date_str
