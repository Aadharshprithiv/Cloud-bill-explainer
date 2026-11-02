"""Vectorised indicators. Every value at index i uses only data up to and including bar i."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def linreg(s: pd.Series, n: int = 20) -> tuple[pd.Series, pd.Series]:
    """Rolling least-squares slope (price units per bar) and z-score of the last close vs the fitted line."""
    y = s.to_numpy(dtype=float)
    x = np.arange(n, dtype=float)
    sx, sxx = x.sum(), (x * x).sum()
    sy = pd.Series(y).rolling(n).sum().to_numpy()
    syy = pd.Series(y * y).rolling(n).sum().to_numpy()
    sxy = np.full(len(y), np.nan)
    if len(y) >= n:
        sxy[n - 1:] = np.convolve(y, x[::-1], mode="valid")  # sum_k k*y[t-n+1+k]
    denom = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    fitted_last = intercept + slope * (n - 1)
    ss_y = syy - sy * sy / n
    ss_x = sxx - sx * sx / n
    sse = np.maximum(ss_y - slope * slope * ss_x, 0)
    sd = np.sqrt(sse / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (y - fitted_last) / sd, 0.0)
    z[np.isnan(slope)] = np.nan
    return pd.Series(slope, index=s.index), pd.Series(z, index=s.index)


def add_all(df: pd.DataFrame, p) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], p.ema_fast)
    out["ema_slow"] = ema(out["close"], p.ema_slow)
    out["rsi"] = rsi(out["close"], p.rsi_len)
    out["atr"] = atr(out, p.atr_len)
    out["slope"], out["z"] = linreg(out["close"], p.reg_len)
    up = (out["ema_fast"] > out["ema_slow"]) & (out["slope"] > 0)
    dn = (out["ema_fast"] < out["ema_slow"]) & (out["slope"] < 0)
    out["trend"] = np.where(up, 1, np.where(dn, -1, 0))
    return out
