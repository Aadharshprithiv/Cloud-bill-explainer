"""Trading sessions (UTC). London 07-16, New York 12-21 (approximate; not DST-adjusted)."""
from __future__ import annotations

import numpy as np
import pandas as pd

LONDON = (7, 16)
NEWYORK = (12, 21)


def session_name(ts: int) -> str:
    dt = pd.Timestamp(ts, unit="s", tz="UTC")
    if dt.weekday() == 5 or (dt.weekday() == 6 and dt.hour < 21) or (dt.weekday() == 4 and dt.hour >= 21):
        return "FX closed"
    h = dt.hour
    lon, ny = LONDON[0] <= h < LONDON[1], NEWYORK[0] <= h < NEWYORK[1]
    if lon and ny:
        return "London/NY overlap"
    if lon:
        return "London"
    if ny:
        return "New York"
    return "Asia"


def session_ok(ts: np.ndarray, pair: str) -> np.ndarray:
    """Allowed entry hours. FX/gold: London or NY. BTC: 24/7."""
    ts = np.asarray(ts)
    if pair == "BTCUSD":
        return np.ones(len(ts), dtype=bool)
    hour = (ts // 3600) % 24
    return (hour >= LONDON[0]) & (hour < NEWYORK[1])


def overlap(ts: np.ndarray) -> np.ndarray:
    hour = (np.asarray(ts) // 3600) % 24
    return (hour >= NEWYORK[0]) & (hour < LONDON[1])
