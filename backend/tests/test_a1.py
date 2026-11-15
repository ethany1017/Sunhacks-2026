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


def test_do_nothing_agent_fails(runner):
    runner.start("smoke", "agent")
    runner.run_headless()
    ev = db.events_for_run(runner.run_id)
    c = Counter(e["type"] for e in ev)
    assert [e["type"] for e in ev[:2]] == ["run_start", "airport"] and ev[-1]["type"] == "sim_end"
    assert c["aircraft_spawn"] == 6 and c["position"] > 100 and c["referee_fail"] >= 1 and c["metrics"] >= 1
    assert {"t", "seq", "type", "callsign", "data", "reason"} == set(ev[0])
    fail = next(e for e in ev if e["type"] == "referee_fail")
    assert fail["data"]["rule"] and fail["data"]["callsigns"] and fail["reason"]
    result = next(e for e in ev if e["type"] == "referee_result")
    assert result["data"]["verdict"] == "FAIL" and "score" in result["data"]


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
    assert runner.sim.sim.controller_name == "tower"
    runner.switch("agent")
    assert runner.sim.sim.controller_name == "agent"
    runner.switch("human")
    runner.run_headless()
    ev = db.events_for_run(runner.run_id)
    sw = [(e["data"]["from"], e["data"]["to"]) for e in ev if e["type"] == "controller_switch"]
    assert sw == [("agent", "human"), ("human", "agent"), ("agent", "human")]
    # the tower took over before anything went wrong, so the run passes
    assert next(e for e in ev if e["type"] == "referee_result")["data"]["verdict"] == "PASS"


def test_agent_code_never_imports_sim():
    pat = re.compile(r"^\s*(from|import)\s+sim(\.|\s|$)", re.M)
    for d in ("atc", "app"):
        for f in (BACKEND / d).rglob("*.py"):
            assert not pat.search(f.read_text()), f
