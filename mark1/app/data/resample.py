from __future__ import annotations

import pandas as pd

from ..config import TF_SECONDS


def resample(df: pd.DataFrame, tf: str, src_step: int = 60, drop_incomplete: bool = True) -> pd.DataFrame:
    """Aggregate candles (default M1) into tf. Buckets are aligned to UTC epoch multiples.

    The final bucket is dropped if it is not complete yet (no lookahead / no forming candles).
    """
    step = TF_SECONDS[tf]
    if df.empty or step == src_step:
        return df.copy()
    b = (df["t"] // step) * step
    g = df.groupby(b, sort=True)
    out = pd.DataFrame({
        "t": g["t"].first().index.values,
        "open": g["open"].first().values,
        "high": g["high"].max().values,
        "low": g["low"].min().values,
        "close": g["close"].last().values,
        "volume": g["volume"].sum().values,
        "spread": g["spread"].mean().values,
    })
    if drop_incomplete and len(out):
        last_src_end = int(df["t"].iloc[-1]) + src_step
        if out["t"].iloc[-1] + step > last_src_end:
            out = out.iloc[:-1]
    return out.reset_index(drop=True)
