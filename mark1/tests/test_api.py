import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.server import app
    app.state.no_loop = True
    with TestClient(app) as c:
        yield c


def test_status_and_demo_flag(client):
    s = client.get("/api/status").json()
    assert s["demo"] is True and s["data_mode"] == "synthetic"


def test_no_order_endpoints(client):
    paths = {r.path for r in client.app.routes}
    assert not any("order" in p for p in paths)


def test_signal_candles_grid(client):
    s = client.get("/api/signal/XAUUSD").json()
    assert s["side"] in ("BUY", "SELL", "NO TRADE")
    c = client.get("/api/candles/XAUUSD?tf=5m").json()
    assert len(c["candles"]) > 100 and set(c["overlays"]) == {"fvg", "ob", "swings", "structure", "range"}
    g = client.get("/api/pairs").json()
    assert [r["pair"] for r in g] == ["XAUUSD", "EURUSD", "GBPUSD", "BTCUSD"]


def test_kill_switch_blocks_start(client):
    assert client.post("/api/engine/start").json()["engine"]["running"]
    client.post("/api/kill")
    assert client.post("/api/engine/start").status_code == 409
    client.post("/api/kill/reset")
    assert client.post("/api/engine/start").status_code == 200


def test_suggestion_approve_flow(client):
    r = client.post("/api/assistant", json={"text": "set min score to 65", "pair": "EURUSD"}).json()
    assert r["kind"] == "suggestion"
    assert client.get("/api/status").json()["params"]["min_score"] == 55
    client.post(f"/api/suggestions/{r['id']}/approve")
    assert client.get("/api/status").json()["params"]["min_score"] == 65
    assert client.post(f"/api/suggestions/{r['id']}/reject").status_code == 409


def test_csv_export(client):
    r = client.get("/api/trades.csv")
    assert r.status_code == 200 and r.text.startswith("id,pair,side")
