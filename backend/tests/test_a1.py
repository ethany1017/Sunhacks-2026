import re
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import simif  # noqa: E402
from app import airport, db  # noqa: E402
from app.runner import Runner  # noqa: E402

SIM = simif.connect()  # one adapter (graph parsed once) shared by the tests


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    return Runner(SIM)


def test_connect_picks_real_sim_and_stub_by_flag(monkeypatch):
    from simif.real import RealSimulator
    from simif.stub import StubSimulator
    assert isinstance(SIM, RealSimulator)
    monkeypatch.setenv("SIMIF", "stub")
    assert isinstance(simif.connect(), StubSimulator)


def test_airport_is_valid_kphx():
    apt = SIM.get_airport()
    airport.validate(apt)
    assert apt["config"]["icao"] == "KPHX"
    runways = {f["properties"]["id"] for f in apt["geojson"]["features"] if f["properties"]["kind"] == "runway"}
    assert runways == {"08/26", "07L/25R", "07R/25L"}
    assert set(apt["graph"]["approaches"]) == {"08", "26", "07L", "25R", "07R", "25L"}
    assert apt["graph"]["crossings"]


def test_state_has_documented_fields():
    SIM.reset("smoke")
    for _ in range(200):
        SIM.step()
    st = SIM.get_state()
    assert {"t", "aircraft", "runways", "runway_config", "weather", "pending_readbacks", "referee"} <= set(st)
    assert st["aircraft"] and "score" not in st["referee"]
    for a in st["aircraft"]:
        assert {"callsign", "kind", "type", "wake", "lat", "lon", "heading_deg", "speed_mps", "altitude_ft", "phase", "gate",
                "runway", "route", "planned_route", "hold", "eta_s", "holding_s", "holding_limit_s", "next_node",
                "clearance"} <= set(a)
    assert SIM.get_level()["id"] == "smoke" and SIM.get_weather()["metar"]
    assert set(SIM.get_referee()) == {"verdict", "fails", "day_end_s"}


def test_agent_handles_smoke_day(runner):
    runner.start("smoke", "agent")
    runner.run_headless()
    ev = db.events_for_run(runner.run_id)
    c = Counter(e["type"] for e in ev)
    assert [e["type"] for e in ev[:2]] == ["run_start", "airport"] and ev[-1]["type"] == "sim_end"
    assert c["aircraft_spawn"] == 6 and c["position"] > 100 and c["metrics"] >= 1
    assert {"t", "seq", "type", "callsign", "data", "reason"} == set(ev[0])
    assert c["proposal"] == c["safety_check"] > 0 and c["clearance"] > 0 and c["readback"] > 0
    assert all(e["reason"] for e in ev if e["type"] in ("proposal", "safety_check", "clearance", "readback"))
    result = next(e for e in ev if e["type"] == "referee_result")
    assert result["data"]["verdict"] == "PASS" and not any(e["type"] == "referee_fail" for e in ev)


def test_human_tower_passes(runner):
    runner.start("smoke", "human")
    runner.run_headless()
    ev = db.events_for_run(runner.run_id)
    assert next(e for e in ev if e["type"] == "referee_result")["data"]["verdict"] == "PASS"
    assert not any(e["type"] == "referee_fail" for e in ev)
    assert sum(e["type"] == "aircraft_exit" for e in ev) == 6  # 3 departed + 3 parked


def test_switch_controller_mid_run(runner):
    runner.start("smoke", "agent")
    while runner.sim.t < 30:
        runner.tick()
    assert runner.sim.sim.controller_name == "agent"
    with pytest.raises(ValueError):
        runner.switch("nobody")
    runner.switch("human")
    assert runner.sim.sim.controller_name in ("human", "tower")
    runner.switch("agent")
    assert runner.sim.sim.controller_name == "agent"
    runner.switch("human")
    runner.run_headless()
    ev = db.events_for_run(runner.run_id)
    sw = [(e["data"]["from"], e["data"]["to"]) for e in ev if e["type"] == "controller_switch"]
    assert sw == [("agent", "human"), ("human", "agent"), ("agent", "human")]
    assert next(e for e in ev if e["type"] == "referee_result")["data"]["verdict"] in ("PASS", "FAIL")


def test_agent_code_never_imports_sim():
    pat = re.compile(r"^\s*(from|import)\s+sim(\.|\s|$)", re.M)
    for d in ("atc", "app"):
        for f in (BACKEND / d).rglob("*.py"):
            assert not pat.search(f.read_text()), f


@pytest.mark.parametrize("level,seed", [("nowhere", None), ("smoke", -1)])
def test_failed_restart_leaves_the_run_alone(runner, level, seed):
    runner.start("smoke", "agent")
    for _ in range(20):
        runner.tick()
    run_id, t = runner.run_id, runner.sim.t
    with pytest.raises(ValueError):
        runner.start(level, "agent", seed)
    with pytest.raises(ValueError):
        runner.start("smoke", "nobody")
    assert runner.run_id == run_id and not runner.ended and runner.sim.t == t
    runner.tick()
    runner.flush()
    assert runner.sim.t > t
    assert not any(e["type"] == "sim_end" for e in db.events_for_run(run_id))


class _Peeker:
    """Test controller that reads the adapter `calls` times per tick and records what it saw."""
    name, status = "agent", "idle"

    def __init__(self, calls):
        self.calls, self.seen = calls, []

    def on_tick(self, sim, events):
        for _ in range(self.calls):
            st = SIM.get_state()
            SIM.get_readbacks()
            SIM.get_referee()
        self.seen.append([(a["callsign"], a["lat"], a["lon"]) for a in st["aircraft"]])


def test_observation_noise_independent_of_call_count():
    runs = []
    for calls in (1, 10):
        SIM.reset("smoke", 7)
        peek = _Peeker(calls)
        SIM.set_controller("agent", peek)
        for _ in range(600):
            SIM.step()
        runs.append((peek.seen, SIM.metrics()["aircraft"]))
    assert any(runs[0][0]) and runs[0] == runs[1]
