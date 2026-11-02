"""FastAPI app: JSON API + static UI. Demo/paper only — there are no order endpoints."""
from __future__ import annotations

import asyncio
import csv
import io
import threading
import time
import uuid
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts, assistant, db, runner, stats
from .backtest import engine as bt, walkforward as wf
from .config import INSTRUMENTS, PAIRS, ROOT, TF_SECONDS, TF_UI, StrategyParams, data_mode, secret_present
from .data import market, news
from .engine import indicators, sessions, setups

STATIC = ROOT / "static"
JOBS: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()  # keep an already-open DB (tests); otherwise open the default
    stop = asyncio.Event()
    task = asyncio.create_task(runner.loop(stop)) if not getattr(app.state, "no_loop", False) else None
    app.state.btc_check = {"checked": False, "note": "pending"}
    threading.Thread(target=lambda: setattr(app.state, "btc_check", market.verify_btc_on_oanda()),
                     daemon=True).start()
    yield
    stop.set()
    if task:
        await task


app = FastAPI(title="Mark 1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _pair(pair: str) -> str:
    pair = pair.upper().replace("/", "")
    if pair not in PAIRS:
        raise HTTPException(404, f"unknown pair {pair}")
    return pair


def _clean(o):
    """JSON-safe: numpy -> python, NaN/inf -> None."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.bool_):
        return bool(o)
    return o


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    now = int(time.time())
    p = db.params()
    g = runner.gates(now, p)
    return _clean({
        "now": now, "demo": True, "data_mode": data_mode(), "session": sessions.session_name(now),
        "engine": {**{k: v for k, v in runner.ENGINE.items() if k != "gates"}, "running": g["engine_running"],
                   "killed": g["killed"]},
        "gates": {k: v for k, v in g.items() if k != "news"}, "news": g["news"],
        "ui": db.ui_state(), "params": p.to_dict(),
        "keys": {"oanda": secret_present("OANDA_TOKEN"), "finnhub": secret_present("FINNHUB_KEY"),
                 "twilio": alerts.configured()},
        "btc_check": getattr(app.state, "btc_check", {}),
        "data_status": market.STATUS,
    })


@app.get("/api/pairs")
def pairs():
    out = []
    for pr in PAIRS:
        s = runner.SNAPSHOT.get(pr)
        if not s:
            lp = market.last_price(pr)
            s = {"pair": pr, "side": "—", "score": None, "price": lp["price"], "spread": lp["spread"],
                 "alignment": {}, "pending": True}
        out.append({"pair": pr, "digits": INSTRUMENTS[pr]["digits"], **{k: s.get(k) for k in (
            "side", "score", "price", "spread", "alignment", "all_aligned", "setup", "action", "candle_t",
            "error", "source", "pending")}})
    return _clean(out)


@app.get("/api/signal/{pair}")
def signal(pair: str):
    pair = _pair(pair)
    s = runner.SNAPSHOT.get(pair)
    if not s:
        runner.tick_pair(pair, db.params(), runner.gates(int(time.time()), db.params()), int(time.time()))
        s = runner.SNAPSHOT[pair]
    return _clean({**s, "digits": INSTRUMENTS[pair]["digits"]})


@app.get("/api/why/{pair}")
def why(pair: str):
    pair = _pair(pair)
    s = signal(pair)
    return _clean({"pair": pair, "side": s["side"], "score": s["score"], "setup": s.get("setup"),
                   "checklist": s.get("checklist", []), "stats": stats.setup_stats(pair, s.get("setup")),
                   "all_setups": stats.setup_stats(pair, None),
                   "note": "Score is a heuristic out of 100 built from the checklist. It is not a probability."})


@app.get("/api/candles/{pair}")
def candles(pair: str, tf: str = "5m", count: int = 400):
    pair = _pair(pair)
    gtf = TF_UI.get(tf, tf.upper())
    if gtf not in TF_SECONDS:
        raise HTTPException(400, "tf must be 1m/5m/1h/4h")
    df = market.recent(pair, gtf, min(max(count, 100), 1500))
    if df.empty:
        return {"pair": pair, "tf": tf, "candles": [], "overlays": {}}
    p = db.params()
    ind = indicators.add_all(df, p)
    st = setups.detect(ind, ind["atr"], TF_SECONDS[gtf])
    t = df["t"].to_numpy()
    sw = setups.swings(df, n=5)  # display only: fewer, more significant swing points
    swings_ = ([{"time": int(t[i]), "price": float(df["high"].iloc[i]), "kind": "H"} for i in np.where(sw["is_high"])[0]]
               + [{"time": int(t[i]), "price": float(df["low"].iloc[i]), "kind": "L"} for i in np.where(sw["is_low"])[0]])
    events = [{"time": int(t[i]), "event": e, "price": float(df["close"].iloc[i])}
              for i, e in enumerate(st["structure"]["event"]) if e]
    last_t = int(t[-1])
    return _clean({
        "pair": pair, "tf": tf, "digits": INSTRUMENTS[pair]["digits"],
        "candles": [{"time": int(r.t), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for r in df.itertuples()],
        "ema_fast": [{"time": int(a), "value": float(b)} for a, b in zip(t, ind["ema_fast"]) if not np.isnan(b)],
        "ema_slow": [{"time": int(a), "value": float(b)} for a, b in zip(t, ind["ema_slow"]) if not np.isnan(b)],
        "overlays": {
            "fvg": _zones(st["fvg"]["zones"], last_t, keep_filled=6, max_active=12),
            "ob": _zones(st["ob"]["zones"], last_t, keep_filled=4, max_active=8),
            "swings": sorted(swings_, key=lambda s: s["time"])[-40:], "structure": events[-12:],
            "range": st["range"]["ranges"][-5:],
        },
    })


def _zones(zones: list, last_t: int, keep_filled: int, max_active: int) -> list:
    """Keep the chart readable: unfilled zones (most recent) plus a few recently filled ones."""
    active = [z for z in zones if z["end"] is None][-max_active:]
    filled = [z for z in zones if z["end"] is not None][-keep_filled:]
    return [{**z, "end": z["end"] or last_t, "active": z["end"] is None} for z in filled + active]


@app.get("/api/news")
def news_api():
    p = db.params()
    st = news.status(int(time.time()), p.news_window_min, db.get_setting("news_pause_manual", False))
    return _clean({**st, "events": news.high_usd(news.events())[:40], "headlines": news.headlines()})


class Toggle(BaseModel):
    on: bool


@app.post("/api/engine/{action}")
def engine_action(action: str):
    if action == "start":
        if db.get_setting("killed", False):
            raise HTTPException(409, "Kill switch is active — reset it first.")
        db.set_setting("engine_running", True)
    elif action == "stop":
        db.set_setting("engine_running", False)
    else:
        raise HTTPException(400, "start|stop")
    return status()


@app.post("/api/kill")
def kill():
    db.set_setting("killed", True)
    db.set_setting("engine_running", False)
    n = runner.close_all("kill switch")
    return {"killed": True, "closed_trades": n}


@app.post("/api/kill/reset")
def kill_reset():
    db.set_setting("killed", False)
    return {"killed": False, "engine_running": False}


@app.post("/api/toggle/{name}")
def toggle(name: str, body: Toggle):
    allowed = {"sound": "sound", "whatsapp": "whatsapp", "news_pause": "news_pause_manual"}
    if name not in allowed:
        raise HTTPException(400, f"toggle one of {list(allowed)}")
    db.set_setting(allowed[name], body.on)
    return db.ui_state()


@app.post("/api/alerts/test")
def alert_test():
    ok, detail = alerts.send_whatsapp("Mark 1 [DEMO] test message — alerts are working.")
    return {"ok": ok, "detail": detail}


@app.get("/api/alerts")
def alert_log():
    return db.alerts()


# ---------------- trades & stats ----------------
@app.get("/api/trades")
def trades_api(limit: int = 200):
    return _clean(db.trades(limit))


@app.get("/api/trades.csv")
def trades_csv():
    rows = db.trades(100000)
    buf = io.StringIO()
    cols = ["id", "pair", "side", "setup", "score", "entry", "sl", "tp", "initial_sl", "opened_at", "closed_at",
            "exit", "r", "status", "reason", "source"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=mark1_trades.csv"})


@app.get("/api/stats")
def stats_api():
    p = db.params()
    now = int(time.time())
    return _clean({"paper": stats.paper_summary(), "daily_r": db.realized_r_since(runner.day_start(now)),
                   "daily_loss_limit_r": p.daily_loss_limit_r, "trailing": p.trailing,
                   "latest_backtest": _bt_brief(db.latest_backtest())})


def _bt_brief(b):
    if not b:
        return None
    return {"id": b["id"], "pair": b["pair"], "mode": b["mode"], "summary": b["summary"], "created_at": b["created_at"]}


# ---------------- backtests (background jobs) ----------------
class BacktestReq(BaseModel):
    pair: str = "XAUUSD"
    months: float = 3
    walkforward: bool = False
    slippage_pips: float = 0.5
    train_days: int = 30
    test_days: int = 14


def _run_backtest(job_id: str, req: BacktestReq):
    job = JOBS[job_id]
    try:
        pair = _pair(req.pair)
        days = int(min(max(req.months, 0.25), 6) * 30.5)
        job["stage"] = "loading data"
        m1 = market.history_m1(pair, days, progress=lambda s: job.update(stage=s))
        if len(m1) < 5000:
            raise RuntimeError(f"only {len(m1)} 1m candles available — not enough to backtest")
        p = db.params()
        costs = bt.Costs(slippage_pips=req.slippage_pips)
        job["stage"] = "walk-forward" if req.walkforward else "simulating"
        res = (wf.run(m1, p, pair, req.train_days, req.test_days, costs) if req.walkforward
               else bt.run(m1, p, pair, costs))
        if data_mode() == "synthetic":
            res["verdict"]["notes"].insert(0, "SYNTHETIC DATA: these numbers say nothing about real markets.")
            res["verdict"]["level"] = "synthetic"
        res.update(pair=pair, mode="walkforward" if req.walkforward else "standard", days=days,
                   params=p.to_dict(), slippage_pips=req.slippage_pips, data_mode=data_mode())
        res = _clean(res)
        res["id"] = db.save_backtest(pair, res["mode"], p.to_dict(), {**res["summary"], "verdict": res["verdict"]},
                                     res["trades"], res["equity"])
        job.update(status="done", stage="done", result=res)
    except Exception as e:  # noqa: BLE001
        job.update(status="error", stage="error", error=str(e)[:300])


@app.post("/api/backtest")
def backtest_start(req: BacktestReq):
    if any(j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(409, "a backtest is already running")
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"id": jid, "status": "running", "stage": "queued", "started": int(time.time()), "req": req.model_dump()}
    threading.Thread(target=_run_backtest, args=(jid, req), daemon=True).start()
    return {"job": jid}


@app.get("/api/backtest/job/{jid}")
def backtest_job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "no such job")
    return JOBS[jid]


@app.get("/api/backtest/latest")
def backtest_latest(pair: str | None = None):
    b = db.latest_backtest(_pair(pair) if pair else None)
    if not b:
        return None
    s = dict(b["summary"])
    v = s.pop("verdict", None)
    return _clean({"id": b["id"], "pair": b["pair"], "mode": b["mode"], "params": b["params"], "summary": s,
                   "verdict": v, "equity": b["equity"], "trades": b["trades"][-200:], "created_at": b["created_at"]})


# ---------------- assistant (suggest-only) ----------------
class AskReq(BaseModel):
    text: str
    pair: str = "XAUUSD"


@app.post("/api/assistant")
def ask(req: AskReq):
    pair = _pair(req.pair)
    cmd = assistant.parse(req.text, pair)
    kind = cmd["kind"]
    if kind == "help" or kind == "unknown":
        return {"kind": "message", "text": ("I didn't understand that. " if kind == "unknown" else "") + assistant.HELP}
    if kind == "view":
        return {"kind": "view", **cmd, "text": "View updated (display only — no strategy settings changed)."}
    if kind == "why":
        return {"kind": "why", **why(cmd["pair"])}
    if kind == "backtest":
        return {"kind": "run_backtest", **cmd,
                "text": f"Starting a {cmd['months']}-month {'walk-forward ' if cmd['walkforward'] else ''}backtest "
                        f"on {cmd['pair']}."}
    days = 60
    m1 = market.history_m1(cmd["pair"], days)
    if len(m1) < 5000:
        return {"kind": "message", "text": "Not enough history to test a suggestion right now."}
    if kind == "optimize":
        return _clean(assistant.optimize(cmd["pair"], m1))
    err = assistant.validate(cmd["changes"])
    if err:
        return {"kind": "message", "text": err}
    label = "Change " + ", ".join(f"{k} → {v}" for k, v in cmd["changes"].items()) + f" (tested on {cmd['pair']}, {days}d)"
    res = assistant.compare(cmd["pair"], cmd["changes"], m1, label)
    if data_mode() == "synthetic":
        res["notes"].insert(0, "Tested on SYNTHETIC data — not evidence about real markets.")
    return _clean(res)


@app.post("/api/suggestions/{sid}/{decision}")
def decide(sid: int, decision: str):
    s = db.get_suggestion(sid)
    if not s:
        raise HTTPException(404, "no such suggestion")
    if s["status"] != "pending":
        raise HTTPException(409, f"already {s['status']}")
    if decision == "approve":
        db.save_params(StrategyParams.from_dict({**db.params().to_dict(), **s["changes"]}))
        db.set_suggestion_status(sid, "approved")
    elif decision == "reject":
        db.set_suggestion_status(sid, "rejected")
    else:
        raise HTTPException(400, "approve|reject")
    return {"id": sid, "status": db.get_suggestion(sid)["status"], "params": db.params().to_dict()}
