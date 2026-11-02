"""Walk-forward: tune on one window, test on the next unseen window, roll forward.

Only z_entry and min_score are tuned (they don't change indicator values, so features are computed once).
Indicators are causal, so computing them over the full history introduces no lookahead.
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import pandas as pd

from ..config import StrategyParams
from ..engine import signals
from . import engine as bt

GRID = {"z_entry": [0.75, 1.0, 1.5, 2.0], "min_score": [45, 55, 65]}
MIN_TRAIN_TRADES = 15


def _pick(results: list[tuple[dict, dict]]) -> dict:
    """Best params by total R among combos with enough trades; falls back to most trades."""
    enough = [r for r in results if r[1]["trades"] >= MIN_TRAIN_TRADES]
    pool = enough or results
    key = (lambda r: r[1]["total_r"]) if enough else (lambda r: r[1]["trades"])
    return max(pool, key=key)[0]


def run(m1: pd.DataFrame, p: StrategyParams, pair: str, train_days: int = 30, test_days: int = 14,
        costs: bt.Costs | None = None, grid: dict | None = None) -> dict:
    grid = grid or GRID
    f = bt.prepare(m1, p, pair)
    combos = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    evs = [(c, signals.evaluate(f, replace(p, **c))) for c in combos]
    t = f["t"].to_numpy()
    start, end = int(t[0]), int(t[-1])
    folds, oos_trades = [], []
    cur = start
    while cur + (train_days + test_days) * 86400 <= end + 86400:
        tr_lo, tr_hi = cur, cur + train_days * 86400
        te_lo, te_hi = tr_hi, tr_hi + test_days * 86400
        train_res = []
        for c, ev in evs:
            sl = ev[(ev["t"] >= tr_lo) & (ev["t"] < tr_hi)]
            train_res.append((c, bt.summarize(bt.simulate(sl, replace(p, **c), pair, costs))))
        best = _pick(train_res)
        best_train = next(s for c, s in train_res if c == best)
        ev = next(e for c, e in evs if c == best)
        te = ev[(ev["t"] >= te_lo) & (ev["t"] < te_hi)]
        test_trades = bt.simulate(te, replace(p, **best), pair, costs)
        oos_trades += test_trades
        folds.append({"train": [tr_lo, tr_hi], "test": [te_lo, te_hi], "params": best,
                      "train_summary": best_train, "test_summary": bt.summarize(test_trades)})
        cur += test_days * 86400
    s = bt.summarize(oos_trades)
    v = bt.verdict(s)
    if folds:
        tr_r = sum(fd["train_summary"]["total_r"] for fd in folds)
        if tr_r > 0 and s["total_r"] <= 0:
            v["notes"].insert(0, f"Overfitting warning: tuned windows made {tr_r:.1f}R but unseen windows made "
                                 f"{s['total_r']:.1f}R.")
            v["level"] = "bad"
    else:
        v["notes"].insert(0, "Not enough data for even one train+test fold.")
        v["level"] = "weak"
    return {"summary": s, "verdict": v, "trades": oos_trades, "equity": bt.equity_curve(oos_trades),
            "folds": folds, "train_days": train_days, "test_days": test_days, "bars": int(len(m1))}
