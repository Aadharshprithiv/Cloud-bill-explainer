import numpy as np
import pandas as pd

from app.backtest import engine as bt, walkforward as wf
from app.config import StrategyParams

from .conftest import make_m1


def _ev(n, side_at, sl_dist, o, h, lo, c, spread=0.0, atr=1.0):
    side = np.zeros(n, int); side[side_at] = 1
    return pd.DataFrame({"t": np.arange(n) * 60, "open": o, "high": h, "low": lo, "close": c, "spread": spread,
                         "side": side, "sl_dist": sl_dist, "atr_m5": atr, "score": 70, "setup": "pullback+plain"})


def test_fill_next_open_and_tp():
    n = 5
    o = np.array([100, 101, 101, 101, 101.0]); c = o.copy()
    h = np.array([100, 101, 101, 103.5, 101.0]); lo = np.array([100, 100.5, 100.5, 100.8, 100.5])
    tr = bt.simulate(_ev(n, 0, 1.0, o, h, lo, c), StrategyParams(rr=2), "BTCUSD", bt.Costs(slippage_pips=0))
    assert len(tr) == 1
    assert tr[0]["entry"] == 101 and tr[0]["entry_t"] == 60       # next bar's open, not signal close
    assert tr[0]["reason"] == "TP" and abs(tr[0]["r"] - 2.0) < 1e-9


def test_sl_checked_first_when_both_hit():
    n = 4
    o = np.full(n, 100.0); c = o.copy()
    h = np.array([100, 100, 105, 100.0]); lo = np.array([100, 100, 95, 100.0])  # bar 2 hits both
    tr = bt.simulate(_ev(n, 0, 1.0, o, h, lo, c), StrategyParams(), "BTCUSD", bt.Costs(slippage_pips=0))
    assert tr[0]["reason"] == "SL" and abs(tr[0]["r"] + 1) < 1e-9


def test_costs_make_losses_worse_and_targets_further():
    n = 4
    o = np.full(n, 100.0); c = o.copy()
    h = np.full(n, 100.0); lo = np.array([100, 100, 98.5, 100.0])   # stop-out bar
    free = bt.simulate(_ev(n, 0, 1.0, o, h, lo, c), StrategyParams(), "BTCUSD", bt.Costs(slippage_pips=0))
    paid = bt.simulate(_ev(n, 0, 1.0, o, h, lo, c, spread=0.2), StrategyParams(), "BTCUSD", bt.Costs(slippage_pips=0.1))
    assert paid[0]["entry"] > free[0]["entry"]          # bought at ask + slippage
    assert paid[0]["tp"] > free[0]["tp"]                # target needs a bigger move
    assert free[0]["r"] == -1 and paid[0]["r"] < -1     # exit slippage on the stop


def test_gap_through_stop_fills_at_open():
    n = 4
    o = np.array([100, 100, 97, 97.0]); c = o.copy(); h = o + 0.1; lo = o - 0.1
    tr = bt.simulate(_ev(n, 0, 1.0, o, h, lo, c), StrategyParams(), "BTCUSD", bt.Costs(slippage_pips=0))
    assert tr[0]["reason"] == "SL (gap)" and tr[0]["r"] < -1


def test_daily_loss_limit_blocks_entries():
    n = 40
    o = np.full(n, 100.0); c = o.copy(); h = o + 0.01; lo = o.copy()
    lo[2::3] = 98                                       # every third bar stops out longs
    side_at = list(range(0, 36, 3))
    ev = _ev(n, side_at, 1.0, o, h, lo, c)
    tr = bt.simulate(ev, StrategyParams(daily_loss_limit_r=3), "BTCUSD", bt.Costs(slippage_pips=0))
    assert len(tr) == 3


def test_summary_metrics():
    trades = [{"r": 2.0, "exit_t": 1}, {"r": -1.0, "exit_t": 2}, {"r": -1.0, "exit_t": 3}, {"r": 2.0, "exit_t": 4}]
    s = bt.summarize(trades)
    assert s["trades"] == 4 and s["win_rate"] == 0.5
    assert s["profit_factor"] == 2.0 and s["total_r"] == 2.0 and s["avg_r"] == 0.5
    assert s["max_drawdown_r"] == 2.0
    assert [p["value"] for p in bt.equity_curve(trades)] == [2, 1, 0, 2]


def test_verdict_is_honest_about_weak_results():
    v = bt.verdict({"trades": 40, "profit_factor": 0.8, "total_r": -5, "max_drawdown_r": 9})
    assert v["level"] == "bad"
    assert any("small sample" in n for n in v["notes"])
    assert any("LOST money" in n for n in v["notes"])


def test_random_walk_is_not_profitable():
    rng = np.random.default_rng(3)
    m1 = make_m1(2000 * np.exp(np.cumsum(rng.standard_normal(60000) * 0.0002)), spread=0.35, wick=0.1)
    r = bt.run(m1, StrategyParams(), "XAUUSD")
    assert r["summary"]["trades"] > 0
    assert r["summary"]["total_r"] < 0                   # no edge on noise after costs


def test_walkforward_runs_and_tests_on_unseen_windows(synth_m1):
    r = wf.run(synth_m1, StrategyParams(), "BTCUSD", train_days=10, test_days=5,
               grid={"z_entry": [1.0, 1.5], "min_score": [55]})
    assert len(r["folds"]) >= 3
    for f in r["folds"]:
        assert f["train"][1] == f["test"][0]             # test starts where training ends
    for t in r["trades"]:
        assert any(f["test"][0] <= t["signal_t"] < f["test"][1] for f in r["folds"])
