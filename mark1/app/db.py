"""SQLite storage: settings, candle cache, paper trade log, alerts (dedupe), backtests, suggestions."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candles (
    pair TEXT, tf TEXT, t INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, spread REAL,
    PRIMARY KEY (pair, tf, t));
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, side TEXT, setup TEXT, score INTEGER,
    entry REAL, sl REAL, tp REAL, initial_sl REAL, opened_at INTEGER, closed_at INTEGER,
    exit REAL, r REAL, status TEXT, reason TEXT, source TEXT DEFAULT 'paper');
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY, pair TEXT, side TEXT, candle_t INTEGER, sent_at INTEGER, channel TEXT,
    status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER, pair TEXT, mode TEXT,
    params TEXT, summary TEXT, trades TEXT, equity TEXT);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER, text TEXT, changes TEXT,
    baseline TEXT, proposed TEXT, status TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or reopen) the database. Tests pass a temp path."""
    global _conn, _path
    with _lock:
        p = Path(path) if path else config.DB_PATH
        if _conn is not None and _path == p:
            return _conn
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(p, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _path = p
        return _conn


def conn() -> sqlite3.Connection:
    return _conn or connect()


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def x(sql: str, args: tuple = ()) -> int:
    with _lock:
        c = conn().execute(sql, args)
        conn().commit()
        return c.lastrowid


# ---------------- settings ----------------
DEFAULT_UI = {"sound": True, "whatsapp": False, "engine_running": False, "news_pause_manual": False,
              "killed": False}


def get_setting(key: str, default=None):
    rows = q("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(rows[0]["value"]) if rows else default


def set_setting(key: str, value) -> None:
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


def ui_state() -> dict:
    return {k: get_setting(k, v) for k, v in DEFAULT_UI.items()}


def params() -> config.StrategyParams:
    return config.StrategyParams.from_dict(get_setting("strategy_params", {}))


def save_params(p: config.StrategyParams) -> None:
    set_setting("strategy_params", p.to_dict())


# ---------------- candles cache ----------------
def save_candles(pair: str, tf: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = [(pair, tf, int(r.t), r.open, r.high, r.low, r.close, r.volume,
             None if pd.isna(r.spread) else float(r.spread)) for r in df.itertuples()]
    with _lock:
        conn().executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn().commit()


def load_candles(pair: str, tf: str, start: int = 0, end: int = 2**62) -> pd.DataFrame:
    with _lock:
        df = pd.read_sql_query(
            "SELECT t,open,high,low,close,volume,spread FROM candles WHERE pair=? AND tf=? AND t>=? AND t<? ORDER BY t",
            conn(), params=(pair, tf, start, end))
    return df


# ---------------- trades ----------------
def open_trade(pair, side, setup, score, entry, sl, tp, opened_at) -> int:
    return x("INSERT INTO trades(pair,side,setup,score,entry,sl,tp,initial_sl,opened_at,status) "
             "VALUES(?,?,?,?,?,?,?,?,?,'open')", (pair, side, setup, score, entry, sl, tp, sl, opened_at))


def close_trade(tid: int, exit_price: float, closed_at: int, reason: str) -> None:
    t = q("SELECT * FROM trades WHERE id=?", (tid,))[0]
    risk = abs(t["entry"] - t["initial_sl"]) or 1e-12
    r = (exit_price - t["entry"]) / risk if t["side"] == "BUY" else (t["entry"] - exit_price) / risk
    x("UPDATE trades SET exit=?, closed_at=?, r=?, status='closed', reason=? WHERE id=?",
      (exit_price, closed_at, round(r, 3), reason, tid))


def trades(limit: int = 500) -> list[dict]:
    return q("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))


def open_trades() -> list[dict]:
    return q("SELECT * FROM trades WHERE status='open'")


def realized_r_since(ts: int) -> float:
    rows = q("SELECT COALESCE(SUM(r),0) AS s FROM trades WHERE status='closed' AND closed_at>=?", (ts,))
    return float(rows[0]["s"])


# ---------------- alerts (dedupe) ----------------
def claim_alert(key: str, pair: str, side: str, candle_t: int, channel: str) -> bool:
    """Atomically reserve an alert key. Returns False if it was already sent/claimed."""
    with _lock:
        try:
            conn().execute("INSERT INTO alerts(key,pair,side,candle_t,sent_at,channel,status) VALUES(?,?,?,?,?,?,'pending')",
                           (key, pair, side, candle_t, int(time.time()), channel))
            conn().commit()
            return True
        except sqlite3.IntegrityError:
            return False


def finish_alert(key: str, status: str, detail: str = "") -> None:
    x("UPDATE alerts SET status=?, detail=? WHERE key=?", (status, detail[:300], key))


def alerts(limit: int = 50) -> list[dict]:
    return q("SELECT * FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,))


# ---------------- backtests ----------------
def save_backtest(pair: str, mode: str, params: dict, summary: dict, trades_: list, equity: list) -> int:
    return x("INSERT INTO backtests(created_at,pair,mode,params,summary,trades,equity) VALUES(?,?,?,?,?,?,?)",
             (int(time.time()), pair, mode, json.dumps(params), json.dumps(summary), json.dumps(trades_),
              json.dumps(equity)))


def latest_backtest(pair: str | None = None) -> dict | None:
    rows = (q("SELECT * FROM backtests WHERE pair=? ORDER BY id DESC LIMIT 1", (pair,)) if pair
            else q("SELECT * FROM backtests ORDER BY id DESC LIMIT 1"))
    if not rows:
        return None
    r = rows[0]
    for k in ("params", "summary", "trades", "equity"):
        r[k] = json.loads(r[k])
    return r


# ---------------- suggestions ----------------
def save_suggestion(text: str, changes: dict, baseline: dict, proposed: dict) -> int:
    return x("INSERT INTO suggestions(created_at,text,changes,baseline,proposed,status) VALUES(?,?,?,?,?,'pending')",
             (int(time.time()), text, json.dumps(changes), json.dumps(baseline), json.dumps(proposed)))


def get_suggestion(sid: int) -> dict | None:
    rows = q("SELECT * FROM suggestions WHERE id=?", (sid,))
    if not rows:
        return None
    r = rows[0]
    for k in ("changes", "baseline", "proposed"):
        r[k] = json.loads(r[k])
    return r


def set_suggestion_status(sid: int, status: str) -> None:
    x("UPDATE suggestions SET status=? WHERE id=?", (status, sid))
