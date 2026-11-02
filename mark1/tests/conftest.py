import os

import numpy as np
import pandas as pd
import pytest

os.environ["MARK1_DATA"] = "synthetic"   # tests never touch the network or real keys
for k in ("OANDA_TOKEN", "TWILIO_SID", "TWILIO_TOKEN", "WHATSAPP_FROM", "WHATSAPP_TO", "FINNHUB_KEY"):
    os.environ[k] = ""


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    import time

    from app import db
    from app.data import news
    db.connect(tmp_path / "test.db")
    news._cache.update(at=time.time() + 10**9, events=[], error="")  # no network in tests
    yield
    db.connect(tmp_path / "other.db")


def make_m1(closes, start=1_767_225_600, spread=0.0, wick=0.0):
    """Build M1 candles from a close path (open = previous close)."""
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    t = start + np.arange(len(c)) * 60
    return pd.DataFrame({"t": t, "open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick,
                         "close": c, "volume": 1.0, "spread": spread})


@pytest.fixture
def synth_m1():
    from app.data import synthetic
    end = synthetic.ANCHOR + 60 * 86400
    return synthetic.m1("BTCUSD", end - 30 * 86400, end)
