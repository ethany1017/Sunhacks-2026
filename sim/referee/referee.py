"""The Referee: part of the simulator, always on. It judges the TRUE state every tick, whoever is controlling.

It never imports controller code and never reads controller reports (decisions, voice lines): only aircraft truth,
runway occupancy, the clearances actually issued and acknowledged, and the engine's own truth-based events.
A fail is recorded the moment a rule is broken (one record per ongoing violation); the run keeps going so the score is
complete, but the verdict is locked to FAIL. Limits and their sources: sim/referee/limits.py.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from sim.core.entities import GROUND_PHASES, STATIONARY_PHASES, Aircraft
from sim.core.physics import HOLD_OFFSET_M, unit
from sim.referee import limits as L

if TYPE_CHECKING:
    from sim.core.engine import Simulation

RULES = ("collision", "loss_of_separation", "runway_occupied", "runway_incursion", "diversion", "stuck", "unhandled_at_end")
ENTRY_ACTIONS = ("cross_runway", "line_up_and_wait", "cleared_for_takeoff")
ROLLING = ("landing_roll", "takeoff_roll")
Pending = Dict[Tuple[Any, ...], Tuple[str, List[str], str]]


def _nm(m: float) -> float:
    return m / L.NM


def runway_spacing(sim: "Simulation", pa: Optional[str], pb: Optional[str]) -> float:
    """Lateral distance between two runway centrelines in metres (0 for the same runway or an unknown one)."""
    if not pa or not pb or pa == pb:
        return 0.0
    ea = next(iter(sim.graph.runways[pa].ends.values()))
    eb = next(iter(sim.graph.runways[pb].ends.values()))
    u = unit(ea["heading_deg"])
    d = sim.graph.node_xy[eb["node"]] - sim.graph.node_xy[ea["node"]]
    return abs(float(d[0] * u[1] - d[1] * u[0]))


def air_loss(sim: "Simulation", a: Aircraft, b: Aircraft) -> Optional[Tuple[float, float, str]]:
    """(distance_m, minimum_m, standard) if two airborne arrivals (final / go-around) are below the separation standard."""
    d = math.hypot(a.x - b.x, a.y - b.y)
    dz = abs(a.alt_m - b.alt_m)
    if d < L.NMAC_M and dz < L.NMAC_M:
        return d, L.NMAC_M, "near mid-air collision, AIM 7-6-3"
    if dz >= L.VERTICAL_MIN_M:
        return None
    gap = runway_spacing(sim, sim.runway_pavement(a.runway), sim.runway_pavement(b.runway))
    if gap >= L.PARALLEL_INDEPENDENT_M:
        return None
    if gap >= L.PARALLEL_SINGLE_M:
        minimum = L.DIAGONAL_NARROW_M if gap <= L.PARALLEL_WIDE_M else L.DIAGONAL_WIDE_M
        std = "dependent parallel approaches, 7110.65 5-9-6"
    elif a.phase == "final" and b.phase == "final":
        lead, follow = sorted((a, b), key=sim.physics.approach_distance)
        wake = L.WAKE_NM.get(lead.type.cwt, {}).get(follow.type.cwt, 0.0) * L.NM
        minimum = max(L.RADAR_MIN_M, wake)
        std = ("wake turbulence, CWT %s behind %s, 7110.65 5-5-4h" % (follow.type.cwt, lead.type.cwt) if wake > L.RADAR_MIN_M
               else "radar minimum, 7110.65 5-5-4a")
    else:
        minimum, std = L.RADAR_MIN_M, "radar minimum, 7110.65 5-5-4a"
    return (d, minimum, std) if d < minimum else None


def footprints_overlap(a: Aircraft, b: Aircraft) -> bool:
    """Separating-axis test on two rectangles (length x wingspan, turned to each heading)."""
    boxes = []
    for ac in (a, b):
        f = unit(ac.heading)
        boxes.append(((f[0], f[1]), (f[1], -f[0]), ac.length_m / 2, ac.wingspan_m / 2))
    dx, dy = b.x - a.x, b.y - a.y
    for f, r, _, _ in boxes:
        for n in (f, r):
            reach = sum(hl * abs(fx * n[0] + fy * n[1]) + hw * abs(rx * n[0] + ry * n[1])
                        for (fx, fy), (rx, ry), hl, hw in boxes)
            if abs(dx * n[0] + dy * n[1]) > reach:
                return False
    return True


class Referee:
    def __init__(self, sim: "Simulation") -> None:
        self.sim = sim
        self.day_end_s = sim.scenario.duration_s + L.END_OF_DAY_GRACE_S
        self.fails: List[Dict[str, Any]] = []
        self.result: Optional[Dict[str, Any]] = None
        self._open: Set[Tuple[Any, ...]] = set()        # ongoing pair violations, one record each until they clear
        self._once: Set[Tuple[Any, ...]] = set()        # stuck episodes already recorded
        self._holds: Dict[str, Tuple[Any, Set[int]]] = {}   # callsign -> (path, run indices whose hold point it has passed)

    # -- public ----------------------------------------------------------------------------------------------------------
    @property
    def verdict(self) -> str:
        if self.fails:
            return "FAIL"
        return "PASS" if self.result is not None else "RUNNING"

    def score(self) -> Dict[str, Any]:
        s = dict(self.sim.score())
        s["fails"] = len(self.fails)
        s["fails_by_rule"] = {r: sum(1 for f in self.fails if f["rule"] == r) for r in RULES}
        return s

    def report(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "fails": [dict(f) for f in self.fails], "score": self.score(),
                "day_end_s": self.day_end_s}

    def tick(self, events: List[Dict[str, Any]]) -> None:
        """Judge this tick. `events` are the engine events emitted so far in this step (truth, not controller reports)."""
        if self.result is not None:
            return
        for e in events:
            kind, d, cs = e["kind"], e["data"], e["entity_id"]
            if kind == "separation_loss" and d["kind"] == "air":
                self._fail("loss_of_separation", [d["a"], d["b"]], "%s and %s were %.2f NM apart in the air; the minimum is %.2f NM (%s)"
                           % (d["a"], d["b"], math.floor(_nm(d["distance_m"]) * 100) / 100,   # truncated: 2.996 reads 2.99,
                              _nm(d["minimum_m"]), d.get("standard", "")))                       # not "3.00 ... minimum 3.00"
            elif kind == "deadlock":
                self._fail("stuck", list(d["aircraft"]), "%s are gridlocked on the taxiways, each waiting for the other (%.0f s)"
                           % (" and ".join(d["aircraft"]), d["waited_s"]))
            elif kind == "aircraft.diverted":
                self._fail("diversion", [cs], "%s diverted after holding %.0f s (limit %.0f s)" % (cs, d["holding_s"], d["limit_s"]))
            elif kind == "aircraft.touchdown":
                if not self._authorised(cs, d["runway"], ("cleared_to_land",)):
                    self._fail("runway_incursion", [cs], "%s landed on runway %s without a landing clearance" % (cs, d["runway"]))
                self._airborne_gap(cs, d["runway"], "landed")
            elif kind == "takeoff.roll":
                self._airborne_gap(cs, d["runway"], "started its takeoff roll")
        live = [a for a in self.sim.aircraft.values() if not a.removed]
        now: Pending = {}
        self._collisions(live, now)
        self._runways(now)
        for key, (rule, callsigns, reason) in now.items():
            if key not in self._open:
                self._fail(rule, callsigns, reason)
        self._open = set(now)
        self._incursions(live)
        self._stuck(live)

    def finish(self) -> None:
        """End of the day (all traffic handled, or the day-end time reached): rule 7, then the locked result."""
        if self.result is not None:
            return
        from sim.core.views import status_text
        sim = self.sim
        left = [a for a in sim.aircraft.values() if a.kind != "vehicle" and not a.done and not a.removed]
        names = [a.callsign for a in left] + [f.callsign for _, _, f in sim.pending]
        detail = ["%s (%s)" % (a.callsign, status_text(sim, a)) for a in left]
        detail += ["%s (never arrived)" % f.callsign for _, _, f in sim.pending]
        if names:
            self._fail("unhandled_at_end", names, "%d aircraft were still not handled when the day ended at t=%.0f s: %s"
                       % (len(names), sim.t, ", ".join(detail)))
        self.result = {"verdict": "FAIL" if self.fails else "PASS", "fails": [dict(f) for f in self.fails], "score": self.score()}
        sim.emit("referee_result", None, self.result)

    # -- rules -----------------------------------------------------------------------------------------------------------
    def _fail(self, rule: str, callsigns: List[str], reason: str) -> None:
        f = {"rule": rule, "callsigns": list(callsigns), "reason": reason, "t": round(self.sim.t, 1)}
        self.fails.append(f)
        self.sim.emit("referee_fail", callsigns[0] if callsigns else None, dict(f))

    def _authorised(self, cs: str, pavement: str, actions: Tuple[str, ...]) -> bool:
        sim = self.sim
        return any(c.state == "acknowledged" and c.action in actions and sim.runway_pavement(c.params.get("runway")) == pavement
                   for c in sim.active.get(cs, []))

    def _collisions(self, live: List[Aircraft], now: Pending) -> None:
        for i in range(len(live)):
            a = live[i]
            for b in live[i + 1:]:
                if a.phase in STATIONARY_PHASES and b.phase in STATIONARY_PHASES:
                    continue
                if abs(a.alt_m - b.alt_m) >= L.COLLISION_VERTICAL_M:
                    continue
                if math.hypot(a.x - b.x, a.y - b.y) > (a.length_m + a.wingspan_m + b.length_m + b.wingspan_m) / 2:
                    continue
                if footprints_overlap(a, b):
                    pair = sorted((a.callsign, b.callsign))
                    where = "on the ground" if max(a.alt_m, b.alt_m) < 3.0 else "in the air"
                    now[("collision",) + tuple(pair)] = ("collision", pair, "%s and %s collided %s (their footprints overlap)"
                                                         % (pair[0], pair[1], where))

    def _runways(self, now: Pending) -> None:
        sim = self.sim
        for rw in sim.graph.runways:
            occ = sim.res.occupants(rw)
            since = sim.res.open[rw]
            for i in range(len(occ)):
                for j in range(i + 1, len(occ)):
                    ph = {cs: getattr(sim.aircraft.get(cs), "phase", None) for cs in (occ[i], occ[j])}
                    roller = next((cs for cs in (occ[i], occ[j]) if ph[cs] in ROLLING), None)
                    if roller is None:
                        continue
                    other = occ[j] if roller == occ[i] else occ[i]
                    if ph[roller] == "takeoff_roll":
                        reason = "%s was on its takeoff roll on runway %s while %s was on the runway" % (roller, rw, other)
                    elif since[roller] >= since[other]:
                        reason = "%s landed on runway %s while %s was still on it" % (roller, rw, other)
                    else:
                        reason = "%s entered runway %s during %s's landing roll" % (other, rw, roller)
                    now[("runway_occupied", rw) + tuple(sorted((roller, other)))] = ("runway_occupied", [roller, other], reason)

    def _airborne_gap(self, cs: str, pavement: str, what: str) -> None:
        """A preceding departure that has lifted off no longer occupies the runway, but must be far enough ahead."""
        sim = self.sim
        me = sim.aircraft.get(cs)
        if me is None:
            return
        for o in sim.aircraft.values():
            if o is me or o.removed or o.phase != "airborne" or sim.runway_pavement(o.dep_end) != pavement:
                continue
            need = L.AIRBORNE_GAP_LIGHT_M if me.type.light and o.type.light else L.AIRBORNE_GAP_M
            d = math.hypot(o.x - me.x, o.y - me.y)
            if d < need:
                self._fail("runway_occupied", [cs, o.callsign], "%s %s on runway %s only %.0f ft behind departing %s (needs %.0f ft)"
                           % (cs, what, pavement, d / L.FT, o.callsign, need / L.FT))

    def _incursions(self, live: List[Aircraft]) -> None:
        """Passing a runway hold point needs an acknowledged cross / line-up / takeoff clearance for that runway."""
        for ac in live:
            p = ac.path
            if ac.kind == "vehicle" or p is None or ac.phase not in GROUND_PHASES + ("landing_roll",):
                continue
            seen = self._holds.get(ac.callsign)
            fresh = seen is None or seen[0] is not p        # a new path: hold points already behind it were judged before
            past: Set[int] = set() if fresh else seen[1]
            half = ac.length_m / 2
            for i, run in enumerate(p.runs):
                if i in past or ac.s <= run.s0 - half - HOLD_OFFSET_M + L.HOLD_LINE_TOLERANCE_M:
                    continue
                past.add(i)
                if not fresh and not self._authorised(ac.callsign, run.runway, ENTRY_ACTIONS):
                    self._fail("runway_incursion", [ac.callsign], "%s passed the hold point for runway %s without a clearance "
                               "to cross, line up or take off" % (ac.callsign, run.runway))
            self._holds[ac.callsign] = (p, past)

    def _stuck(self, live: List[Aircraft]) -> None:
        from sim.core.views import status_text
        t = self.sim.t
        for ac in live:
            if ac.kind == "vehicle" or ac.done:
                continue
            if ac.phase == "at_gate" and ac.ready and t - ac.scheduled_s >= L.STUCK_AT_GATE_S:
                key: Tuple[Any, ...] = ("gate", ac.callsign)
                reason = "%s has been ready at gate %s for %.0f s and was never pushed back" % (ac.callsign, ac.gate, t - ac.scheduled_s)
            elif ac.stopped_since is not None and t - ac.stopped_since >= L.STUCK_STOPPED_S:
                key = ("stopped", ac.callsign, ac.stopped_since)
                reason = "%s has not moved for %.0f s (%s)" % (ac.callsign, t - ac.stopped_since, status_text(self.sim, ac))
            else:
                continue
            if key not in self._once:
                self._once.add(key)
                self._fail("stuck", [ac.callsign], reason)
