"""Proof-panel statistics: paper-trade performance and past stats per setup type."""
from __future__ import annotations

from . import db
from .backtest.engine import summarize

MIN_SAMPLE = 100


def paper_summary() -> dict:
    closed = [t for t in db.trades(5000) if t["status"] == "closed" and t["r"] is not None]
    s = summarize([{"r": t["r"]} for t in reversed(closed)])
    s["win_loss_ratio"] = round(s["wins"] / s["losses"], 3) if s["losses"] else None
    s["open"] = len(db.open_trades())
    return s


def setup_stats(pair: str, setup: str | None) -> dict:
    """Stats for this setup type from the latest backtest of the pair plus closed paper trades."""
    bt = db.latest_backtest(pair)
    rows = []
    src = []
    if bt:
        rows += [t for t in bt["trades"] if not setup or t.get("setup") == setup]
        src.append(f"backtest #{bt['id']} ({bt['mode']})")
    paper = [t for t in db.trades(5000) if t["pair"] == pair and t["status"] == "closed"
             and (not setup or t["setup"] == setup)]
    if paper:
        rows += paper
        src.append("paper trades")
    n = len(rows)
    out = {"setup": setup or "all", "pair": pair, "trades": n, "sources": src,
           "win_rate": None, "avg_r": None, "warning": None}
    if n:
        r = [t["r"] for t in rows]
        out["win_rate"] = round(sum(1 for x in r if x > 0) / n, 4)
        out["avg_r"] = round(sum(r) / n, 4)
    if not bt:
        out["warning"] = "No backtest yet for this pair — run one to get past stats."
    elif n < MIN_SAMPLE:
        out["warning"] = f"Only {n} past trades of this setup type (<{MIN_SAMPLE}). Treat these stats as unreliable."
    return out
