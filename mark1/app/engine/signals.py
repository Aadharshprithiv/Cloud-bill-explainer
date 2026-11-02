"""Signal engine.

Rule (per pair):
  * 4h, 1h and 5m trends agree (trend = EMA20 vs EMA50 + regression slope sign).
  * 1m entry: a regression pullback (z-score <= -z_entry for longs, >= +z_entry for shorts within the
    last 3 bars) and RSI turning back in the trend direction, on a candle closing in that direction.
  * Session: London/NY for FX & gold, 24/7 for BTC. No entries inside a high-impact USD news window.
  * SL = sl_atr x ATR14 (5m), TP = rr x risk.
  * Score: heuristic 0-100 built from the checklist. It is NOT a probability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TF_SECONDS, StrategyParams
from . import indicators, sessions, setups


def _with_close(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    out = df.copy()
    out["close_t"] = out["t"] + TF_SECONDS[tf]
    return out


def _asof(base: pd.DataFrame, other: pd.DataFrame, cols: list[str], suffix: str) -> pd.DataFrame:
    """Attach the most recent *closed* higher-timeframe values to each base bar (no lookahead)."""
    o = other[["close_t"] + cols].rename(columns={c: f"{c}_{suffix}" for c in cols})
    return pd.merge_asof(base.sort_values("close_t"), o.sort_values("close_t"), on="close_t",
                         direction="backward")


def build_features(frames: dict[str, pd.DataFrame], p: StrategyParams, pair: str,
                   news_block: np.ndarray | None = None) -> tuple[pd.DataFrame, dict]:
    """frames: {'M1','M5','H1','H4'} -> candles. Returns M1 feature frame + 5m setup objects for overlays."""
    m1 = _with_close(indicators.add_all(frames["M1"], p), "M1")
    m5 = _with_close(indicators.add_all(frames["M5"], p), "M5")
    h1 = _with_close(indicators.add_all(frames["H1"], p), "H1")
    h4 = _with_close(indicators.add_all(frames["H4"], p), "H4")

    st = setups.detect(m5, m5["atr"], TF_SECONDS["M5"])
    m5["fvg_bull"], m5["fvg_bear"] = st["fvg"]["touch_bull"], st["fvg"]["touch_bear"]
    m5["ob_bull"], m5["ob_bear"] = st["ob"]["touch_bull"], st["ob"]["touch_bear"]
    m5["bias"] = st["structure"]["bias"]
    m5["event"] = st["structure"]["event"]
    m5["range_state"] = st["range"]["state"]
    m5["range_high"], m5["range_low"] = st["range"]["high"], st["range"]["low"]

    f = _asof(m1, h4, ["trend"], "h4")
    f = _asof(f, h1, ["trend"], "h1")
    f = _asof(f, m5, ["trend", "atr", "fvg_bull", "fvg_bear", "ob_bull", "ob_bear", "bias", "event",
                      "range_state", "range_high", "range_low"], "m5")
    f = f.reset_index(drop=True)
    f["session_ok"] = sessions.session_ok(f["t"].to_numpy(), pair)
    f["overlap"] = sessions.overlap(f["t"].to_numpy())
    f["news_block"] = news_block if news_block is not None else False
    return f, st


def evaluate(f: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Vectorised per-bar decision. Adds side (+1/-1/0), score, setup, sl_dist columns."""
    th4, th1, tm5 = (f[c].fillna(0).to_numpy() for c in ("trend_h4", "trend_h1", "trend_m5"))
    aligned_up = (th4 == 1) & (th1 == 1) & (tm5 == 1)
    aligned_dn = (th4 == -1) & (th1 == -1) & (tm5 == -1)

    z = f["z"].to_numpy()
    zmin = pd.Series(z).shift(1).rolling(3, min_periods=1).min().to_numpy()
    zmax = pd.Series(z).shift(1).rolling(3, min_periods=1).max().to_numpy()
    rsi = f["rsi"].to_numpy()
    rsi_prev = np.roll(rsi, 1); rsi_prev[0] = np.nan
    rsi_d = rsi - rsi_prev
    o, c = f["open"].to_numpy(), f["close"].to_numpy()

    pull_up = zmin <= -p.z_entry
    pull_dn = zmax >= p.z_entry
    turn_up = (rsi_d > 0) & (rsi_prev < 50)
    turn_dn = (rsi_d < 0) & (rsi_prev > 50)
    candle_up, candle_dn = c > o, c < o

    sess = f["session_ok"].to_numpy(bool)
    news = np.asarray(f["news_block"], dtype=bool)
    atr5 = f["atr_m5"].to_numpy()
    valid = ~np.isnan(atr5) & (atr5 > 0) & ~np.isnan(z) & ~np.isnan(rsi_d)

    fvg_b, fvg_s = f["fvg_bull_m5"].fillna(False).to_numpy(bool), f["fvg_bear_m5"].fillna(False).to_numpy(bool)
    ob_b, ob_s = f["ob_bull_m5"].fillna(False).to_numpy(bool), f["ob_bear_m5"].fillna(False).to_numpy(bool)
    bias = f["bias_m5"].fillna(0).to_numpy()
    rstate = f["range_state_m5"].fillna(0).to_numpy()
    overlap = f["overlap"].to_numpy(bool)

    def score(dirn: int) -> np.ndarray:
        if dirn == 1:
            depth, turn, fv, ob, rs = -zmin, rsi_d, fvg_b, ob_b, (rstate == 1) | (rstate == -2)
        else:
            depth, turn, fv, ob, rs = zmax, -rsi_d, fvg_s, ob_s, (rstate == -1) | (rstate == 2)
        s = 30.0  # trend alignment (required)
        s += 15 * np.clip(np.nan_to_num(depth) / 2.0, 0, 1)
        s += 10 * np.clip(np.nan_to_num(turn) / 5.0, 0, 1)
        s += 10 * fv + 10 * ob + 10 * (bias == dirn)
        s += np.where(overlap, 10, 6)
        s += 5 * rs
        return np.round(s).astype(int)

    s_up, s_dn = score(1), score(-1)
    base = sess & ~news & valid
    long_ok = base & aligned_up & pull_up & turn_up & candle_up & (s_up >= p.min_score)
    short_ok = base & aligned_dn & pull_dn & turn_dn & candle_dn & (s_dn >= p.min_score)

    out = f.copy()
    out["side"] = np.where(long_ok, 1, np.where(short_ok, -1, 0))
    out["score"] = np.where(aligned_dn & ~aligned_up, s_dn, s_up)
    out["score"] = np.where(long_ok, s_up, np.where(short_ok, s_dn, out["score"]))
    out["sl_dist"] = p.sl_atr * atr5
    tag = np.full(len(out), "plain", dtype=object)
    # primary confluence tag (most specific first) — used to group past stats
    for cond_b, cond_s, name in ((ob_b, ob_s, "OB"), (fvg_b, fvg_s, "FVG"),
                                 (bias == 1, bias == -1, "structure"),
                                 ((rstate == 1) | (rstate == -2), (rstate == -1) | (rstate == 2), "range")):
        pick = (tag == "plain") & (((out["side"] == 1) & cond_b) | ((out["side"] == -1) & cond_s))
        tag[pick] = name
    out["setup"] = np.where(out["side"] != 0, "pullback+" + tag.astype(str), "")
    return out


