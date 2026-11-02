from app import alerts, assistant, db
from app.data import news


def test_news_window():
    evts = news.parse_events([
        {"title": "NFP", "country": "USD", "date": "2026-06-05T08:30:00-04:00", "impact": "High"},
        {"title": "ECB", "country": "EUR", "date": "2026-06-05T08:30:00-04:00", "impact": "High"},
        {"title": "Claims", "country": "USD", "date": "2026-06-05T10:00:00-04:00", "impact": "Low"},
    ])
    t = evts[0]["t"]
    assert news.blocking_event(t - 14 * 60, evts)["title"] == "NFP"
    assert news.blocking_event(t + 15 * 60, evts)
    assert news.blocking_event(t - 16 * 60, evts) is None
    assert news.blocking_event(t + 16 * 60, evts) is None


def test_alert_dedupe_sends_once():
    sent = []
    sig = {"pair": "XAUUSD", "side": "BUY", "candle_t": 123, "entry": 2400.0, "sl": 2395.0, "tp": 2410.0,
           "score": 72, "setup": "pullback+FVG"}
    sender = lambda body: (sent.append(body) or (True, "sent"))  # noqa: E731
    assert alerts.alert_signal(sig, True, sender)["sent"]
    assert alerts.alert_signal(sig, True, sender)["why"] == "duplicate"
    assert len(sent) == 1
    assert "XAUUSD BUY" in sent[0] and "Score 72/100" in sent[0] and "not a probability" in sent[0]


def test_alert_not_configured_is_safe():
    ok, why = alerts.send_whatsapp("x")
    assert not ok and "not configured" in why


def test_assistant_parse():
    assert assistant.parse("show FVG on 5m", "XAUUSD") == {"kind": "view", "show": True, "overlays": ["fvg"],
                                                           "tf": "M5", "pair": "XAUUSD"}
    assert assistant.parse("why this setup?", "EURUSD")["kind"] == "why"
    c = assistant.parse("set min score to 65", "XAUUSD")
    assert c == {"kind": "change", "pair": "XAUUSD", "changes": {"min_score": 65}}
    assert assistant.parse("set SL to 2 ATR", "XAUUSD")["changes"] == {"sl_atr": 2.0}
    assert assistant.parse("trailing off", "XAUUSD")["changes"] == {"trailing": False}
    assert assistant.validate({"rsi_len": 500})


def test_assistant_never_applies_changes(synth_m1):
    before = db.params().to_dict()
    res = assistant.compare("BTCUSD", {"min_score": 70}, synth_m1, "test")
    assert res["kind"] == "suggestion" and res["status"] == "pending"
    assert db.params().to_dict() == before                     # unchanged until approved
    assert "summary" in res["baseline"] and "summary" in res["proposed"]


def test_oanda_parser_uses_bid_ask_mid_and_skips_incomplete():
    from app.data import oanda
    payload = {"candles": [
        {"time": "1767225600.000000000", "complete": True, "volume": 10,
         "bid": {"o": "1.1000", "h": "1.1010", "l": "1.0990", "c": "1.1005"},
         "ask": {"o": "1.1002", "h": "1.1012", "l": "1.0992", "c": "1.1007"}},
        {"time": "1767225660.000000000", "complete": False, "volume": 3,
         "bid": {"o": "1", "h": "1", "l": "1", "c": "1"}, "ask": {"o": "1", "h": "1", "l": "1", "c": "1"}},
    ]}
    df = oanda.parse_candles(payload)
    assert len(df) == 1 and df["t"][0] == 1767225600
    assert abs(df["close"][0] - 1.1006) < 1e-12 and abs(df["spread"][0] - 0.0002) < 1e-12


def test_binance_parser():
    from app.data import binance
    df = binance.parse_klines([[1767225600000, "65000", "65100", "64900", "65050", "12.5", 0, 0, 0, 0, 0, 0]])
    assert df["t"][0] == 1767225600 and df["close"][0] == 65050.0


def test_resample_drops_incomplete_bucket():
    from app.data.resample import resample
    from .conftest import make_m1
    m1 = make_m1(range(1, 8), start=0)                 # 7 minutes -> one full 5m bar + partial
    out = resample(m1, "M5")
    assert len(out) == 1 and out["open"][0] == 1 and out["close"][0] == 5 and out["high"][0] == 5
