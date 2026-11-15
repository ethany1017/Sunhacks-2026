"""A3: the safety engine's rules (approve and reject each), the proposal -> safety_check -> issue flow, readback
checking, and short end-to-end days with no Referee safety fails."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atc import safety  # noqa: E402
from atc.planner import Planner  # noqa: E402

SAFETY_RULES = ("collision", "loss_of_separation", "runway_occupied", "runway_incursion")


def ac(cs, **kw):
    a = {"callsign": cs, "kind": "departure", "phase": "taxi_out", "x": 0.0, "y": 0.0, "heading_deg": 90.0,
         "speed_mps": 0.0, "length_m": 40.0, "wingspan_m": 35.0, "wake": "F", "wtc": "M", "eta_s": None,
         "runway": None, "blocked_by": None}
    a.update(kw)
    return a


def pic(*aircraft, **kw):
    p = {"t": 1000.0, "aircraft": {a["callsign"]: a for a in aircraft}, "occupants": {}, "runway_clearances": [],
         "clear_at": {}, "rollout_s": 72.0, "tailwind": {}, "tailwind_limit": 10.0, "live_actions": {}, "routes": {},
         "parked_on": {}, "paths": {}, "pushbacks": {}, "last_roll": {}, "rain": False, "hot_nodes": {}}
    p.update(kw)
    return p


def takeoff(cs="ME", pav="P"):
    return {"callsign": cs, "action": "cleared_for_takeoff", "end": "09", "pavement": pav}


# -- per rule -----------------------------------------------------------------------------------------------------
def test_tailwind():
    p = pic(ac("ME"), tailwind={"09": 12.0, "27": 0.0})
    assert safety.tailwind(p, takeoff())[:2] == (False, "tailwind")
    assert safety.tailwind(p, {"callsign": None, "action": "set_runway_config", "ends": ["27"]})[0]
    assert not safety.tailwind(p, {"callsign": None, "action": "set_runway_config", "ends": ["27", "09"]})[0]


def test_runway_occupancy_takeoff_and_landing():
    assert safety.runway_occupancy(pic(ac("ME")), takeoff())[0]
    busy = pic(ac("ME"), ac("X"), occupants={"P": ["X"]})
    assert safety.runway_occupancy(busy, takeoff())[:2] == (False, "runway_occupancy")
    cleared = pic(ac("ME"), ac("X"), runway_clearances=[{"callsign": "X", "action": "cross_runway", "pavement": "P",
                                                         "t": 990.0}])
    assert not safety.runway_occupancy(cleared, takeoff())[0]
    land = {"callsign": "ME", "action": "cleared_to_land", "end": "09", "pavement": "P", "eta": 60.0}
    late = pic(ac("ME", kind="arrival", phase="final"), ac("X"), occupants={"P": ["X"]}, clear_at={"X": 1055.0})
    assert not safety.runway_occupancy(late, land)[0]           # clears 5 s before touchdown: inside the 8 s buffer
    early = pic(ac("ME", kind="arrival", phase="final"), ac("X"), occupants={"P": ["X"]}, clear_at={"X": 1030.0})
    assert safety.runway_occupancy(early, land)[0]


def test_arrival_departure_spacing_and_rain():
    arr = lambda eta: ac("A1", kind="arrival", phase="final", runway="P", eta_s=eta)
    assert not safety.arrival_departure_spacing(pic(ac("ME"), arr(40.0)), takeoff())[0]
    assert safety.arrival_departure_spacing(pic(ac("ME"), arr(100.0)), takeoff())[0]
    ok, rule, _ = safety.arrival_departure_spacing(pic(ac("ME"), arr(70.0), rain=True), takeoff())
    assert (ok, rule) == (False, "rain_spacing")                # 60 s guard x 1.3 in rain
    cross = {"callsign": "ME", "action": "cross_runway", "pavement": "P"}
    assert not safety.arrival_departure_spacing(pic(ac("ME"), arr(50.0)), cross)[0]


def test_departure_spacing():
    roll = lambda t: {"P": {"t": t, "callsign": "L", "cwt": "F", "wtc": "M"}}
    assert not safety.departure_spacing(pic(ac("ME"), ac("L", phase="takeoff_roll"), last_roll=roll(980.0)), takeoff())[0]
    still = pic(ac("ME"), ac("L", phase="takeoff_roll"), last_roll=roll(900.0))
    assert not safety.departure_spacing(still, takeoff())[0]
    near = pic(ac("ME"), ac("L", phase="airborne", x=1000.0), last_roll=roll(900.0))
    assert not safety.departure_spacing(near, takeoff())[0]
    far = pic(ac("ME"), ac("L", phase="airborne", x=3000.0), last_roll=roll(900.0))
    assert safety.departure_spacing(far, takeoff())[0]


def test_wake_turbulence():
    heavy = lambda t: {"P": {"t": t, "callsign": "H", "cwt": "B", "wtc": "H"}}
    assert safety.wake_turbulence(pic(ac("ME"), last_roll=heavy(940.0)), takeoff())[:2] == (False, "wake_turbulence")
    assert safety.wake_turbulence(pic(ac("ME"), last_roll=heavy(870.0)), takeoff())[0]
    light = pic(ac("ME", wake="I", wtc="L"), last_roll={"P": {"t": 940.0, "callsign": "J", "cwt": "F", "wtc": "M"}})
    assert not safety.wake_turbulence(light, takeoff())[0]       # a light 60 s behind a jet (90 s needed)
    sup = lambda t: {"P": {"t": t, "callsign": "S", "cwt": "A", "wtc": "J"}}
    assert not safety.wake_turbulence(pic(ac("ME", wake="B", wtc="H"), last_roll=sup(940.0)), takeoff())[0]  # heavy 60 s behind a super
    assert safety.wake_turbulence(pic(ac("ME", wake="C", wtc="H"), last_roll=sup(870.0)), takeoff())[0]      # 130 s: ok
    assert safety.wake_turbulence(pic(ac("ME", wake="B", wtc="H"), last_roll=heavy(940.0)), takeoff())[0]    # heavy behind heavy


def test_hold_short_and_crossings():
    crossing = pic(ac("ME", speed_mps=5.0), live_actions={"ME": ("cross_runway",)})
    assert not safety.hold_short(crossing, {"callsign": "ME", "action": "resume_taxi"})[0]
    assert not safety.hold_short(crossing, {"callsign": "ME", "action": "hold_position"})[0]
    assert not safety.hold_short(crossing, {"callsign": "ME", "action": "taxi_via", "route": ["a", "b"]})[0]
    assert safety.hold_short(pic(ac("ME")), {"callsign": "ME", "action": "hold_position"})[0]
    rolling = pic(ac("ME"), ac("R", phase="takeoff_roll", runway="P"))
    assert not safety.hold_short(rolling, {"callsign": "ME", "action": "cross_runway", "pavement": "P"})[0]
    assert safety.hold_short(pic(ac("ME")), {"callsign": "ME", "action": "cross_runway", "pavement": "P"})[0]


def test_taxi_conflict_head_on_parked_and_converging():
    taxi = {"callsign": "ME", "action": "taxi_via", "route": ["a", "b", "c"]}
    assert not safety.taxi_conflict(pic(ac("ME"), ac("O"), routes={"O": ["d", "c", "b"]}), taxi)[0]   # head-on b-c
    assert safety.taxi_conflict(pic(ac("ME"), ac("O"), routes={"O": ["b", "c", "d"]}), taxi)[0]       # same way
    assert not safety.taxi_conflict(pic(ac("ME"), ac("O"), parked_on={"O": "c"}), taxi)[0]
    mine = [{"xy": (100.0, 0.0), "eta": 10.0, "hdg": 90.0}]
    theirs = [{"xy": (100.0, 5.0), "eta": 15.0, "hdg": 180.0}]
    conv = dict(taxi, path=mine)
    assert not safety.taxi_conflict(pic(ac("ME"), ac("O", x=100.0, y=300.0), paths={"O": theirs}), conv)[0]
    later = [{"xy": (100.0, 5.0), "eta": 60.0, "hdg": 180.0}]
    assert safety.taxi_conflict(pic(ac("ME"), ac("O", x=100.0, y=300.0), paths={"O": later}), conv)[0]


def test_taxi_conflict_sharp_turn_and_pushback():
    turn = {"callsign": "ME", "action": "resume_taxi", "route": ["a", "b"],
            "path": [{"xy": (10.0, 0.0), "eta": 5.0, "hdg": 90.0}, {"xy": (-30.0, -40.0), "eta": 15.0, "hdg": 225.0}]}
    near = pic(ac("ME"), ac("O", x=-40.0, y=10.0))
    assert not safety.taxi_conflict(near, turn)[0]               # the turn at (10, 0) sweeps over O, 51 m away
    assert safety.taxi_conflict(pic(ac("ME"), ac("O", x=-300.0, y=10.0)), turn)[0]
    push = {"callsign": "ME", "action": "pushback_approved", "lane_xy": (0.0, -60.0), "push_from": (0.0, 0.0),
            "push_to": (0.0, -60.0)}
    assert not safety.taxi_conflict(pic(ac("ME"), pushbacks={"O": {"xy": (30.0, -60.0), "t": 950.0}}), push)[0]
    assert not safety.taxi_conflict(pic(ac("ME"), ac("O", x=0.0, y=-50.0)), push)[0]   # someone in the push path
    assert safety.taxi_conflict(pic(ac("ME")), push)[0]


def test_taxi_conflict_route_into_a_moving_pushback():
    me = ac("ME", x=0.0, y=-100.0, heading_deg=0.0)
    pb = ac("PB", phase="pushback", x=0.0, y=40.0)
    path = [{"n": "a", "xy": (0.0, -50.0), "eta": 10.0, "hdg": 0.0}, {"n": "b", "xy": (0.0, 20.0), "eta": 25.0, "hdg": 0.0}]
    prop = {"callsign": "ME", "action": "resume_taxi", "route": ["a", "b"], "path": path}
    pushes = {"PB": {"xy": (0.0, 20.0), "stand_xy": (0.0, 60.0), "t": 900.0, "lane": "b"}}
    assert safety.taxi_conflict(pic(me, pb, pushbacks=pushes, hot_nodes={"b": "PB"}), prop)[0] is False
    assert safety.taxi_conflict(pic(me, pb, pushbacks=pushes), prop)[0]           # not moving yet: no conflict


def test_check_reports_first_failure_or_all_passed():
    ok, rule, why = safety.check(pic(ac("ME")), takeoff())
    assert ok and why.startswith("Passed tailwind, runway_occupancy")
    ok, rule, why = safety.check(pic(ac("ME"), ac("X"), occupants={"P": ["X"]}), takeoff())
    assert (ok, rule) == (False, "runway_occupancy") and "X" in why


# -- planner flow --------------------------------------------------------------------------------------------------
class FakeSI:
    def __init__(self, readbacks=()):
        self.issued, self.resolved, self._rb = [], [], list(readbacks)

    def issue(self, c):
        self.issued.append(c)
        return {"ok": True, "clearance_id": f"c{len(self.issued)}", "text": c["action"]}

    def get_readbacks(self):
        out, self._rb = self._rb, []
        return out

    def resolve_readback(self, rid, ok):
        self.resolved.append((rid, ok))

    def runway_label(self, pav):
        return pav

    @staticmethod
    def spoken_runway(end):
        return {"09": "zero niner", "27": "two seven"}[end]


def bare_planner(si, p):
    pl = object.__new__(Planner)   # no airport needed for the proposal and readback paths
    pl.si, pl.t, pl.pic, pl.rejected, pl.expected, pl.mem, pl.ends = si, p["t"], p, {}, {}, {}, {"09": {}}
    pl.events = []
    pl.emit = lambda t, typ, cs, data, reason: pl.events.append((typ, cs, data, reason))
    return pl


def test_unsafe_proposal_is_rejected_recorded_and_never_issued():
    si = FakeSI()
    p = pic(ac("ME"), ac("X"), occupants={"P": ["X"]})
    pl = bare_planner(si, p)
    res = pl._propose(p["aircraft"]["ME"], "cleared_for_takeoff", {"runway": "09"}, "ME is first in line.",
                      end="09", pavement="P")
    assert res is None and si.issued == []
    types = [e[0] for e in pl.events]
    assert types == ["proposal", "safety_check", "alert"]             # the rejection is also an alert
    assert pl.events[2][2]["rule"] == "runway_occupancy"
    chk = pl.events[1]
    assert chk[2]["approved"] is False and chk[2]["rule"] == "runway_occupancy" and "X" in chk[3]
    pl._propose(p["aircraft"]["ME"], "cleared_for_takeoff", {"runway": "09"}, "again", end="09", pavement="P")
    assert len(pl.events) == 3                                   # one record per rejection episode
    p["occupants"] = {}
    assert pl._propose(p["aircraft"]["ME"], "cleared_for_takeoff", {"runway": "09"}, "clear now", end="09", pavement="P")
    assert len(si.issued) == 1 and [e[0] for e in pl.events][-3:] == ["proposal", "safety_check", "clearance"]


def test_readback_wrong_is_rejected_and_correct_accepted():
    rbs = [{"id": "r1", "clearance_id": "c1", "callsign": "ME", "text": "cleared to land two seven",
            "parsed": {"action": "cleared_to_land", "runway": "27"}},
           {"id": "r2", "clearance_id": "c2", "callsign": "YOU", "text": "cleared for takeoff zero niner",
            "parsed": {"action": "cleared_for_takeoff", "runway": "09"}}]
    si = FakeSI(rbs)
    pl = bare_planner(si, pic())
    pl.expected = {"c1": ("ME", "cleared_to_land", "09"), "c2": ("YOU", "cleared_for_takeoff", "09")}
    pl._readbacks()
    assert si.resolved == [("r1", False), ("r2", True)]
    types = [(e[0], e[1]) for e in pl.events]
    assert ("readback_correction", "ME") in types and ("alert", "ME") in types
    assert not any(t[1] == "YOU" and t[0] != "readback" for t in types)


def test_readback_for_a_clearance_issued_before_the_switch_is_checked_not_waved_through():
    rbs = [{"id": "r1", "clearance_id": "h1", "callsign": "ME", "text": "Roger", "parsed": {"action": None, "runway": None}},
           {"id": "r2", "clearance_id": "h2", "callsign": "YOU", "text": "cleared to land two seven",
            "parsed": {"action": "cleared_to_land", "runway": "27"}},
           {"id": "r3", "clearance_id": "h3", "callsign": "HIM", "text": "cleared to land zero niner",
            "parsed": {"action": "cleared_to_land", "runway": "09"}},
           {"id": "r4", "clearance_id": "gone", "callsign": "HER", "text": "taxi", "parsed": {"action": "taxi_via"}}]
    si = FakeSI(rbs)
    pl = bare_planner(si, pic())
    pl.ends = {"09": {}, "27": {}}
    land = "runway zero niner, cleared to land"
    pl.live = {"h1": {"action": "cleared_to_land", "text": land}, "h2": {"action": "cleared_to_land", "text": land},
               "h3": {"action": "cleared_to_land", "text": land}}
    pl._readbacks()
    assert si.resolved == [("r1", False), ("r2", False), ("r3", True), ("r4", False)]


# -- end to end ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("level", ["smoke", "busy_arrivals"])
def test_short_practice_day_has_no_safety_fails(level, tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    import simif
    from app import db
    from app.runner import Runner

    r = Runner(simif.connect())
    r.start(level, "agent")
    r.run_headless()
    ref = r.sim.metrics()["referee"]
    assert not [f for f in ref["fails"] if f["rule"] in SAFETY_RULES]
    ev = db.events_for_run(r.run_id)
    assert all(e["reason"] for e in ev if e["type"] in ("proposal", "safety_check", "clearance", "readback"))
    assert sum(e["type"] == "proposal" for e in ev) == sum(e["type"] == "safety_check" for e in ev)
