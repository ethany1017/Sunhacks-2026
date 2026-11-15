from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.schemas import Snapshot


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def test_health_graph_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}
    g = client.get("/graph").json()
    assert g["type"] == "FeatureCollection" and len(g["meta"]["center"]) == 2 and len(g["meta"]["bounds"]) == 4
    Snapshot.model_validate(client.get("/state").json())


def test_websocket_sends_snapshot_then_deltas_at_about_10hz(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        Snapshot.model_validate(first)
        assert first["type"] == "snapshot"
        t0, count = time.time(), 0
        last = first["t"]
        while time.time() - t0 < 2.0:
            msg = ws.receive_json()
            assert msg["type"] == "delta" and msg["t"] >= last
            last = msg["t"]
            count += 1
    assert 12 <= count <= 28            # about 10 Hz


def test_control_pause_speed_haboob_and_errors(client: TestClient, monkeypatch) -> None:
    assert client.post("/control", json={"action": "speed", "value": 5}).json()["state"]["speed"] == 5
    assert client.post("/control", json={"action": "speed", "value": 7}).json()["ok"] is False
    assert client.post("/control", json={"action": "pause"}).json()["state"]["paused"] is True
    t0 = client.get("/state").json()["t"]
    time.sleep(0.5)
    assert client.get("/state").json()["t"] == t0                      # paused
    assert client.post("/control", json={"action": "resume"}).json()["state"]["paused"] is False
    res = client.post("/control", json={"action": "trigger_haboob"}).json()
    assert res["ok"] is True
    assert client.post("/control", json={"action": "trigger_haboob"}).json()["ok"] is False
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude = client.post("/control", json={"action": "set_controller", "value": "claude"}).json()
    assert claude["ok"] is False and "claude" in claude["error"]
    assert client.post("/control", json={"action": "bogus"}).status_code == 422


def test_decisions_endpoint_and_reset(client: TestClient) -> None:
    client.post("/control", json={"action": "speed", "value": 20})
    deadline = time.time() + 6
    snap = client.get("/state").json()
    while not snap["decisions"] and time.time() < deadline:
        time.sleep(0.2)
        snap = client.get("/state").json()
    assert snap["decisions"], "baseline should have recorded decisions"
    d = client.get("/decisions/%s" % snap["decisions"][0]["id"]).json()
    assert d["facts"] and d["rules"] is not None and "alternatives" in d
    assert client.get("/decisions/nope").status_code == 404
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert client.post("/control", json={"action": "reset"}).json()["ok"] is True
        seen = [ws.receive_json() for _ in range(6)]
        assert any(m["type"] == "snapshot" and m["t"] < 5 for m in seen)
