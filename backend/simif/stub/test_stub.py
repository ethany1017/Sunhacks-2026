import asyncio
import contextlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from simif.stub import airport, loop, world  # noqa: E402

APT = airport.load()
GRAPH = APT["graph"]
RUNWAY_PHASES = {"landing", "rollout", "lineup", "takeoff"}


def _edge():
    e = GRAPH["edges"][0]
    return e["from"], e["to"]


def _scenario(legs):
    return {"name": "t", "runway": next(iter(GRAPH["approaches"])),
            "aircraft": [{"callsign": "T1", "kind": "departure", "aircraft_type": "C172", "spawn_t": 0, "legs": legs}]}


def _run(speed=0, prepare=None, cancel_after=None):
    events = []
    scenario, aircraft = loop.load("movement_test", GRAPH)
    if prepare:
        prepare(aircraft)

    async def go():
        task = asyncio.create_task(loop.run(scenario, aircraft, speed, APT, events.extend))
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(go())
    return events


def test_loader_reports_missing_file(tmp_path):
    shutil.copytree(airport.AIRPORT_DIR, tmp_path / "a")
    (tmp_path / "a" / "graph.json").unlink()
    with pytest.raises(ValueError, match="graph.json"):
        airport.load(tmp_path / "a")


def test_movement_speed_and_phase_order():
    a, b = _edge()
    (ac,) = world.build(_scenario([{"phase": "taxi", "nodes": [a, b]}, {"phase": "holding", "hold_s": 3},
                                   {"phase": "departed", "extend_m": 500}]), GRAPH)
    start = [ac.lon, ac.lat]
    assert ac.step(1.0) == []
    assert world.dist(start, [ac.lon, ac.lat]) == pytest.approx(world.SPEED_KT["taxi"] * world.KT, rel=1e-3)
    phases = []
    for _ in range(10_000):
        phases += [r["phase"] for _, r in ac.step(1.0)]
        if ac.done:
            break
    assert phases == ["holding", "departed", "departed"]


def test_validation_rejects_unknown_node_and_missing_edge():
    a, _ = _edge()
    with pytest.raises(world.ScenarioError, match="NOPE"):
        world.build(_scenario([{"phase": "taxi", "nodes": [a, "NOPE"]}]), GRAPH)
    linked = {n for e in GRAPH["edges"] for n in (e["from"], e["to"]) if a in (e["from"], e["to"])}
    far = next(n for n in GRAPH["nodes"] if n not in linked)
    with pytest.raises(world.ScenarioError, match="no graph edge"):
        world.build(_scenario([{"phase": "taxi", "nodes": [a, far]}]), GRAPH)


def test_unsafe_scenario_name_is_not_found():
    with pytest.raises(FileNotFoundError):
        loop.load("../airport/graph", GRAPH)


def test_headless_movement_run():
    events = _run()
    types = [e["type"] for e in events]
    assert types[0] == "sim_start" and types[-1] == "sim_end" and types.count("aircraft_spawn") == 3
    assert types.count("sim_end") == 1 and "run_id" not in events[0]["data"]
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)
    pos = [e for e in events if e["type"] == "position"]
    last = {}
    for e in pos:
        last[e["callsign"]] = e["data"]["phase"]
    assert sorted(last.values()) == ["departed", "parked", "parked"]
    assert {e["data"]["phase"] for e in pos} == set(world.SPEED_KT)
    phase = {}  # no two aircraft in a runway phase at once
    for e in pos:
        phase[e["callsign"]] = e["data"]["phase"]
        assert sum(p in RUNWAY_PHASES for p in phase.values()) <= 1, e
    for cs in last:  # regular reports are 1 sim second apart; phase changes carry a reason
        ts = [e["t"] for e in pos if e["callsign"] == cs and not e["reason"]]
        assert ts and all(b - a == 1.0 for a, b in zip(ts, ts[1:]))
    for cs in (cs for cs, p in last.items() if p == "parked"):  # one final parked report, then nothing
        assert [e["data"]["phase"] for e in pos if e["callsign"] == cs].count("parked") == 1


def test_runs_are_deterministic():
    assert _run() == _run()


def test_cancelled_run_ends_with_sim_end():
    events = _run(speed=1, cancel_after=0.1)  # 1 sim s per wall s: still in the first tick when cancelled
    assert events[0]["type"] == "sim_start"
    assert events[-1]["type"] == "sim_end" and "cancelled" in events[-1]["reason"]


def test_crashing_run_ends_with_sim_end():
    def boom(dt):
        raise RuntimeError("boom")

    def prepare(aircraft):
        for a in aircraft:
            a.step = boom

    events = _run(prepare=prepare)
    assert events[0]["type"] == "sim_start"
    assert events[-1]["type"] == "sim_end" and "failed" in events[-1]["reason"] and "boom" in events[-1]["reason"]