def checklist(row: pd.Series, p: StrategyParams, pair: str) -> tuple[int, list[dict]]:
    """Human-readable checklist for the latest bar. Direction = 4h trend (or 1h if 4h is flat)."""
    d = int(row.get("trend_h4") or 0) or int(row.get("trend_h1") or 0) or 1
    word = "up" if d == 1 else "down"
    z_ok = (row["z_prev_min"] <= -p.z_entry) if d == 1 else (row["z_prev_max"] >= p.z_entry)
    rsi_ok = (row["rsi_d"] > 0 and row["rsi_prev"] < 50) if d == 1 else (row["rsi_d"] < 0 and row["rsi_prev"] > 50)
    rs = int(row.get("range_state_m5") or 0)
    items = [
        ("4h trend " + word, int(row.get("trend_h4") or 0) == d),
        ("1h trend " + word, int(row.get("trend_h1") or 0) == d),
        ("5m trend " + word, int(row.get("trend_m5") or 0) == d),
        (f"1m regression pullback (|z| ≥ {p.z_entry})", bool(z_ok)),
        ("1m RSI turning " + word, bool(rsi_ok)),
        ("1m candle closes " + word, (row["close"] > row["open"]) if d == 1 else (row["close"] < row["open"])),
        ("Session OK" + (" (BTC 24/7)" if pair == "BTCUSD" else " (London/NY)"), bool(row["session_ok"])),
        ("No high-impact USD news window", not bool(row["news_block"])),
        ("Touching 5m FVG (" + word + ")", bool(row.get("fvg_bull_m5" if d == 1 else "fvg_bear_m5"))),
        ("Touching 5m order block (" + word + ")", bool(row.get("ob_bull_m5" if d == 1 else "ob_bear_m5"))),
        ("5m structure bias " + word + " (BOS/CHoCH)", int(row.get("bias_m5") or 0) == d),
        ("6-to-6 range breakout/fake-out favours " + word, rs in ((1, -2) if d == 1 else (-1, 2))),
        (f"Score ≥ {p.min_score}", int(row["score"]) >= p.min_score),
    ]
    required = 8  # first 8 items are hard rules; the rest add to the score
    return d, [{"item": n, "ok": bool(ok), "required": i < required} for i, (n, ok) in enumerate(items)]


