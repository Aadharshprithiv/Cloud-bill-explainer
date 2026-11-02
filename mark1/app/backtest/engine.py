"""Bar-by-bar backtester.

* Signal at the close of 1m bar i -> fill at the OPEN of bar i+1 (no lookahead).
* Costs: half the bar's spread on entry and on exit, plus slippage (in pips) on entry and on stop exits.
* If SL and TP are both inside one bar, the SL is assumed to fill first (conservative).
* One position per pair at a time. Daily loss limit (in R) blocks new entries for the rest of the UTC day.
* Optional trailing stop: once +1R, SL trails at 1 x ATR(5m) behind the best close.
* High-impact news is NOT filtered in backtests (no free historical calendar). This is shown in the UI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import INSTRUMENTS, StrategyParams
from ..data.resample import resample
from ..engine import signals


@dataclass
class Costs:
    slippage_pips: float = 0.5
    spread_override: float | None = None  # price units; None = use candle spread


def frames_from_m1(m1: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"M1": m1, "M5": resample(m1, "M5"), "H1": resample(m1, "H1"), "H4": resample(m1, "H4")}


def prepare(m1: pd.DataFrame, p: StrategyParams, pair: str) -> pd.DataFrame:
    f, _ = signals.build_features(frames_from_m1(m1), p, pair)
    return f


def simulate(ev: pd.DataFrame, p: StrategyParams, pair: str, costs: Costs | None = None) -> list[dict]:
    costs = costs or Costs()
    pip = INSTRUMENTS[pair]["pip"]
    slip = costs.slippage_pips * pip
    t = ev["t"].to_numpy()
    o, h, lo, c = (ev[k].to_numpy() for k in ("open", "high", "low", "close"))
    spread = (np.full(len(ev), costs.spread_override) if costs.spread_override is not None
              else ev["spread"].fillna(INSTRUMENTS[pair]["spread"]).to_numpy())
    side = ev["side"].to_numpy()
    sl_dist = ev["sl_dist"].to_numpy()
    atr5 = ev["atr_m5"].to_numpy()
    score = ev["score"].to_numpy()
    setup = ev["setup"].to_numpy()
    day = t // 86400

    trades: list[dict] = []
    i, N = 0, len(ev)
    day_r: dict[int, float] = {}
    while i < N - 1:
        s = side[i]
        if s == 0 or np.isnan(sl_dist[i]) or day_r.get(day[i], 0.0) <= -p.daily_loss_limit_r:
            i += 1
            continue
        j = i + 1                                   # fill on next bar's open
        half = spread[j] / 2
        entry = o[j] + s * (half + slip)            # buy at ask (+slip), sell at bid (-slip)
        risk = sl_dist[i]
        sl = entry - s * risk
        tp = entry + s * risk * p.rr
        best = entry
        exit_px, exit_t, reason = None, None, None
        k = j
        while k < N:
            # prices the position can be closed at: bid for longs, ask for shorts
            hk = h[k] - s * spread[k] / 2 if s == 1 else h[k] + spread[k] / 2
            lk = lo[k] - spread[k] / 2 if s == 1 else lo[k] + spread[k] / 2
            ok_ = o[k] - spread[k] / 2 if s == 1 else o[k] + spread[k] / 2
            if s == 1:
                if ok_ <= sl:                     # gap through stop
                    exit_px, reason = ok_ - slip, "SL (gap)"
                elif lk <= sl:
                    exit_px, reason = sl - slip, "SL"
                elif hk >= tp:
                    exit_px, reason = tp, "TP"
            else:
                if ok_ >= sl:
                    exit_px, reason = ok_ + slip, "SL (gap)"
                elif hk >= sl:
                    exit_px, reason = sl + slip, "SL"
                elif lk <= tp:
                    exit_px, reason = tp, "TP"
            if exit_px is not None:
                exit_t = t[k]
                break
            # trailing stop update uses this bar's close (applies from the next bar)
            if p.trailing and not np.isnan(atr5[k]):
                best = max(best, c[k]) if s == 1 else min(best, c[k])
                if s * (best - entry) >= risk:
                    new_sl = best - s * atr5[k]
                    sl = max(sl, new_sl) if s == 1 else min(sl, new_sl)
            k += 1
        if exit_px is None:                        # still open at the end of data
            k = N - 1
            exit_px, exit_t, reason = c[k] - s * spread[k] / 2, t[k], "end of data"
        r = s * (exit_px - entry) / risk
        if reason == "SL" and p.trailing and r > 0:
            reason = "trailing SL"
        trades.append({"pair": pair, "side": "BUY" if s == 1 else "SELL", "setup": setup[i], "score": int(score[i]),
                       "signal_t": int(t[i]), "entry_t": int(t[j]), "exit_t": int(exit_t),
                       "entry": float(entry), "sl": float(entry - s * risk), "tp": float(tp),
                       "exit": float(exit_px), "r": round(float(r), 4), "reason": reason})
        day_r[day[j]] = day_r.get(day[j], 0.0) + r
        i = k + 1                                  # flat again after the exit bar
    return trades


def summarize(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_r": 0.0, "total_r": 0.0,
                "max_drawdown_r": 0.0, "wins": 0, "losses": 0}
    r = np.array([tr["r"] for tr in trades])
    wins, losses = r[r > 0], r[r <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    mdd = float((peak - eq).max())
    pf = float(gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    return {"trades": n, "wins": int(len(wins)), "losses": int(len(losses)),
            "win_rate": round(float(len(wins) / n), 4), "profit_factor": round(pf, 3) if math.isfinite(pf) else None,
            "avg_r": round(float(r.mean()), 4), "total_r": round(float(r.sum()), 3),
            "max_drawdown_r": round(mdd, 3)}


def equity_curve(trades: list[dict]) -> list[dict]:
    eq, out = 0.0, []
    for tr in sorted(trades, key=lambda x: x["exit_t"]):
        eq += tr["r"]
        out.append({"time": tr["exit_t"], "value": round(eq, 4)})
    return out


def verdict(s: dict) -> dict:
    """Plain-language honesty about results. Never dresses up weak numbers."""
    warnings = []
    level = "ok"
    if s["trades"] < 30:
        warnings.append(f"Only {s['trades']} trades — far too few to conclude anything.")
        level = "weak"
    elif s["trades"] < 100:
        warnings.append(f"{s['trades']} trades is a small sample (<100); results can easily be luck.")
        level = "weak"
    pf = s.get("profit_factor")
    if s["trades"] and (pf is not None and pf < 1.0):
        warnings.append(f"Profit factor {pf} < 1: this configuration LOST money after costs.")
        level = "bad"
    elif s["trades"] and pf is not None and pf < 1.2:
        warnings.append(f"Profit factor {pf} is marginal; small cost changes could make it negative.")
        level = "weak" if level == "ok" else level
    if s["trades"] and s["max_drawdown_r"] > max(5.0, abs(s["total_r"])):
        warnings.append(f"Max drawdown {s['max_drawdown_r']}R is larger than the total gain.")
        level = "weak" if level == "ok" else level
    if not warnings:
        warnings.append("No red flags in this sample — still past performance only, not a forecast.")
    warnings.append("News filter not applied in backtests (no historical calendar).")
    return {"level": level, "notes": warnings}


def run(m1: pd.DataFrame, p: StrategyParams, pair: str, costs: Costs | None = None,
        features: pd.DataFrame | None = None) -> dict:
    f = features if features is not None else prepare(m1, p, pair)
    ev = signals.evaluate(f, p)
    trades = simulate(ev, p, pair, costs)
    s = summarize(trades)
    return {"summary": s, "verdict": verdict(s), "trades": trades, "equity": equity_curve(trades),
            "bars": int(len(m1)), "from": int(m1["t"].iloc[0]) if len(m1) else None,
            "to": int(m1["t"].iloc[-1]) if len(m1) else None}
