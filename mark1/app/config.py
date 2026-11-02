"""Configuration. Secrets come from .env and are never printed or logged.

Use `secret_present(name)` when you need to report whether a key is set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")  # also accept a .env one level up

DB_PATH = Path(os.getenv("MARK1_DB", ROOT / "mark1.db"))
CACHE_DIR = ROOT / "data_cache"

PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "BTCUSD"]
TIMEFRAMES = ["M1", "M5", "H1", "H4"]
TF_SECONDS = {"M1": 60, "M5": 300, "H1": 3600, "H4": 14400}
TF_UI = {"1m": "M1", "5m": "M5", "1h": "H1", "4h": "H4"}

# Instrument metadata: source, symbol at the source, pip size, default spread (price units)
INSTRUMENTS = {
    "XAUUSD": {"source": "oanda", "symbol": "XAU_USD", "pip": 0.1, "spread": 0.35, "digits": 2},
    "EURUSD": {"source": "oanda", "symbol": "EUR_USD", "pip": 0.0001, "spread": 0.00012, "digits": 5},
    "GBPUSD": {"source": "oanda", "symbol": "GBP_USD", "pip": 0.0001, "spread": 0.00016, "digits": 5},
    # OANDA v20 does not list BTC for most practice accounts; data comes from Binance public API.
    "BTCUSD": {"source": "binance", "symbol": "BTCUSDT", "pip": 1.0, "spread": 5.0, "digits": 2},
}


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def secret_present(name: str) -> bool:
    return bool(env(name))


def data_mode() -> str:
    """'synthetic' when explicitly asked or when no OANDA token exists, else 'live'."""
    if env("MARK1_DATA").lower() == "synthetic":
        return "synthetic"
    if not secret_present("OANDA_TOKEN"):
        return "synthetic"
    return "live"


def oanda_base() -> str:
    # Hard-wired to the practice host unless OANDA_ENV is explicitly something else.
    # Mark 1 never places orders on any host.
    if env("OANDA_ENV", "practice").lower() == "live":
        return "https://api-fxtrade.oanda.com"
    return "https://api-fxpractice.oanda.com"


@dataclass
class StrategyParams:
    """Tunable strategy parameters. Changed only via approved assistant suggestions."""
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_len: int = 14
    atr_len: int = 14
    reg_len: int = 20
    z_entry: float = 1.0          # pullback depth (regression z-score) needed on the 1m
    sl_atr: float = 1.5           # SL = sl_atr * ATR14 (5m ATR)
    rr: float = 2.0               # TP = rr * risk
    min_score: int = 55           # heuristic score threshold (0-100), NOT a probability
    news_window_min: int = 15
    trailing: bool = False        # trail stop by 1 ATR once price is +1R
    daily_loss_limit_r: float = 3.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        fields = cls.__dataclass_fields__
        clean = {}
        for k, v in (d or {}).items():
            if k in fields:
                typ = fields[k].type
                if typ in ("int", int):
                    v = int(v)
                elif typ in ("float", float):
                    v = float(v)
                elif typ in ("bool", bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                clean[k] = v
        return cls(**clean)


# Bounds used to validate assistant suggestions.
PARAM_BOUNDS = {
    "ema_fast": (5, 100), "ema_slow": (10, 300), "rsi_len": (2, 50), "atr_len": (5, 50),
    "reg_len": (10, 200), "z_entry": (0.25, 4.0), "sl_atr": (0.5, 5.0), "rr": (0.5, 6.0),
    "min_score": (0, 100), "news_window_min": (0, 120), "daily_loss_limit_r": (0.5, 20.0),
}
