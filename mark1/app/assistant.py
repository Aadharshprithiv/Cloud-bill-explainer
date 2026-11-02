"""Rule-based command assistant. It can change the VIEW directly, but strategy changes are only ever
SUGGESTED: each suggestion is backtested (baseline vs proposed, same data) and waits for Approve/Reject.
Nothing here writes strategy parameters; only the approve endpoint does, after a human click.
"""
from __future__ import annotations

import re
from dataclasses import replace

from . import db
from .backtest import engine as bt
from .config import PAIRS, PARAM_BOUNDS, TF_UI, StrategyParams

OVERLAYS = {"fvg": "fvg", "fair value": "fvg", "order block": "ob", "ob": "ob", "swing": "swings",
            "6-to-6": "range", "6 to 6": "range", "range": "range"}
ALIASES = [
    (r"min(imum)?\s*score|score\s*threshold|threshold|score", "min_score"),
    (r"z[- ]?score|z\b|pullback", "z_entry"),
    (r"stop|sl\b|stop[- ]?loss", "sl_atr"),
    (r"rr\b|reward|risk[- ]?reward|tp\b|take[- ]?profit", "rr"),
    (r"rsi", "rsi_len"),
    (r"ema\s*fast|fast\s*ema|ema\s*20", "ema_fast"),
    (r"ema\s*slow|slow\s*ema|ema\s*50", "ema_slow"),
    (r"atr", "atr_len"),
    (r"regression|reg\b", "reg_len"),
    (r"daily\s*(loss)?\s*limit", "daily_loss_limit_r"),
    (r"news\s*window", "news_window_min"),
]
HELP = ("Try: 'show FVG on 5m', 'hide order blocks', 'show 6-to-6 range on 1h', 'why this setup?', "
        "'set min score to 65', 'use z 1.5', 'set SL to 2 ATR', 'set RR to 2.5', 'trailing on', "
        "'optimize', 'backtest 3 months'. Strategy changes are suggestions with a backtest — you approve or reject.")


def _pair_in(text: str, default: str) -> str:
    for p in PAIRS:
        if p.lower() in text.replace("/", "").lower():
            return p
    for name, p in (("gold", "XAUUSD"), ("bitcoin", "BTCUSD"), ("btc", "BTCUSD"), ("euro", "EURUSD"),
                    ("cable", "GBPUSD"), ("pound", "GBPUSD")):
        if name in text.lower():
            return p
    return default


def parse(text: str, pair: str) -> dict:
    t = text.strip().lower()
    if not t or t in ("help", "?"):
        return {"kind": "help"}
    if "why" in t:
        return {"kind": "why", "pair": _pair_in(t, pair)}
    m = re.search(r"\b(show|hide|toggle)\b", t)
    if m:
        overlays = sorted({v for k, v in OVERLAYS.items() if k in t})
        tf = next((v for k, v in TF_UI.items() if re.search(rf"\b{k}\b", t)), None)
        if overlays or tf:
            return {"kind": "view", "show": m.group(1) != "hide", "overlays": overlays, "tf": tf,
                    "pair": _pair_in(t, pair)}
    if "optimi" in t or "improve" in t or "tune" in t:
        return {"kind": "optimize", "pair": _pair_in(t, pair)}
    if t.startswith("backtest") or "run backtest" in t:
        mo = re.search(r"(\d+)\s*(month|mo)", t)
        return {"kind": "backtest", "pair": _pair_in(t, pair), "months": int(mo.group(1)) if mo else 3,
                "walkforward": "walk" in t}
    if "trailing" in t:
        return {"kind": "change", "pair": _pair_in(t, pair), "changes": {"trailing": not ("off" in t or "disable" in t)}}
    nums = re.findall(r"-?\d+(?:\.\d+)?", t.replace("6-to-6", ""))
    num = nums[-1] if nums else None
    for pat, key in ALIASES:
        if re.search(pat, t) and num:
            cur = getattr(db.params(), key)
            val = float(num)
            if re.search(r"\b(raise|increase)\b.*\bby\b", t):
                val = cur + val
            elif re.search(r"\b(lower|decrease|reduce)\b.*\bby\b", t):
                val = cur - val
            val = int(round(val)) if isinstance(cur, int) and not isinstance(cur, bool) else val
            return {"kind": "change", "pair": _pair_in(t, pair), "changes": {key: val}}
    return {"kind": "unknown"}


def validate(changes: dict) -> str | None:
    for k, v in changes.items():
        if k in PARAM_BOUNDS:
            lo, hi = PARAM_BOUNDS[k]
            if not (lo <= v <= hi):
                return f"{k}={v} is outside the allowed range {lo}–{hi}."
    merged = StrategyParams.from_dict({**db.params().to_dict(), **changes})
    if merged.ema_fast >= merged.ema_slow:
        return "EMA fast must be shorter than EMA slow."
    return None


def compare(pair: str, changes: dict, m1, label: str) -> dict:
    """Backtest baseline vs proposed on identical data and store a pending suggestion."""
    base_p = db.params()
    prop_p = StrategyParams.from_dict({**base_p.to_dict(), **changes})
    base = bt.run(m1, base_p, pair)
    prop = bt.run(m1, prop_p, pair)
    keep = lambda r: {"summary": r["summary"], "verdict": r["verdict"], "equity": r["equity"][-400:]}  # noqa: E731
    sid = db.save_suggestion(label, changes, keep(base), keep(prop))
    notes = []
    if "news_window_min" in changes:
        notes.append("News window has no effect in backtests (no historical calendar).")
    if "daily_loss_limit_r" in changes or "trailing" in changes:
        notes.append("Backtest applies this rule too.")
    return {"kind": "suggestion", "id": sid, "pair": pair, "text": label, "changes": changes,
            "current": {k: getattr(base_p, k) for k in changes}, "baseline": keep(base), "proposed": keep(prop),
            "notes": notes, "status": "pending"}


def optimize(pair: str, m1) -> dict:
    """Tune z_entry/min_score on the first 70% of data, check on the last 30% (unseen)."""
    import itertools
    cut = int(m1["t"].iloc[0] + (m1["t"].iloc[-1] - m1["t"].iloc[0]) * 0.7)
    base_p = db.params()
    f = bt.prepare(m1, base_p, pair)
    from .engine import signals
    best, best_r = None, None
    for z, ms in itertools.product([0.75, 1.0, 1.5, 2.0], [45, 55, 65, 75]):
        p = replace(base_p, z_entry=z, min_score=ms)
        ev = signals.evaluate(f, p)
        s = bt.summarize(bt.simulate(ev[ev["t"] < cut], p, pair))
        if s["trades"] >= 20 and (best_r is None or s["total_r"] > best_r):
            best, best_r = {"z_entry": z, "min_score": ms}, s["total_r"]
    if not best or all(getattr(base_p, k) == v for k, v in best.items()):
        return {"kind": "message", "text": "No better z/score combination found on the tuning window — "
                                           "keeping current settings is the honest answer."}
    oos = m1[m1["t"] >= cut].reset_index(drop=True)
    res = compare(pair, best, oos, f"Optimize {pair}: tuned on first 70%, results below are the UNSEEN last 30%")
    res["notes"].append("Baseline vs proposed are both measured on the unseen 30% only.")
    return res
