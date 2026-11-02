"""Price-action setup detection: swings, structure (BOS/CHoCH), FVG, order blocks, 6-to-6 range.

Rules for no lookahead: every per-bar feature at index i is computable at the close of bar i.
A swing point at bar j (n bars each side) only becomes known at bar j+n.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RANGE_TZ = ZoneInfo("America/New_York")  # 6-to-6 = 18:00 -> 06:00 New York time
RANGE_START_H, RANGE_END_H = 18, 6
FAKE_BARS = 6  # a breakout that closes back inside within this many bars is a fake breakout


def swings(df: pd.DataFrame, n: int = 2) -> dict:
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    N = len(df)
    sh = np.zeros(N, bool)
    sl = np.zeros(N, bool)
    for j in range(n, N - n):
        win_h, win_l = h[j - n:j + n + 1], lo[j - n:j + n + 1]
        if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
            sh[j] = True
        if lo[j] == win_l.min() and (win_l == lo[j]).sum() == 1:
            sl[j] = True
    # value known at bar i = last swing confirmed at or before i (swing j confirmed at j+n)
    last_sh = np.full(N, np.nan)
    last_sl = np.full(N, np.nan)
    cur_h = cur_l = np.nan
    for i in range(N):
        j = i - n
        if j >= 0 and sh[j]:
            cur_h = h[j]
        if j >= 0 and sl[j]:
            cur_l = lo[j]
        last_sh[i], last_sl[i] = cur_h, cur_l
    return {"is_high": sh, "is_low": sl, "last_high": last_sh, "last_low": last_sl}


def structure(df: pd.DataFrame, sw: dict) -> dict:
    """Bias flips on a close through the last confirmed swing. First break against bias = CHoCH, else BOS."""
    c = df["close"].to_numpy()
    N = len(df)
    bias = np.zeros(N, int)
    event = np.array([""] * N, dtype=object)
    b = 0
    broken_h = broken_l = np.nan
    for i in range(N):
        lh, ll = sw["last_high"][i], sw["last_low"][i]
        if not np.isnan(lh) and c[i] > lh and lh != broken_h:
            event[i] = "CHoCH_up" if b == -1 else "BOS_up"
            b, broken_h = 1, lh
        elif not np.isnan(ll) and c[i] < ll and ll != broken_l:
            event[i] = "CHoCH_down" if b == 1 else "BOS_down"
            b, broken_l = -1, ll
        bias[i] = b
    return {"bias": bias, "event": event}


def fvgs(df: pd.DataFrame, atr: pd.Series, min_atr: float = 0.1, max_active: int = 30) -> dict:
    """3-candle fair value gaps. touch_bull[i]: bar i traded into an unfilled bullish gap formed earlier."""
    h, lo, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    a = atr.to_numpy()
    t = df["t"].to_numpy()
    N = len(df)
    touch_bull = np.zeros(N, bool)
    touch_bear = np.zeros(N, bool)
    zones: list[dict] = []
    active: list[dict] = []
    for i in range(N):
        # 1) interaction with zones that existed before this bar
        still = []
        for z in active:
            if z["dir"] == 1:
                if lo[i] <= z["top"] and c[i] >= z["bottom"]:
                    touch_bull[i] = True
                if c[i] < z["bottom"]:
                    z["end"] = int(t[i]); continue
            else:
                if h[i] >= z["bottom"] and c[i] <= z["top"]:
                    touch_bear[i] = True
                if c[i] > z["top"]:
                    z["end"] = int(t[i]); continue
            still.append(z)
        active = still[-max_active:]
        # 2) new gap formed at the close of bar i
        if i >= 2 and not np.isnan(a[i]):
            if lo[i] > h[i - 2] and lo[i] - h[i - 2] >= min_atr * a[i]:
                z = {"dir": 1, "bottom": float(h[i - 2]), "top": float(lo[i]), "start": int(t[i - 2]), "end": None}
                zones.append(z); active.append(z)
            elif h[i] < lo[i - 2] and lo[i - 2] - h[i] >= min_atr * a[i]:
                z = {"dir": -1, "bottom": float(h[i]), "top": float(lo[i - 2]), "start": int(t[i - 2]), "end": None}
                zones.append(z); active.append(z)
    return {"touch_bull": touch_bull, "touch_bear": touch_bear, "zones": zones}


def order_blocks(df: pd.DataFrame, atr: pd.Series, body_atr: float = 1.2, max_active: int = 20) -> dict:
    """Bullish OB = last bearish candle before a strong bullish displacement that closes above it."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr.to_numpy()
    t = df["t"].to_numpy()
    N = len(df)
    touch_bull = np.zeros(N, bool)
    touch_bear = np.zeros(N, bool)
    zones, active = [], []
    for i in range(N):
        still = []
        for z in active:
            if z["dir"] == 1:
                if lo[i] <= z["top"] and c[i] >= z["bottom"]:
                    touch_bull[i] = True
                if c[i] < z["bottom"]:
                    z["end"] = int(t[i]); continue
            else:
                if h[i] >= z["bottom"] and c[i] <= z["top"]:
                    touch_bear[i] = True
                if c[i] > z["top"]:
                    z["end"] = int(t[i]); continue
            still.append(z)
        active = still[-max_active:]
        if i >= 1 and not np.isnan(a[i]):
            body = abs(c[i] - o[i])
            if c[i] > o[i] and c[i - 1] < o[i - 1] and body >= body_atr * a[i] and c[i] > h[i - 1]:
                z = {"dir": 1, "bottom": float(lo[i - 1]), "top": float(h[i - 1]), "start": int(t[i - 1]), "end": None}
                zones.append(z); active.append(z)
            elif c[i] < o[i] and c[i - 1] > o[i - 1] and body >= body_atr * a[i] and c[i] < lo[i - 1]:
                z = {"dir": -1, "bottom": float(lo[i - 1]), "top": float(h[i - 1]), "start": int(t[i - 1]), "end": None}
                zones.append(z); active.append(z)
    return {"touch_bull": touch_bull, "touch_bear": touch_bear, "zones": zones}


