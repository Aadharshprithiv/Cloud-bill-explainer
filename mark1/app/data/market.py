"""Single entry point for candles. Chooses OANDA / Binance / synthetic and caches in SQLite."""
from __future__ import annotations

import threading
import time

import pandas as pd

from .. import db
from ..config import INSTRUMENTS, TF_SECONDS, data_mode
from . import binance, oanda, synthetic
from .resample import resample

_mem: dict[tuple, tuple[float, pd.DataFrame]] = {}
_mem_lock = threading.Lock()
STATUS: dict[str, dict] = {}   # pair -> {"ok": bool, "error": str, "at": ts, "source": str}
MEM_TTL = 20


def _now() -> int:
    return int(time.time())


def source_of(pair: str) -> str:
    if data_mode() == "synthetic":
        return "synthetic"
    return INSTRUMENTS[pair]["source"]


_syn: dict[str, tuple[int, pd.DataFrame]] = {}


def _synthetic_m1(pair: str, end: int) -> pd.DataFrame:
    hit = _syn.get(pair)
    if hit and hit[0] == end:
        return hit[1]
    df = synthetic.m1(pair, end - 120 * 86400, end)
    _syn[pair] = (end, df)
    return df


def _fetch_recent(pair: str, tf: str, count: int) -> pd.DataFrame:
    src = source_of(pair)
    if src == "synthetic":
        step = TF_SECONDS[tf]
        end = _now() // 60 * 60
        span = count * step * (1.5 if pair != "BTCUSD" else 1.0) + 3 * 86400
        m = _synthetic_m1(pair, end)
        m = m[m["t"] >= end - span]
        return resample(m, tf).tail(count).reset_index(drop=True)
    sym = INSTRUMENTS[pair]["symbol"]
    if src == "oanda":
        return oanda.candles(sym, tf, count)
    return binance.candles(sym, tf, count)


def recent(pair: str, tf: str, count: int = 500) -> pd.DataFrame:
    """Latest *closed* candles. Falls back to the SQLite cache if the source fails."""
    key = (pair, tf, count, data_mode())
    with _mem_lock:
        hit = _mem.get(key)
        if hit and time.time() - hit[0] < MEM_TTL:
            return hit[1]
    try:
        df = _fetch_recent(pair, tf, count)
        if df["spread"].isna().all():
            df["spread"] = INSTRUMENTS[pair]["spread"]
        df["spread"] = df["spread"].fillna(INSTRUMENTS[pair]["spread"])
        STATUS[pair] = {"ok": True, "error": "", "at": _now(), "source": source_of(pair)}
        if source_of(pair) != "synthetic":
            db.save_candles(pair, tf, df)
    except Exception as e:  # noqa: BLE001 — messages from our clients never contain secrets
        STATUS[pair] = {"ok": False, "error": str(e)[:200], "at": _now(), "source": source_of(pair)}
        df = db.load_candles(pair, tf).tail(count).reset_index(drop=True)
    with _mem_lock:
        _mem[key] = (time.time(), df)
    return df


def history_m1(pair: str, days: int, end: int | None = None, progress=None) -> pd.DataFrame:
    """Up to ~6 months of M1 candles for backtesting, cached in SQLite between runs."""
    days = max(1, min(days, 186))
    end = (end or _now()) // 60 * 60
    start = end - days * 86400
    src = source_of(pair)
    if src == "synthetic":
        return synthetic.m1(pair, start, end)
    cached = db.load_candles(pair, "M1", start, end)
    sym = INSTRUMENTS[pair]["symbol"]
    fetch = oanda.candles_range if src == "oanda" else binance.candles_range
    pieces = [cached]
    if cached.empty:
        if progress:
            progress(f"downloading {days}d of M1 for {pair}")
        pieces.append(fetch(sym, "M1", start, end, 60))
    else:
        first, last = int(cached["t"].iloc[0]), int(cached["t"].iloc[-1])
        if first - start > 3 * 86400:  # missing head (allow a weekend)
            if progress:
                progress(f"downloading older M1 for {pair}")
            pieces.append(fetch(sym, "M1", start, first, 60))
        if end - last > 120:
            pieces.append(fetch(sym, "M1", last + 60, end, 60))
    df = pd.concat([p for p in pieces if not p.empty]) if any(not p.empty for p in pieces) else cached
    df = df.drop_duplicates("t").sort_values("t").reset_index(drop=True)
    df["spread"] = df["spread"].fillna(INSTRUMENTS[pair]["spread"])
    db.save_candles(pair, "M1", df)
    return df


def last_price(pair: str) -> dict:
    df = recent(pair, "M1", 5)
    if df.empty:
        return {"price": None, "spread": None, "t": None}
    r = df.iloc[-1]
    return {"price": float(r.close), "spread": float(r.spread), "t": int(r.t) + 60}


def verify_btc_on_oanda() -> dict:
    """Checks whether the OANDA practice account lists a BTC instrument."""
    if data_mode() == "synthetic":
        return {"checked": False, "available": None, "note": "synthetic mode — not checked"}
    try:
        names = oanda.account_instruments()
    except Exception as e:  # noqa: BLE001
        return {"checked": False, "available": None, "note": f"check failed: {e}"}
    btc = [n for n in names if n.startswith("BTC")]
    return {"checked": True, "available": bool(btc), "instruments": btc,
            "note": "BTC listed on OANDA account" if btc else "OANDA account has no BTC — using Binance"}