def latest_signal(frames: dict[str, pd.DataFrame], p: StrategyParams, pair: str,
                  news_block_now: bool = False) -> dict:
    """Evaluate the most recent closed 1m candle and package it for the UI / alerts."""
    if any(frames[k].empty for k in ("M1", "M5", "H1", "H4")) or len(frames["H4"]) < p.ema_slow + 2:
        return {"pair": pair, "side": "NO TRADE", "score": 0, "reason": "not enough data", "checklist": [],
                "alignment": {}}
    nb = np.zeros(len(frames["M1"]), bool)
    if news_block_now:
        nb[-1] = True
    f, _ = build_features(frames, p, pair, nb)
    ev = evaluate(f, p)
    z = ev["z"]
    ev["z_prev_min"] = z.shift(1).rolling(3, min_periods=1).min()
    ev["z_prev_max"] = z.shift(1).rolling(3, min_periods=1).max()
    ev["rsi_prev"] = ev["rsi"].shift(1)
    ev["rsi_d"] = ev["rsi"] - ev["rsi_prev"]
    row = ev.iloc[-1]
    d, items = checklist(row, p, pair)
    side = int(row["side"])
    entry = float(row["close"])
    sl_dist = float(row["sl_dist"]) if not np.isnan(row["sl_dist"]) else None
    out = {
        "pair": pair,
        "candle_t": int(row["t"]),
        "side": {1: "BUY", -1: "SELL"}.get(side, "NO TRADE"),
        "direction_checked": "up" if d == 1 else "down",
        "score": int(row["score"]),
        "setup": row["setup"] or None,
        "entry": entry,
        "sl": None, "tp": None, "rr": p.rr,
        "checklist": items,
        "alignment": {"H4": int(row.get("trend_h4") or 0), "H1": int(row.get("trend_h1") or 0),
                      "M5": int(row.get("trend_m5") or 0)},
        "rsi": float(row["rsi"]) if not np.isnan(row["rsi"]) else None,
        "z": float(row["z"]) if not np.isnan(row["z"]) else None,
        "atr_m5": float(row["atr_m5"]) if not np.isnan(row["atr_m5"]) else None,
    }
    a = out["alignment"]
    out["all_aligned"] = a["H4"] == a["H1"] == a["M5"] != 0
    if sl_dist:
        s = 1 if side == 1 else -1 if side == -1 else (1 if d == 1 else -1)
        out["sl"] = entry - s * sl_dist
        out["tp"] = entry + s * sl_dist * p.rr
    return out
