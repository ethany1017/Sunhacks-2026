"""Contract-shaped views of simulation state (REST/WS snapshot items and the controller observation)."""
from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from sim.core.clock import TICK_S
from sim.core.entities import Aircraft
from sim.core.physics import HOLD_OFFSET_M, REENTRY_M
from sim.referee.limits import DIVERSION_HOLDING_S

if TYPE_CHECKING:
    from sim.core.engine import Simulation

FT_PER_M = 3.28084
M_PER_DEG = 111320.0


def _ll(sim: "Simulation", pts: np.ndarray) -> List[List[float]]:
    if len(pts) == 0:
        return []
    lon, lat = sim.graph.frame._inv.transform(pts[:, 0], pts[:, 1])
    return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat)]


def route_lines(sim: "Simulation", ac: Aircraft) -> Tuple[List[List[float]], List[List[float]]]:
    """(cleared remaining route, planned route beyond the clearance) as lon/lat lists."""
    p = ac.path
    if p is None or ac.phase in ("at_gate", "parked", "final", "go_around", "airborne") or ac.s >= p.length - 0.5:
        return [], []
    stop = min(max(ac.stop_s, ac.s), p.length)
    idx = np.nonzero(p.cum > ac.s)[0]
    here = np.array([[ac.x, ac.y]])
    cleared_i = [i for i in idx if p.cum[i] <= stop + 1e-6]
    cleared = np.vstack([here, p.pts[cleared_i], p.xy_at(stop)[None, :]]) if stop > ac.s else here
    planned_i = [i for i in idx if p.cum[i] > stop + 1e-6]
    planned = np.vstack([p.xy_at(stop)[None, :], p.pts[planned_i]]) if planned_i else np.zeros((0, 2))
    return _ll(sim, cleared if stop > ac.s else np.zeros((0, 2))), _ll(sim, planned)


def eta_s(sim: "Simulation", ac: Aircraft) -> Optional[float]:
    if ac.phase == "final":
        if ac.wp is not None:     # still flying round to the re-entry point (after a go-around or a runway reversal)
            dist = math.hypot(ac.wp[0] - ac.x, ac.wp[1] - ac.y) + REENTRY_M
        else:
            dist = sim.physics.approach_distance(ac)
        return round(dist / max(ac.speed, 30.0), 1)
    if ac.path is not None and ac.phase in ("taxi_in", "taxi_out", "runway_exit", "hold_short", "line_up"):
        return round(max(ac.path.length - ac.s, 0.0) / 4.5, 1)
    return None


def status_text(sim: "Simulation", ac: Aircraft) -> str:
    rwy = ac.runway or ""
    if ac.kind == "vehicle":
        return "Vehicle on service road"
    ph = ac.phase
    if ph == "final":
        return "On final runway %s%s" % (rwy, "" if ac.landing_cleared else ", awaiting landing clearance")
    if ph == "go_around":
        return "Going around"
    if ph == "landing_roll":
        return "Landing roll runway %s" % rwy
    if ph == "runway_exit":
        return "Exiting runway"
    if ph in ("taxi_in", "hold_short", "taxi_out") and ac.speed < 0.2 and ac.stop_why in ("run", "busy", "hold") and ac.path:
        if ac.stop_why == "hold":
            return "Holding position"
        run = next((r for r in ac.path.runs if r.s0 - ac.length_m / 2 - HOLD_OFFSET_M >= ac.s - 3.0), None)
        label = sim.runway_label(run.runway) if run else ""
        return "Holding short runway %s%s" % (label, " (runway busy)" if ac.stop_why == "busy" else "")
    if ph == "taxi_in":
        return "Taxiing to gate %s" % ac.gate
    if ph == "taxi_out":
        return "Taxiing to runway %s" % (ac.dep_end or "")
    if ph == "hold_short":
        return "Holding short"
    if ph == "line_up":
        return ("Cleared for takeoff runway %s" if ac.takeoff_cleared else "Lined up runway %s") % ac.dep_end
    if ph == "takeoff_roll":
        return "Takeoff roll runway %s" % ac.dep_end
    if ph == "pushback":
        return "Pushing back from gate %s" % ac.gate
    if ph == "at_gate":
        return "At gate %s%s" % (ac.gate, ", ready for pushback" if ac.ready and ac.kind == "departure" else "")
    if ph == "parked":
        return "Parked at gate %s" % ac.gate
    return "Departed"


