import numpy as np
import pandas as pd

from app.backtest import engine as bt
from app.config import StrategyParams
from app.engine import indicators, setups, signals

from .conftest import make_m1


def test_ema_rsi_atr_basic():
    s = pd.Series(np.arange(1, 101, dtype=float))
    assert indicators.ema(s, 20).iloc[-1] < 100
    assert indicators.rsi(s, 14).iloc[-1] == 100.0          # only gains
    down = pd.Series(np.arange(100, 0, -1, dtype=float))
    assert indicators.rsi(down, 14).iloc[-1] < 1
    df = make_m1(np.full(50, 10.0), wick=0.5)
    assert abs(indicators.atr(df, 14).iloc[-1] - 1.0) < 1e-9


def test_linreg_slope_and_zscore():
    line = pd.Series(np.arange(100) * 2.0 + 5)
    slope, z = indicators.linreg(line, 20)
    assert abs(slope.iloc[-1] - 2.0) < 1e-9
    assert abs(z.iloc[-1]) < 1e-6                        # perfect fit
    bumped = line.copy(); bumped.iloc[-1] -= 10
    _, z2 = indicators.linreg(bumped + np.sin(np.arange(100)), 20)
    assert z2.iloc[-1] < -2                               # last close far below the line


def test_fvg_detection():
    # bar2 low (12) above bar0 high (10.5) -> bullish gap [10.5, 12]
    df = pd.DataFrame({"t": [0, 60, 120, 180], "open": [10, 10.5, 11.5, 12.5], "high": [10.5, 11.6, 13, 13],
                       "low": [9.5, 10.4, 12, 12.2], "close": [10.4, 11.5, 12.8, 12.6]})
    out = setups.fvgs(df, pd.Series([0.5] * 4))
    z = out["zones"][0]
    assert z["dir"] == 1 and z["bottom"] == 10.5 and z["top"] == 12


def test_swings_confirmed_only_after_n_bars():
    highs = [1, 2, 5, 2, 1, 1, 1]
    df = pd.DataFrame({"t": range(7), "open": highs, "high": highs, "low": [h - 0.5 for h in highs], "close": highs})
    sw = setups.swings(df, n=2)
    assert sw["is_high"][2]
    assert np.isnan(sw["last_high"][3])                  # not known yet at bar 3
    assert sw["last_high"][4] == 5                        # known at bar 2 + n


def test_structure_bos_then_choch():
    sw = {"last_high": np.array([np.nan, 10, 10, 12, 12]), "last_low": np.array([np.nan, 8, 8, 8, 11])}
    df = pd.DataFrame({"close": [9, 9, 10.5, 11, 10.5]})
    st = setups.structure(df, sw)
    assert st["event"][2] == "BOS_up" and st["event"][4] == "CHoCH_down"


def test_six_to_six_range_and_fake_breakout():
    # New York 18:00 -> 06:00 is 22:00 -> 10:00 UTC in summer (EDT). Use 5m bars over one day.
    start = int(pd.Timestamp("2026-06-02 21:00", tz="UTC").timestamp())
    n = 12 * 24
    t = start + np.arange(n) * 300
    close = np.full(n, 100.0)
    hour_utc = ((t + 300) // 3600) % 24
    in_range = (hour_utc >= 22) | (hour_utc <= 10)
    close[in_range] = 100 + np.sin(np.arange(in_range.sum())) * 1.0   # range ~[99, 101]
    after = np.where(~in_range & (np.arange(n) > 50))[0]
    i0 = after[0]
    close[i0:i0 + 2] = 102          # breakout up
    close[i0 + 2:] = 100            # back inside quickly -> fake
    df = pd.DataFrame({"t": t, "open": close, "high": close + 0.1, "low": close - 0.1, "close": close})
    r = setups.six_to_six(df, 300)
    assert r["ranges"], "range should be recorded"
    assert r["state"][i0] == 1 and r["state"][i0 + 2] == 2


def test_score_is_bounded_and_signals_need_alignment(synth_m1):
    p = StrategyParams()
    ev = signals.evaluate(bt.prepare(synth_m1, p, "BTCUSD"), p)
    assert ev["score"].between(0, 100).all()
    sig = ev[ev["side"] != 0]
    assert len(sig) > 0
    for _, r in sig.iterrows():
        assert r["trend_h4"] == r["trend_h1"] == r["trend_m5"] == r["side"]
        assert r["score"] >= p.min_score


def test_no_lookahead_signals_identical_on_truncated_data(synth_m1):
    p = StrategyParams()
    full = signals.evaluate(bt.prepare(synth_m1, p, "BTCUSD"), p)
    cut = len(synth_m1) - 2500
    part = signals.evaluate(bt.prepare(synth_m1.iloc[:cut].reset_index(drop=True), p, "BTCUSD"), p)
    a = full.iloc[:cut].reset_index(drop=True)
    assert (a["side"].values == part["side"].values).all()
    assert (a["score"].values == part["score"].values).all()


def test_session_filter_fx_vs_btc():
    from app.engine import sessions
    t3utc = int(pd.Timestamp("2026-06-03 03:00", tz="UTC").timestamp())
    assert not sessions.session_ok(np.array([t3utc]), "EURUSD")[0]
    assert sessions.session_ok(np.array([t3utc]), "BTCUSD")[0]


def test_latest_signal_shape(synth_m1):
    from app.backtest.engine import frames_from_m1
    s = signals.latest_signal(frames_from_m1(synth_m1), StrategyParams(), "BTCUSD")
    assert s["side"] in ("BUY", "SELL", "NO TRADE")
    assert 0 <= s["score"] <= 100
    assert len(s["checklist"]) == 13
    assert set(s["alignment"]) == {"H4", "H1", "M5"}


def test_news_block_suppresses_signal(synth_m1):
    p = StrategyParams()
    f = bt.prepare(synth_m1, p, "BTCUSD")
    f["news_block"] = True
    assert (signals.evaluate(f, p)["side"] == 0).all()
