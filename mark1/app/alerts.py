"""WhatsApp alerts via Twilio REST. One message per confirmed setup, de-duplicated in SQLite."""
from __future__ import annotations

import requests

from . import db
from .config import INSTRUMENTS, env, secret_present

REQUIRED = ("TWILIO_SID", "TWILIO_TOKEN", "WHATSAPP_FROM", "WHATSAPP_TO")


def configured() -> bool:
    return all(secret_present(k) for k in REQUIRED)


def _wa(num: str) -> str:
    return num if num.startswith("whatsapp:") else f"whatsapp:{num}"


def format_message(sig: dict) -> str:
    d = INSTRUMENTS[sig["pair"]]["digits"]
    return (f"Mark 1 [DEMO] {sig['pair']} {sig['side']}\n"
            f"Entry {sig['entry']:.{d}f}\nSL {sig['sl']:.{d}f}\nTP {sig['tp']:.{d}f}\n"
            f"Score {sig['score']}/100 (heuristic, not a probability)\nSetup: {sig.get('setup') or '-'}")


def dedupe_key(sig: dict) -> str:
    return f"{sig['pair']}|{sig['side']}|{sig['candle_t']}"


def send_whatsapp(body: str) -> tuple[bool, str]:
    if not configured():
        return False, "Twilio not configured"
    sid = env("TWILIO_SID")
    try:
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                          data={"From": _wa(env("WHATSAPP_FROM")), "To": _wa(env("WHATSAPP_TO")), "Body": body},
                          auth=(sid, env("TWILIO_TOKEN")), timeout=15)
    except requests.RequestException as e:
        return False, f"network error: {type(e).__name__}"
    if r.status_code in (200, 201):
        return True, "sent"
    try:
        msg = r.json().get("message", "")[:120]
    except ValueError:
        msg = ""
    return False, f"HTTP {r.status_code} {msg}"


def alert_signal(sig: dict, enabled: bool, sender=send_whatsapp) -> dict:
    """Send at most one message per (pair, side, candle). Returns what happened."""
    if sig.get("side") not in ("BUY", "SELL"):
        return {"sent": False, "why": "no setup"}
    key = dedupe_key(sig)
    if not db.claim_alert(key, sig["pair"], sig["side"], sig["candle_t"], "whatsapp" if enabled else "log"):
        return {"sent": False, "why": "duplicate"}
    if not enabled:
        db.finish_alert(key, "skipped", "WhatsApp off")
        return {"sent": False, "why": "whatsapp off"}
    ok, detail = sender(format_message(sig))
    db.finish_alert(key, "sent" if ok else "failed", detail)
    return {"sent": ok, "why": detail}
