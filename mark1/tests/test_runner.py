import time

import pandas as pd
import pytest

from app import db, runner
from app.config import StrategyParams


@pytest.fixture
def fake(monkeypatch):
    now = int(time.time())
    ct = now - now % 60 - 60
    sig = {"pair": "XAUUSD", "side": "BUY", "candle_t": ct, "entry": 100.0, "sl": 99.0, "tp": 102.0, "score": 70,
           "setup": "pullback+FVG", "alignment": {}, "checklist": []}
    m1 = pd.DataFrame({"t": [ct], "open": [100.0], "high": [100.5], "low": [99.6], "close": [100.2],
                       "volume": [1.0], "spread": [0.0]})
    monkeypatch.setattr(runner, "frames_for", lambda pair: {"M1": m1, "M5": m1, "H1": m1, "H4": m1})
    monkeypatch.setattr(runner.signals, "latest_signal", lambda *a, **k: dict(sig))
    monkeypatch.setattr(runner.market, "last_price", lambda pair: {"price": 100.2, "spread": 0.0, "t": ct + 60})
    runner._last_candle.clear()
    return {"now": now, "ct": ct, "m1": m1}


def gates(**kw):
    g = {"engine_running": True, "killed": False, "news_paused": False, "daily_limit_hit": False, "whatsapp": False}
    g.update(kw)
    return g


def test_opens_one_paper_trade(fake):
    s = runner.tick_pair("XAUUSD", StrategyParams(), gates(), fake["now"])
    assert s["action"] == "paper trade opened"
    assert len(db.open_trades()) == 1
    runner._last_candle.clear()                       # simulate restart: de-dupe must still hold
    s = runner.tick_pair("XAUUSD", StrategyParams(), gates(), fake["now"])
    assert len(db.open_trades()) == 1 and s["action"] != "paper trade opened"


@pytest.mark.parametrize("kw,word", [({"killed": True}, "killed"), ({"news_paused": True}, "news"),
                                     ({"daily_limit_hit": True}, "daily"), ({"engine_running": False}, "stopped")])
def test_gates_block(fake, kw, word):
    s = runner.tick_pair("XAUUSD", StrategyParams(), gates(**kw), fake["now"])
    assert s["action"].startswith("blocked") and word in s["action"]
    assert db.open_trades() == []


def test_stop_loss_closes_trade(fake):
    tid = db.open_trade("XAUUSD", "BUY", "x", 70, 100.0, 99.0, 102.0, fake["ct"])
    bars = pd.DataFrame({"t": [fake["ct"]], "open": [100.0], "high": [103.0], "low": [98.0], "close": [100.0],
                         "volume": [1.0], "spread": [0.0]})
    runner.manage_open_trades("XAUUSD", bars, StrategyParams(), fake["now"])
    t = [x for x in db.trades() if x["id"] == tid][0]
    assert t["status"] == "closed" and t["reason"] == "SL" and t["r"] == -1.0   # SL first
