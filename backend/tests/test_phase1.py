import copy
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND), str(BACKEND / "scripts")]

from simif.stub import StubSimulator  # noqa: E402
from app import airport, db  # noqa: E402


PHASE1_TYPES = {"airport", "sim_start", "sim_end", "weather", "runway_change", "aircraft_spawn", "position", "proposal",
                "ai_decision", "safety_check", "clearance", "audio", "readback", "readback_correction", "sequence", "alert"}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))


def test_simulator_airport_is_valid():
    airport.validate(StubSimulator().get_airport())


def test_validator_rejects_edge_to_missing_node():
    apt = copy.deepcopy(StubSimulator().get_airport())
    graph = apt["graph"]
    graph["edges"].append({"from": graph["edges"][0]["from"], "to": "NOPE", "kind": "taxiway", "name": "X", "length_m": 1})
    with pytest.raises(airport.AirportError, match="NOPE"):
        airport.validate(apt)


def test_emit_increasing_seq_and_rejects_unknown_type(tmp_db):
    run = db.start_run()
    a = db.emit(run, "sim_start", 0, data={"run_id": run})
    b = db.emit(run, "alert", 1, data={"level": "info", "message": "hi"}, reason="test")
    assert b["seq"] > a["seq"]
    with pytest.raises(ValueError):
        db.emit(run, "bogus", 2)
    assert [e["seq"] for e in db.events_for_run(run)] == [a["seq"], b["seq"]]


def test_websocket_replays_then_streams(tmp_db, monkeypatch):
    monkeypatch.setenv("AUTOSTART", "0")  # no live run: this test checks replay of hand-written runs
    from fastapi.testclient import TestClient
    from app.main import app

    db.emit(1, "sim_start", 0, data={"run_id": 1})  # old run: not replayed
    db.emit(2, "sim_start", 0, data={"run_id": 2})
    db.emit(2, "alert", 1, data={"level": "info", "message": "x"})
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        history = [json.loads(ws.receive_text()) for _ in range(2)]
        assert [e["type"] for e in history] == ["sim_start", "alert"]
        assert set(history[0]) == {"t", "seq", "type", "callsign", "data", "reason"}
        new = db.emit(3, "sim_start", 0, data={"run_id": 3})
        assert json.loads(ws.receive_text())["seq"] == new["seq"]


def test_seed_covers_every_event_type():
    import seed

    apt = StubSimulator().get_airport()
    airport.validate(apt)
    events = seed.build_events(apt, 1)
    assert {e["type"] for e in events} == PHASE1_TYPES and PHASE1_TYPES <= db.EVENT_TYPES
    assert [e["type"] for e in events[:2]] == ["airport", "sim_start"]
    assert events[0]["data"] == apt and events[0]["callsign"] is None
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)