def aircraft_view(sim: "Simulation", ac: Aircraft, with_routes: bool = True) -> Dict[str, Any]:
    lon, lat = sim.graph.lonlat((ac.x, ac.y))
    cleared, planned = route_lines(sim, ac) if with_routes else ([], [])
    hold = None
    if ac.path is not None and ac.speed < 0.2 and ac.stop_why in ("run", "busy") and ac.phase in ("taxi_in", "taxi_out", "hold_short"):
        run = next((r for r in ac.path.runs if r.s0 - ac.length_m / 2 - HOLD_OFFSET_M >= ac.s - 3.0), None)
        if run:
            hlon, hlat = sim.graph.lonlat(ac.path.xy_at(run.s0))
            hold = {"runway": run.runway, "lon": round(hlon, 6), "lat": round(hlat, 6)}
    return {"id": ac.callsign, "callsign": ac.callsign, "type": ac.type.icao, "kind": ac.kind, "airline": ac.airline,
            "lon": round(lon, 7), "lat": round(lat, 7), "heading_deg": round(ac.heading, 1), "speed_mps": round(ac.speed, 2),
            "phase": ac.phase, "status_text": status_text(sim, ac), "wingspan_m": ac.wingspan_m, "length_m": ac.length_m,
            "gate": ac.gate, "runway": sim.runway_pavement(ac.runway or ac.dep_end), "route": cleared, "planned_route": planned,
            "hold": hold, "eta_s": eta_s(sim, ac), "altitude_ft": int(ac.alt_m * FT_PER_M),
            "holding_s": round(ac.holding_s, 1) if ac.kind == "arrival" else None,
            "holding_limit_s": DIVERSION_HOLDING_S if ac.kind == "arrival" else None}


class ObservationBuffer:
    """Short history of aircraft views so the controller can see a delayed, noisy picture of the truth."""

    def __init__(self) -> None:
        self.frames: Deque[Dict[str, Dict[str, Any]]] = deque(maxlen=80)

    def record(self, sim: "Simulation") -> None:
        frame = {}
        for ac in sim.aircraft.values():
            if ac.removed:
                continue
            v = aircraft_view(sim, ac, with_routes=False)
            v.update(ready=ac.ready, landing_cleared=ac.landing_cleared, takeoff_cleared=ac.takeoff_cleared,
                     dep_end=ac.dep_end, arrival_end=ac.runway if ac.kind == "arrival" else None,
                     blocked_by=ac.blocked_by, stopped_s=0.0 if ac.stopped_since is None else round(sim.clock.t - ac.stopped_since, 1),
                     frequency=ac.frequency, done=ac.done, entry_node=ac.entry_node,
                     next_node=sim._route_origin(ac)[0] if ac.kind != "vehicle" else None,   # where an explicit taxi route must start
                     prev_node=sim._route_back(ac)[0] if ac.kind != "vehicle" else None)     # or, stopped, may start (turn round)
            frame[ac.callsign] = v
        self.frames.append(frame)

    def observe(self, delay_s: float, noise_m: float, rng: np.random.Generator) -> Tuple[float, List[Dict[str, Any]]]:
        if not self.frames:
            return 0.0, []
        back = min(int(round(delay_s / TICK_S)), len(self.frames) - 1)
        frame = self.frames[len(self.frames) - 1 - back]
        out = []
        for cs in sorted(frame):
            v = dict(frame[cs])
            if noise_m > 0:
                sx = noise_m / math.sqrt(2)
                v["lat"] = round(v["lat"] + float(rng.normal(0, sx)) / M_PER_DEG, 7)
                v["lon"] = round(v["lon"] + float(rng.normal(0, sx)) / (M_PER_DEG * math.cos(math.radians(v["lat"]))), 7)
            out.append(v)
        return back * TICK_S, out
