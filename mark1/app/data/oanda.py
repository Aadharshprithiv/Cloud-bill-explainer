"""OANDA v20 REST (practice) — read-only candle and pricing requests.

There is deliberately no order-placement code anywhere in Mark 1.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

from ..config import env, oanda_base

MAX_COUNT = 5000


class OandaError(RuntimeError):
    pass


def _headers() -> dict:
    token = env("OANDA_TOKEN")
    if not token:
        raise OandaError("OANDA_TOKEN not set")
    return {"Authorization": f"Bearer {token}", "Accept-Datetime-Format": "UNIX"}


def _get(path: str, params: dict | None = None, timeout: float = 15) -> dict:
    try:
        r = requests.get(oanda_base() + path, headers=_headers(), params=params, timeout=timeout)
    except requests.RequestException as e:  # never include headers (token) in messages
        raise OandaError(f"network error: {type(e).__name__}") from None
    if r.status_code != 200:
        raise OandaError(f"HTTP {r.status_code} on {path.split('?')[0]}")
    return r.json()


def parse_candles(payload: dict) -> pd.DataFrame:
    rows = []
    for c in payload.get("candles", []):
        if not c.get("complete", True):
            continue
        bid, ask = c.get("bid"), c.get("ask")
        if bid and ask:
            o = (float(bid["o"]) + float(ask["o"])) / 2
            h = (float(bid["h"]) + float(ask["h"])) / 2
            low = (float(bid["l"]) + float(ask["l"])) / 2
            cl = (float(bid["c"]) + float(ask["c"])) / 2
            spread = float(ask["c"]) - float(bid["c"])
        else:
            m = c["mid"]
            o, h, low, cl = float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"])
            spread = float("nan")
        rows.append((int(float(c["time"])), o, h, low, cl, float(c.get("volume", 0)), spread))
    return pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "spread"])


def candles(symbol: str, tf: str, count: int = 500, start: int | None = None) -> pd.DataFrame:
    params = {"granularity": tf, "price": "BA"}
    if start is not None:
        params["from"] = str(start)
        params["count"] = str(min(count, MAX_COUNT))
    else:
        params["count"] = str(min(count, MAX_COUNT))
    return parse_candles(_get(f"/v3/instruments/{symbol}/candles", params))


def candles_range(symbol: str, tf: str, start: int, end: int, step_s: int) -> pd.DataFrame:
    """Page through history (5000 candles per request)."""
    frames, cur = [], start
    while cur < end:
        df = candles(symbol, tf, MAX_COUNT, start=cur)
        if df.empty:
            cur += MAX_COUNT * step_s
            continue
        frames.append(df)
        nxt = int(df["t"].iloc[-1]) + step_s
        if nxt <= cur:
            break
        cur = nxt
        if len(df) < 10 and cur < end - MAX_COUNT * step_s:
            cur += MAX_COUNT * step_s  # weekend gap
    if not frames:
        return parse_candles({})
    out = pd.concat(frames).drop_duplicates("t").sort_values("t")
    return out[out["t"] < end].reset_index(drop=True)


def account_instruments() -> list[str]:
    """Names of instruments tradable on the first practice account (used to verify BTC availability)."""
    accts = _get("/v3/accounts").get("accounts", [])
    if not accts:
        return []
    data = _get(f"/v3/accounts/{accts[0]['id']}/instruments")
    return [i["name"] for i in data.get("instruments", [])]


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())