def six_to_six(df: pd.DataFrame, step: int) -> dict:
    """Range of 18:00->06:00 New York time; then breakout / fake-breakout state until 18:00.

    state[i]: 0 none/inside, 1 breakout up, -1 breakout down, 2 fake up (bearish), -2 fake down (bullish)
    """
    t = df["t"].to_numpy()
    h, lo, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    N = len(df)
    local = pd.to_datetime(t + step, unit="s", utc=True).tz_convert(RANGE_TZ)  # bar close time
    hour = local.hour.to_numpy() + local.minute.to_numpy() / 60.0
    # session date key: a range that starts 18:00 on day D belongs to day D+1
    day = (local + pd.Timedelta(hours=24 - RANGE_START_H)).date
    in_range = (hour >= RANGE_START_H) | (hour <= RANGE_END_H)
    state = np.zeros(N, int)
    rh = np.full(N, np.nan)
    rl = np.full(N, np.nan)
    ranges = []
    cur_day, hi, lw, start_t = None, -np.inf, np.inf, None
    done = False
    brk, brk_i = 0, -1
    for i in range(N):
        if day[i] != cur_day:
            cur_day, hi, lw, start_t, done, brk, brk_i = day[i], -np.inf, np.inf, int(t[i]), False, 0, -1
        if in_range[i] and not done:
            hi, lw = max(hi, h[i]), min(lw, lo[i])
            continue
        if not done:
            done = True
            if np.isfinite(hi):
                ranges.append({"start": start_t, "end": int(t[i]), "high": float(hi), "low": float(lw)})
        if not np.isfinite(hi):
            continue
        rh[i], rl[i] = hi, lw
        if brk in (0,):
            if c[i] > hi:
                brk, brk_i = 1, i
            elif c[i] < lw:
                brk, brk_i = -1, i
        elif brk == 1 and c[i] < hi and i - brk_i <= FAKE_BARS:
            brk = 2
        elif brk == -1 and c[i] > lw and i - brk_i <= FAKE_BARS:
            brk = -2
        state[i] = brk
    return {"state": state, "high": rh, "low": rl, "ranges": ranges}


def detect(df: pd.DataFrame, atr: pd.Series, step: int) -> dict:
    sw = swings(df)
    st = structure(df, sw)
    return {"swings": sw, "structure": st, "fvg": fvgs(df, atr), "ob": order_blocks(df, atr),
            "range": six_to_six(df, step)}
