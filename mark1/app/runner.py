"""Background engine loop (every 30 s, independent of any browser).

Each tick, per pair: refresh candles, evaluate the latest closed 1m candle, manage open PAPER trades,
and — if the engine is running and no safety gate is closed — log a paper trade and send one alert.
No real orders are ever sent anywhere.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from . import alerts, db
from .config import PAIRS, TF_SECONDS, StrategyParams
from .data import market, news
from .engine import signals, sessions

log = logging.getLogger("mark1.runner")
TICK_SECONDS = 30
COUNTS = {"M1": 600, "M5": 600, "H1": 300, "H4": 300}

SNAPSHOT: dict[str, dict] = {}          # pair -> latest signal + price
ENGINE: dict = {"last_tick": None, "tick_ms": None, "error": "", "ticks": 0}
_last_candle: dict[str, int] = {}
_tick_lock = threading.Lock()


def day_start(ts: int) -> int:
    return ts - ts % 86400


def gates(now: int, p: StrategyParams) -> dict:
    ui = db.ui_state()
    nstat = news.status(now, p.news_window_min, ui["news_pause_manual"])
    day_r = db.realized_r_since(day_start(now))
    return {
        "engine_running": bool(ui["engine_running"]),
        "killed": bool(ui["killed"]),
        "news_paused": nstat["state"] == "paused",
        "news": nstat,
        "daily_r": round(day_r, 3),
        "daily_limit_hit": day_r <= -p.daily_loss_limit_r,
        "whatsapp": bool(ui["whatsapp"]),
    }


def frames_for(pair: str) -> dict:
    return {tf: market.recent(pair, tf, n) for tf, n in COUNTS.items()}


def manage_open_trades(pair: str, m1, p: StrategyParams, now: int) -> list[dict]:
    """Apply SL/TP (SL checked first) and the optional trailing stop to open paper trades."""
    closed = []
    if m1.empty:
        return closed
    for tr in [t for t in db.open_trades() if t["pair"] == pair]:
        s = 1 if tr["side"] == "BUY" else -1
        risk = abs(tr["entry"] - tr["initial_sl"])
        sl = tr["sl"]
        bars = m1[m1["t"] >= tr["opened_at"]]
        for b in bars.itertuples():
            half = b.spread / 2
            lo_px = b.low - half if s == 1 else b.low + half
            hi_px = b.high - half if s == 1 else b.high + half
            if (s == 1 and lo_px <= sl) or (s == -1 and hi_px >= sl):
                db.close_trade(tr["id"], sl, int(b.t) + 60, "SL" if sl == tr["initial_sl"] else "trailing SL")
                closed.append(tr); break
            if (s == 1 and hi_px >= tr["tp"]) or (s == -1 and lo_px <= tr["tp"]):
                db.close_trade(tr["id"], tr["tp"], int(b.t) + 60, "TP")
                closed.append(tr); break
            if p.trailing and s * (b.close - tr["entry"]) >= risk:
                atr_px = risk / p.sl_atr
                new_sl = b.close - s * atr_px
                if (s == 1 and new_sl > sl) or (s == -1 and new_sl < sl):
                    sl = new_sl
                    db.x("UPDATE trades SET sl=? WHERE id=?", (sl, tr["id"]))
    return closed


def close_all(reason: str) -> int:
    n = 0
    for tr in db.open_trades():
        px = market.last_price(tr["pair"])["price"]
        if px is not None:
            db.close_trade(tr["id"], px, int(time.time()), reason)
            n += 1
    return n


def tick_pair(pair: str, p: StrategyParams, g: dict, now: int) -> dict:
    frames = frames_for(pair)
    sig = signals.latest_signal(frames, p, pair, news_block_now=g["news_paused"])
    lp = market.last_price(pair)
    sig.update(price=lp["price"], spread=lp["spread"], session=sessions.session_name(now),
               data=market.STATUS.get(pair, {}), source=market.source_of(pair))
    manage_open_trades(pair, frames["M1"], p, now)

    sig["action"] = "watch"
    ct = sig.get("candle_t")
    # only act on a candle that closed in the last few minutes, once per candle
    fresh = ct is not None and _last_candle.get(pair) != ct and now - (ct + TF_SECONDS["M1"]) <= 180
    if ct is not None:
        _last_candle[pair] = ct
    if sig["side"] in ("BUY", "SELL") and fresh:
        blocked = [k for k in ("killed", "news_paused", "daily_limit_hit") if g[k]]
        if not g["engine_running"]:
            blocked.append("engine stopped")
        if any(t["pair"] == pair for t in db.open_trades()):
            blocked.append("position already open")
        if blocked:
            sig["action"] = "blocked: " + ", ".join(blocked)
        else:
            # the alert claim doubles as the de-dupe for the paper trade (survives restarts)
            sig["alert"] = alerts.alert_signal(sig, g["whatsapp"])
            if sig["alert"]["why"] == "duplicate":
                sig["action"] = "already handled"
            else:
                db.open_trade(pair, sig["side"], sig["setup"], sig["score"], sig["entry"], sig["sl"], sig["tp"],
                              ct + TF_SECONDS["M1"])
                sig["action"] = "paper trade opened"
    SNAPSHOT[pair] = sig
    return sig


def tick() -> None:
    if not _tick_lock.acquire(blocking=False):
        return
    t0 = time.time()
    try:
        now = int(t0)
        p = db.params()
        g = gates(now, p)
        for pair in PAIRS:
            try:
                tick_pair(pair, p, g, now)
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed for %s", pair)
                SNAPSHOT.setdefault(pair, {"pair": pair, "side": "NO TRADE", "score": 0})["error"] = str(e)[:200]
        ENGINE.update(last_tick=now, tick_ms=int((time.time() - t0) * 1000), error="", gates=g)
        ENGINE["ticks"] += 1
    except Exception as e:  # noqa: BLE001
        log.exception("tick failed")
        ENGINE["error"] = str(e)[:200]
    finally:
        _tick_lock.release()


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(tick)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
