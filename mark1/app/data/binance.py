"""Binance public klines for BTCUSD (BTCUSDT). No API key needed."""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://api.binance.com"
INTERVAL = {"M1": "1m", "M5": "5m", "H1": "1h", "H4": "4h"}
MAX_LIMIT = 1000


class BinanceError(RuntimeError):
    pass


def parse_klines(rows: list) -> pd.DataFrame:
    out = [(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float("nan"))
           for r in rows]
    return pd.DataFrame(out, columns=["t", "open", "high", "low", "close", "volume", "spread"])


def _klines(symbol: str, tf: str, limit: int, start: int | None = None) -> list:
    params = {"symbol": symbol, "interval": INTERVAL[tf], "limit": min(limit, MAX_LIMIT)}
    if start is not None:
        params["startTime"] = start * 1000
    try:
        r = requests.get(BASE + "/api/v3/klines", params=params, timeout=15)
    except requests.RequestException as e:
        raise BinanceError(f"network error: {type(e).__name__}") from None
    if r.status_code != 200:
        raise BinanceError(f"HTTP {r.status_code}")
    return r.json()


def candles(symbol: str, tf: str, count: int = 500) -> pd.DataFrame:
    df = parse_klines(_klines(symbol, tf, count))
    return df.iloc[:-1].reset_index(drop=True) if len(df) else df  # drop the still-forming candle


def candles_range(symbol: str, tf: str, start: int, end: int, step_s: int) -> pd.DataFrame:
    frames, cur = [], start
    while cur < end:
        rows = _klines(symbol, tf, MAX_LIMIT, start=cur)
        if not rows:
            break
        df = parse_klines(rows)
        frames.append(df)
        nxt = int(df["t"].iloc[-1]) + step_s
        if nxt <= cur:
            break
        cur = nxt
    if not frames:
        return parse_klines([])
    out = pd.concat(frames).drop_duplicates("t").sort_values("t")
    return out[out["t"] < end].reset_index(drop=True)
