"""Deterministic synthetic candles for tests and offline demo mode.

These are NOT market data. The UI shows a SYNTHETIC badge whenever they are used.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ANCHOR = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
BASE_PRICE = {"XAUUSD": 2650.0, "EURUSD": 1.0850, "GBPUSD": 1.2700, "BTCUSD": 65000.0}
VOL = {"XAUUSD": 0.00022, "EURUSD": 0.00009, "GBPUSD": 0.00011, "BTCUSD": 0.0007}
SPREAD = {"XAUUSD": 0.35, "EURUSD": 0.00012, "GBPUSD": 0.00016, "BTCUSD": 5.0}


def fx_open(ts: np.ndarray) -> np.ndarray:
    """FX market hours: closed Fri 21:00 UTC -> Sun 21:00 UTC."""
    dt = pd.to_datetime(ts, unit="s", utc=True)
    wd, hr = dt.weekday.values, dt.hour.values
    closed = (wd == 5) | ((wd == 4) & (hr >= 21)) | ((wd == 6) & (hr < 21))
    return ~closed


def m1(pair: str, start: int, end: int, seed: int | None = None) -> pd.DataFrame:
    """M1 candles covering [start, end). Same seed + pair -> same series (anchored at ANCHOR)."""
    s0 = min(start, ANCHOR) // 60 * 60
    ts = np.arange(s0, end // 60 * 60, 60, dtype=np.int64)
    n = len(ts)
    rng = np.random.default_rng(seed if seed is not None else zlib.crc32(pair.encode()))
    vol = VOL[pair]
    # Regime-switching drift so trends and pullbacks exist.
    drift = np.empty(n)
    i = 0
    while i < n:
        length = int(rng.exponential(900)) + 60
        drift[i:i + length] = rng.normal(0, vol * 0.08)
        i += length
    # Intraday volatility cycle (busier in London/NY)
    hour = (ts // 3600) % 24
    vol_mult = np.where((hour >= 7) & (hour < 21), 1.25, 0.6)
    rets = drift + rng.standard_normal(n) * vol * vol_mult
    # mean reversion wiggle on top of drift
    logp = np.cumsum(rets)
    # pull the long-run level back toward the base price (keeps prices plausible over months)
    slow = pd.Series(logp).ewm(span=30000, adjust=False).mean().to_numpy()
    closes = BASE_PRICE[pair] * np.exp(logp - 0.9 * slow)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    wick = np.abs(rng.standard_normal((2, n))) * vol * vol_mult * closes * 0.6
    highs = np.maximum(opens, closes) + wick[0]
    lows = np.minimum(opens, closes) - wick[1]
    df = pd.DataFrame({"t": ts, "open": opens, "high": highs, "low": lows, "close": closes,
                       "volume": rng.integers(20, 400, n).astype(float),
                       "spread": np.full(n, SPREAD[pair])})
    if pair != "BTCUSD":
        df = df[fx_open(df["t"].values)]
    df = df[(df["t"] >= start) & (df["t"] < end)]
    return df.reset_index(drop=True)
