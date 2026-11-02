"""Forex Factory weekly calendar (cached 1h) + optional Finnhub headlines.

Signals pause from `window` minutes before to `window` minutes after any high-impact USD event.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import requests

from ..config import CACHE_DIR, env

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_TTL = 3600
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "events": [], "error": ""}
_head_cache: dict = {"at": 0.0, "items": []}


def parse_events(raw: list) -> list[dict]:
    out = []
    for e in raw or []:
        try:
            ts = int(datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
        except (KeyError, ValueError, TypeError):
            continue
        out.append({"title": e.get("title", ""), "country": e.get("country", ""), "impact": e.get("impact", ""),
                    "t": ts, "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    return sorted(out, key=lambda x: x["t"])


def _disk_path():
    return CACHE_DIR / "ff_calendar.json"


def events(force: bool = False) -> list[dict]:
    """Cached for one hour in memory and on disk (so restarts don't hammer the feed)."""
    with _lock:
        if not force and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["events"]
        p = _disk_path()
        if not force and p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL:
            try:
                _cache.update(at=p.stat().st_mtime, events=parse_events(json.loads(p.read_text())), error="")
                return _cache["events"]
            except (ValueError, OSError):
                pass
        try:
            r = requests.get(FF_URL, timeout=15, headers={"User-Agent": "Mark1-dashboard/1.0"})
            r.raise_for_status()
            raw = r.json()
            CACHE_DIR.mkdir(exist_ok=True)
            p.write_text(json.dumps(raw))
            _cache.update(at=time.time(), events=parse_events(raw), error="")
        except Exception as e:  # noqa: BLE001
            # keep previous events; retry in 5 minutes rather than an hour
            _cache.update(at=time.time() - CACHE_TTL + 300, error=f"calendar fetch failed: {type(e).__name__}")
        return _cache["events"]


def high_usd(evts: list[dict]) -> list[dict]:
    return [e for e in evts if e["country"] == "USD" and e["impact"].lower() == "high"]


def blocking_event(now: int, evts: list[dict], window_min: int = 15) -> dict | None:
    w = window_min * 60
    for e in high_usd(evts):
        if e["t"] - w <= now <= e["t"] + w:
            return e
    return None


def status(now: int | None = None, window_min: int = 15, manual_pause: bool = False) -> dict:
    now = now or int(time.time())
    evts = events()
    block = blocking_event(now, evts, window_min)
    upcoming = [e for e in high_usd(evts) if e["t"] > now][:5]
    if manual_pause:
        state, text = "paused", "Paused manually"
    elif block:
        state, text = "paused", f"News pause: {block['title']} ({'in' if block['t'] > now else 'was'} " \
                                f"{abs(block['t'] - now) // 60} min)"
    else:
        state, text = "clear", "No high-impact USD news within ±%d min" % window_min
    return {"state": state, "text": text, "blocking": block, "upcoming": upcoming,
            "error": _cache.get("error", ""), "fetched_at": int(_cache["at"]) if _cache["at"] else None}


def headlines(limit: int = 8) -> list[dict]:
    """Optional Finnhub forex headlines (cached 10 min). Empty list if FINNHUB_KEY is unset."""
    key = env("FINNHUB_KEY")
    if not key:
        return []
    if time.time() - _head_cache["at"] < 600:
        return _head_cache["items"][:limit]
    try:
        r = requests.get("https://finnhub.io/api/v1/news", params={"category": "forex", "token": key}, timeout=10)
        r.raise_for_status()
        items = [{"headline": i.get("headline", ""), "source": i.get("source", ""), "url": i.get("url", ""),
                  "t": i.get("datetime", 0)} for i in r.json()]
        _head_cache.update(at=time.time(), items=items)
    except Exception:  # noqa: BLE001 — never surface the URL (it contains the key)
        _head_cache["at"] = time.time() - 300
    return _head_cache["items"][:limit]
