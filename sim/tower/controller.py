"""The airport's built-in rule-based tower: first-come-first-served sequencing through the Simulation tools.

It sees only what a real tower sees: observe() (delayed, noisy positions, the clearance list, pending readbacks), the
published airport graph and the tool methods. No snapshot(), no sim.aircraft, no readback truth, no Referee."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from sim.core.aircraft_types import get_type
from sim.core.engine import Simulation
from sim.levels import TAILWIND_LIMIT_KT, tailwind_kt

DECIDE_EVERY_TICKS = 5          # 2 Hz decision pass
LAND_CLEAR_ETA_S = 70.0         # clear to land inside this ETA when the runway is free
GO_AROUND_ETA_S = 22.0          # no clearance and runway still blocked this close in: go around
ARRIVAL_SLOT_S = 100.0           # minimum spacing between landings on one runway (sim separation is 2.5 nm)
LINE_UP_GUARD_S = 100.0         # no line-up if a landing is due within this
TAKEOFF_GUARD_S = 55.0          # no takeoff roll if a landing is due within this
CROSS_GUARD_S = 90.0            # no crossing if a landing on that runway is due within this
CROSS_WINDOW_S = 45.0
FAR_SIDE_M = 150.0              # no crossing while an aircraft stands on the first this-many metres after the runway
PUSHBACK_CLEAR_M = 90.0
PUSH_DELAY_MAX_S = 200.0        # ... unless the departure has waited at the gate this long
FLOW_SWITCH_TAILWIND_KT = 5.0   # turn the airport around once the active direction has more tailwind than this
OPPOSING_PENALTY_M = 2000.0     # taxi routing: an edge another cleared route uses the other way counts this much longer
CROSSING_PENALTY_M = 2500.0     # taxi routing: per runway-pavement edge, so a route never trades a crossing for a detour
REPEAT_CROSSING_PENALTY_M = 20000.0   # taxi routing: per edge of a runway the route would otherwise use twice
PROBE_S = 20.0                  # ground conflict probe: look this far ahead along each cleared taxi route
STOP_CLEAR_S = 60.0             # a held aircraft must stop clear of the other's route this far ahead, not just the probe window
PROBE_MARGIN_M = 5.0            # margin the probe keeps between two taxiing aircraft beyond their half diagonals
RELEASE_GRACE_S = 10.0          # a just-released aircraft counts as moving (it is about to) for this long
HOLD_MAX_S = 90.0               # a probe hold is released after this long even if the traffic is still there
RESUME_TAXI_SPEED = 7.7         # m/s a held aircraft is assumed to reach (accelerating from rest) when it may resume
TAXI_ACCEL = 0.9                # m/s^2 a taxiing aircraft is assumed to pick up speed at
RELEASE_EXTRA_M = 15.0          # a hold is only released with this much more room than the margin (no hold/resume flapping)
EXIT_SPEED_MPS = 10.0           # speed at the runway exit (the simulator's pilots: sim/core/physics.py EXIT_SPEED)
EXIT_TAXI_WEIGHT = 0.1          # exit choice: one second of runway occupancy is worth ten seconds of taxi to the gate
UNJAM_AFTER_S = 15.0            # two aircraft waiting for each other this long: reroute one of them
UNJAM_RETRY_S = 45.0            # and do not try again for the same aircraft sooner than this
PUSH_KEEP_CLEAR_M = 60.0        # taxi routing: keep this far from an aircraft pushing back while another way exists
TAXI_WAIT_S = 60.0              # a new taxi clearance waits up to this long for a way that is not head-on into traffic
FLOW_FINISH_ETA_S = 150.0       # on a flow change, arrivals closer than this finish on the old direction (if still legal)
CLAIMS = ("cleared_to_land", "cross_runway", "line_up_and_wait", "cleared_for_takeoff")   # clearances that hold a runway
RULES = {
    "taxi": "JO 7110.65BB 3-7-2 Taxi and ground movement operations: issue the route up to the runway hold short point",
    "cross": "JO 7110.65BB 3-7-2: a runway crossing needs its own clearance; hold short until the runway is clear",
    "luaw": "JO 7110.65BB 3-9-4 Line up and wait: only when the runway will be clear for the departure",
    "takeoff": "JO 7110.65BB 3-9-10 Takeoff clearance: runway clear of traffic, no landing inside the guard time",
    "same_rwy_dep": "JO 7110.65BB 3-9-6 Same runway separation: preceding departure airborne, wake spacing applied",
    "land": "JO 7110.65BB 3-10-5 Landing clearance: runway clear of conflicting traffic before touchdown",
    "land_withhold": "JO 7110.65BB 3-10-8 Withholding landing clearance: go around when the runway is not clear",
    "readback": "JO 7110.65BB 3-7-2: request a corrected readback for hold short and runway instructions",
}


def departure_gap_s(leader_wtc: str, follower_wtc: str) -> float:
    """Time from one takeoff roll to the next on one runway (JO 7110.65 5-8-3: 2 min behind a heavy; otherwise about one
    minute, which also keeps the Referee's 6,000 ft airborne gap; see docs/ASSUMPTIONS.md)."""
    if leader_wtc.startswith("H"):
        return 120.0
    if follower_wtc.startswith("L") and leader_wtc.startswith("M"):
        return 90.0
    return 60.0


def _headings(f: np.ndarray, h0: float) -> np.ndarray:
    """Heading (rad) along a predicted track; where it does not move, the current heading."""
    d = np.diff(f, axis=0)
    h = np.arctan2(d[:, 0], d[:, 1])
    still = np.hypot(d[:, 0], d[:, 1]) < 0.2
    h = np.concatenate([h, h[-1:]]) if len(h) else np.array([math.radians(h0)])
    h[np.concatenate([still, still[-1:]]) if len(still) else [True]] = math.radians(h0)
    return h


def _boxes_meet(fa: np.ndarray, a: Dict[str, Any], fb: np.ndarray, b: Dict[str, Any], margin: float) -> np.ndarray:
    """Per predicted second: do the two footprints (length x wingspan rectangles turned along their tracks, grown by
    `margin`) overlap? Separating-axis test, vectorised over time."""
    ha, hb = _headings(fa, a["heading_deg"]), _headings(fb, b["heading_deg"])
    axes = [np.stack([np.sin(h), np.cos(h)], -1) for h in (ha, hb)] + [np.stack([np.cos(h), -np.sin(h)], -1) for h in (ha, hb)]
    fwd_a, fwd_b, side_a, side_b = axes
    d = fb - fa
    apart = np.zeros(len(fa), dtype=bool)
    for n in axes:
        reach = ((a["length_m"] / 2 + margin) * np.abs((fwd_a * n).sum(-1)) + (a["wingspan_m"] / 2 + margin) * np.abs((side_a * n).sum(-1))
                 + (b["length_m"] / 2 + margin) * np.abs((fwd_b * n).sum(-1)) + (b["wingspan_m"] / 2 + margin) * np.abs((side_b * n).sum(-1)))
        apart |= np.abs((d * n).sum(-1)) > reach
    return ~apart


class TowerController:
    name = "tower"
    status = "idle"

    def __init__(self) -> None:
        self.assigned: Dict[str, Dict[str, Any]] = {}
        self._ticks = 0
        self.order: Dict[str, int] = {}                   # first-come-first-served order of first sighting
        self.expected: Dict[str, Optional[str]] = {}      # clearance_id -> runway end the readback must contain
        self.last_arrival_slot: Dict[str, float] = {}     # runway pavement -> last landing slot time
        self.last_airborne: Dict[str, Tuple[float, str]] = {}   # pavement -> (t, wake class)
        self.claims: Dict[Tuple[str, str], str] = {}      # (pavement, callsign) -> clearance id that holds the runway
        self.routes: Dict[str, List[str]] = {}            # callsign -> taxi route nodes this tower cleared (until done)
        self.wtc: Dict[str, str] = {}                     # callsign -> wake class, from the observed aircraft type
        self.cross_ids: set = set()                       # clearance ids of runway crossings this tower issued
        self._issued_now: set = set()                     # callsigns given a clearance during this decision pass
        self.ready_at: Dict[str, float] = {}              # callsign -> when the tower first saw it ready at the gate
        self.taxi_wait: Dict[str, float] = {}             # callsign -> when its taxi clearance was first held back
        self.unjammed: Dict[str, float] = {}              # callsign -> when it was last rerouted out of a face-off
        self.ground_hold: Dict[str, str] = {}
        self.hold_since: Dict[str, float] = {}
        self.released: Dict[str, float] = {}              # callsign -> when the probe last let it resume            # callsign -> when the probe told it to hold position             # callsign told to hold position -> the traffic it waits for
        self._tracks: Dict[str, Tuple[Tuple[str, ...], np.ndarray, np.ndarray]] = {}   # callsign -> (nodes, pts, cum)

    # -- entry point -----------------------------------------------------------------------------------
    def on_tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        for ev in events:
            if ev["kind"] == "takeoff.roll":      # departure spacing counts from the start of the previous takeoff roll
                cs = ev["entity_id"]
                self.last_airborne[ev["data"]["runway"]] = (ev["sim_time_ms"] / 1000.0, self.wtc.get(cs, "M"))
            if ev["kind"] == "clearance.issued":   # the runway the readback must contain, as the clearance said it
                p = ev["data"]["params"]
                self.expected[ev["data"]["clearance_id"]] = p.get("runway") or p.get("departure_runway") or (p.get("hold_short") or [None])[0]
            if ev["kind"] == "aircraft.airborne":
                self.assigned.setdefault(ev["entity_id"], {})["airborne"] = True
            if ev["kind"] in ("aircraft.airborne", "aircraft.parked"):
                self.routes.pop(ev["entity_id"], None)
            if ev["kind"] == "pilot.unable":       # refused for tailwind: forget the clearance so it is planned again
                st = self.assigned.setdefault(ev["entity_id"], {})
                for k in {"cleared_to_land": ("landing",), "line_up_and_wait": ("luaw",),
                          "cleared_for_takeoff": ("to",)}.get(ev["data"]["action"], ()):
                    st.pop(k, None)
        self._ticks += 1                       # on_tick runs once per sim tick: count them rather than read the clock
        decide = self._ticks % DECIDE_EVERY_TICKS == 0
        obs: Optional[Dict[str, Any]] = None
        if any(e["kind"] == "readback" for e in events) or decide:
            obs = sim.observe()
        if obs is None:
            return
        live = {c["id"] for c in obs["clearances"]}
        self.claims = {k: cid for k, cid in self.claims.items() if cid in live}
        self._readbacks(sim, obs)
        if decide:
            self._flow(sim, obs)
            by_cs = {a["callsign"]: a for a in obs["aircraft"] if a["kind"] != "vehicle"}
            for cs in sorted((c for c in by_cs if c not in self.order), key=lambda c: (by_cs[c]["eta_s"] or 0.0, c)):
                self.order[cs] = len(self.order)
            for a in by_cs.values():
                self.wtc[a["callsign"]] = get_type(a["type"]).wtc
                nodes = self.routes.get(a["callsign"])
                if nodes and a.get("next_node") in nodes:    # forget the part of a cleared route already taxied
                    self.routes[a["callsign"]] = nodes[max(nodes.index(a["next_node"]) - 1, 0):]
            for cs in sorted(by_cs, key=lambda c: self._priority(by_cs[c])):
                a = by_cs[cs]
                if a["kind"] == "arrival":
                    self._arrival(sim, obs, a)
                else:
                    self._departure(sim, obs, a)
            busy = self._issued_now | {c["callsign"] for c in obs["clearances"] if c["state"] == "issued"}
            self._ground_probe(sim, by_cs, busy, obs["t"])
            self._unjam(sim, obs, by_cs, busy)
            self._issued_now = set()

    def _priority(self, a: Dict[str, Any]) -> Tuple[int, float]:
        """Who gets a runway first this pass: aircraft waiting to cross (they block exits and taxiways), then departures
        at the hold point in the order they got there, then everyone else first come, first served."""
        if a.get("hold"):
            return (0 if a["kind"] == "arrival" else 1, -float(a.get("stopped_s") or 0.0))
        return (2, float(self.order[a["callsign"]]))

    # -- helpers ------------------------------------------------------------------------------------------
    def _issue(self, sim: Simulation, a: Dict[str, Any], action: str, params: Dict[str, Any], summary: str, facts: List[str],
               rules: List[str], alternatives: List[Dict[str, str]], review_s: float) -> Optional[Dict[str, Any]]:
        res = sim.issue_clearance(a["callsign"], action, params)
        if not res["ok"]:
            sim.record_decision(callsign=a["callsign"], action=action, summary="Could not issue %s: %s" % (action, res["error"]),
                                facts=facts, rules=rules, alternatives=alternatives, next_review_s=review_s)
            return None
        self._issued_now.add(a["callsign"])
        if action in ("taxi_via", "cross_runway"):
            self.ground_hold.pop(a["callsign"], None)     # a new route or crossing replaces a hold position
        if action == "taxi_via":
            self.routes[a["callsign"]] = list(params.get("route") or [])
        pav = sim.runway_pavement(params.get("runway")) if action in CLAIMS else None
        if pav:
            self.claims[(pav, a["callsign"])] = res["clearance_id"]
            if action == "cross_runway":
                self.cross_ids.add(res["clearance_id"])
        sim.record_decision(callsign=a["callsign"], action=action, summary=summary, facts=facts, rules=rules,
                            alternatives=alternatives, next_review_s=review_s, voice_id=res["voice_id"])
        return res

    def _taxi_params(self, sim: Simulation, a: Dict[str, Any], to: str, dst: Optional[str],
                     origin: Optional[str] = None, obs: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """An explicit route that avoids taxiing head-on into a route already cleared the other way (ground control's
        one-way flows) and past an aircraft pushing back, or the simulator's shortest route if there is no usable node to
        start from. With `obs` (a new taxi clearance that may wait) None while the only way is head-on into other
        traffic, for up to TAXI_WAIT_S."""
        import networkx as nx
        origin = origin or a.get("next_node")
        if not origin or not dst:
            return {"to": to}
        g = sim.graph
        against = {(v, u) for cs, nodes in self.routes.items() if cs != a["callsign"] for u, v in zip(nodes, nodes[1:])}
        pushing = [np.array(g.frame.to_xy(o["lon"], o["lat"])) for o in (obs or {}).get("aircraft", [])
                   if o["phase"] == "pushback" and o["callsign"] != a["callsign"]]
        blocked = {n for n, xy in g.node_xy.items() for p in pushing if math.hypot(xy[0] - p[0], xy[1] - p[1]) < PUSH_KEEP_CLEAR_M}

        def weight(hard: bool, avoid: frozenset) -> Any:
            def w(u: str, v: str, d: Dict[str, Any]) -> Optional[float]:
                e = g.edges[d["eid"]]
                if e.kind == "runway" or (hard and ((u, v) in against or v in blocked)):
                    return None
                return (e.length + (CROSSING_PENALTY_M if e.pavement else 0.0) + (OPPOSING_PENALTY_M if (u, v) in against else 0.0)
                        + (REPEAT_CROSSING_PENALTY_M if e.pavement in avoid else 0.0))
            return w

        def twice(route: List[str]) -> frozenset:
            runs = [r.runway for r in g.build_path(route).runs]
            return frozenset(p for p in runs if runs.count(p) > 1)
        fallback: Optional[List[str]] = None
        for hard in (True, False):      # never head-on into another cleared route if any other way exists
            try:
                route = nx.dijkstra_path(g.g, origin, dst, weight=weight(hard, frozenset()))   # (u, v) is the direction
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                if hard and obs is not None and obs["t"] - self.taxi_wait.setdefault(a["callsign"], obs["t"]) < TAXI_WAIT_S:
                    return None         # a new clearance can wait a little for the way to clear
                continue
            again = twice(route)
            if again:                   # onto the same runway twice (a crossing, then the departure entry): find another way
                try:
                    route = nx.dijkstra_path(g.g, origin, dst, weight=weight(hard, again))
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    pass
            if not twice(route):
                return {"route": route, "to": to}
            fallback = fallback or route
        return {"route": fallback, "to": to} if fallback else {"to": to}

    @staticmethod
    def _runway(obs: Dict[str, Any], pav: str) -> Dict[str, Any]:
        return next(r for r in obs["runways"] if r["id"] == pav)

    def _blockers(self, obs: Dict[str, Any], pav: str, me: str, t0: float, t1: float, ignore: Tuple[str, ...] = ()) -> List[str]:
        """Who occupies or has reserved this pavement during [t0, t1] (absolute sim seconds)."""
        out = ["%s cleared" % cs for (p, cs) in self.claims if p == pav and cs != me and cs not in ignore]
        for w in self._runway(obs, pav)["occupancy"]:
            if w["callsign"] == me or w["callsign"] in ignore:
                continue
            if w["kind"] == "occupied" and w["end_s"] >= obs["t"] - 0.15:   # still on the pavement
                out.append("%s on runway" % w["callsign"])
            elif w["kind"] == "reserved" and w["start_s"] < t1 and w["end_s"] > t0:
                out.append("%s reserved" % w["callsign"])
        return sorted(set(out))

    def _landing_due(self, obs: Dict[str, Any], pav: str, within_s: float, me: str) -> Optional[Dict[str, Any]]:
        for o in obs["aircraft"]:
            if (o["kind"] == "arrival" and o["phase"] == "final" and o["callsign"] != me and o.get("arrival_end")
                    and o["runway"] == pav and (o["eta_s"] or 999) <= within_s):
                return o
        return None

    # -- ground conflict probe ------------------------------------------------------------------------------
    def _future(self, sim: Simulation, a: Dict[str, Any], speed: float, from_rest: bool = False, secs: float = PROBE_S) -> np.ndarray:
        """Where this aircraft will be each second for PROBE_S seconds at `speed` (or accelerating from rest to it):
        along its cleared route when the tower knows it, else straight ahead on its heading."""
        g = sim.graph
        here = np.array(g.frame.to_xy(a["lon"], a["lat"]), dtype=float)
        nodes = self.routes.get(a["callsign"]) or []
        nxt = a.get("next_node")
        v0 = 0.0 if from_rest else speed
        top = RESUME_TAXI_SPEED if (from_rest or 0.3 < speed < RESUME_TAXI_SPEED) else speed   # a slow mover speeds up
        v = np.minimum(v0 + TAXI_ACCEL * (np.arange(int(secs)) + 0.5), max(top, v0))
        t = np.concatenate([[0.0], np.cumsum(v)])
        if nxt in nodes and a["phase"] != "pushback":
            rest = tuple(nodes[nodes.index(nxt):])
            cached = self._tracks.get(a["callsign"])
            if cached is None or cached[0] != rest:
                pts = np.vstack([g.build_path(list(rest)).pts]) if len(rest) > 1 else np.array([g.node_xy[nxt]])
                self._tracks[a["callsign"]] = cached = (rest, pts, np.zeros(0))
            pts = np.vstack([here, cached[1]])
        elif a["phase"] == "pushback" and a.get("gate") in g.stands:
            pts = np.vstack([here, g.node_xy[g.stands[a["gate"]].lane_node]])   # backing out onto the lane
        else:
            h = math.radians(a["heading_deg"])
            pts = np.vstack([here, here + np.array([math.sin(h), math.cos(h)]) * (secs * max(speed, 1.0))])
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        t = np.minimum(t, cum[-1])
        return np.stack([np.interp(t, cum, pts[:, 0]), np.interp(t, cum, pts[:, 1])], axis=-1)

    def _ground_probe(self, sim: Simulation, by_cs: Dict[str, Dict[str, Any]], busy: set, obs_t: float) -> None:
        """Ground control's scan: two taxiing aircraft whose next 20 s bring their wingtips together (head-on on one
        taxiway, a close pass at a dog-leg, a taxi past a pushback) get a hold for one of them until the other has passed.
        Same-direction traffic is left to the pilots (they follow at a safe distance)."""
        ground = [a for a in by_cs.values() if a["phase"] in ("taxi_in", "taxi_out", "runway_exit", "pushback", "hold_short", "line_up")
                  and not a.get("done")]
        # a hold or resume would supersede a taxi clearance not yet read back, so leave those aircraft for the next pass
        # (and either would cancel or freeze a runway clearance, so never while one is live)
        crossing = {cs for (p, cs) in self.claims}      # any live runway clearance (crossing, line up, takeoff, landing)
        holdable = lambda a: (a["phase"] in ("taxi_in", "taxi_out") and a["callsign"] not in crossing and not a.get("hold")
                              and a["callsign"] not in busy)
        starting = {cs for cs, t0 in self.released.items() if obs_t - t0 < RELEASE_GRACE_S}     # resumed, still picking up speed
        moving = lambda a: a["speed_mps"] > 0.3 or (a["callsign"] in starting and a["phase"] in ("taxi_in", "taxi_out", "hold_short"))
        fut = {a["callsign"]: self._future(sim, a, a["speed_mps"], from_rest=a["speed_mps"] <= 0.3) if moving(a)
               else self._future(sim, a, a["speed_mps"] if a["phase"] == "pushback" else 0.0) for a in ground}
        if any(a["phase"] == "pushback" and a["speed_mps"] < 0.05 for a in ground):   # still at the stand: not moving yet
            for a in ground:
                if a["phase"] == "pushback" and a["speed_mps"] < 0.05:
                    fut[a["callsign"]] = fut[a["callsign"]][:1].repeat(len(fut[a["callsign"]]), axis=0)

        def reach(a: Dict[str, Any]) -> float:
            return math.hypot(a["length_m"], a["wingspan_m"]) / 2.0     # a turning aircraft sweeps its half diagonal

        def clash(a: Dict[str, Any], b: Dict[str, Any], fa: np.ndarray, fb: np.ndarray, now_ok: bool = False) -> bool:
            if not moving(a):
                return False
            if not moving(b) and b["phase"] != "pushback" and b.get("blocked_by") == a["callsign"]:
                return False                                    # it is waiting for this one: this one must go first
            meet = _boxes_meet(fa, a, fb, b, PROBE_MARGIN_M + (RELEASE_EXTRA_M if now_ok else 0.0))
            return bool(meet.any() and (now_ok or not meet[0]))   # already touching: nothing a hold can fix now

        def stop_at(a: Dict[str, Any]) -> np.ndarray:
            """Where a hold issued now would stop it: about 3 s for the readback, then braking."""
            f = fut[a["callsign"]]
            return f[min(len(f) - 1, int(math.ceil(3.0 + a["speed_mps"] / 2.6)))]   # 3 s at speed + v^2 / (2 x 1.3)
        # release holds whose traffic has passed
        for cs, other in list(self.ground_hold.items()):
            a = by_cs.get(cs)
            if a is None or a["phase"] not in ("taxi_in", "taxi_out", "hold_short"):    # stopped by a hold: hold_short
                self.ground_hold.pop(cs, None)
                continue
            if cs in busy or cs in crossing:
                continue
            fa = self._future(sim, a, RESUME_TAXI_SPEED, from_rest=True)
            going = dict(a, speed_mps=RESUME_TAXI_SPEED)
            held_long = obs_t - self.hold_since.get(cs, obs_t) > HOLD_MAX_S     # it will not clear by waiting: go on
            if held_long or not any(clash(going, b, fa, fut[b["callsign"]], now_ok=True) for b in ground if b["callsign"] != cs):
                self.ground_hold.pop(cs)
                self.released[cs] = obs_t
                starting.add(cs)            # later checks this pass see it on the move
                fut[cs] = fa
                self._issue(sim, a, "resume_taxi", {}, "%s resume taxi: %s has passed" % (cs, other),
                            ["No traffic within %.0f m of %s's route in the next %.0f s" % (PROBE_MARGIN_M, cs, PROBE_S)],
                            [RULES["taxi"]], [], 10)
        ahead: Dict[str, np.ndarray] = {}
        for i, a in enumerate(ground):
            for b in ground[i + 1:]:
                ca, cb = a["callsign"], b["callsign"]
                if ca in self.ground_hold or cb in self.ground_hold or not clash(a, b, fut[ca], fut[cb]):
                    continue
                # hold the one whose stopping point is clear of the other's path (it waits, the other passes)
                def clear_of_all(x: Dict[str, Any], y: Dict[str, Any]) -> bool:
                    """x's stopping point is off y's route and off the route of every other aircraft on the move."""
                    for z in ground:
                        cz = z["callsign"]
                        if cz == x["callsign"] or (z is not y and not moving(z)):
                            continue
                        if cz not in ahead:
                            ahead[cz] = self._future(sim, z, RESUME_TAXI_SPEED, secs=STOP_CLEAR_S)   # its route, however slow now
                        if float(np.min(np.hypot(*(ahead[cz] - stop_at(x)).T))) < reach(x) + reach(z):
                            return False
                    return True
                cands = [x for x, y in ((a, b), (b, a)) if holdable(x) and x["speed_mps"] > 0.3 and clear_of_all(x, y)]
                if not cands:
                    continue                                     # a hold would only park one in the other's way
                who = sorted(cands, key=lambda x: (x["kind"] == "arrival", -self.order[x["callsign"]]))[0]
                other = cb if who is a else ca
                self.ground_hold[who["callsign"]] = other
                self.hold_since[who["callsign"]] = obs_t
                self._issue(sim, who, "hold_position", {}, "%s hold position for %s" % (who["callsign"], other),
                            ["%s and %s would pass closer than %.0f m within %.0f s" % (ca, cb, PROBE_MARGIN_M, PROBE_S)],
                            [RULES["taxi"]], [{"option": "let both continue", "reason": "wingtip conflict"}], 5)

    def _unjam(self, sim: Simulation, obs: Dict[str, Any], by_cs: Dict[str, Dict[str, Any]], busy: set) -> None:
        """Two aircraft stopped nose to nose, each waiting for the other: give one of them a new route that keeps clear of
        the other (turning round where it stands if needed). Ground control's way out of a face-off."""
        import networkx as nx
        g = sim.graph
        t = obs["t"]
        for a in by_cs.values():
            b = by_cs.get(a.get("blocked_by") or "")
            if (b is None or b.get("blocked_by") != a["callsign"] or a["speed_mps"] > 0.1 or b["speed_mps"] > 0.1
                    or min(a.get("stopped_s") or 0.0, b.get("stopped_s") or 0.0) < UNJAM_AFTER_S
                    or t - self.unjammed.get(a["callsign"], -1e9) < UNJAM_RETRY_S):
                continue
            self.unjammed[a["callsign"]] = self.unjammed[b["callsign"]] = t
            for x, y in sorted(((a, b), (b, a)), key=lambda p: p[0]["kind"] == "arrival"):
                nodes = self.routes.get(x["callsign"]) or []
                st = self.assigned.get(x["callsign"], {})
                to = st.get("end") if x["kind"] == "departure" else x.get("gate")
                if not nodes or not to or x["callsign"] in busy or x["phase"] not in ("taxi_in", "taxi_out", "hold_short"):
                    continue
                yx = np.array(g.frame.to_xy(y["lon"], y["lat"]))
                room = math.hypot(y["length_m"], y["wingspan_m"]) / 2.0 + 10.0     # the graph under the other aircraft
                near = {n for n, xy in g.node_xy.items() if math.hypot(xy[0] - yx[0], xy[1] - yx[1]) < room}
                under = {eid for eid, e in g.edges.items()        # taxiway edges that pass under the other aircraft
                         if float(np.min(np.hypot(*(np.asarray(e.pts) - yx).T))) < room + x["wingspan_m"] / 2.0 + 15.0}

                def w(u: str, v: str, d: Dict[str, Any]) -> Optional[float]:
                    e = g.edges[d["eid"]]
                    if e.kind == "runway" or u in near or v in near or d["eid"] in under:
                        return None
                    return e.length + (CROSSING_PENALTY_M if e.pavement else 0.0)
                xx = np.array(g.frame.to_xy(x["lon"], x["lat"]))
                for origin in (x.get("next_node"), x.get("prev_node")):
                    if not origin or origin in near or (origin == x.get("next_node") and math.hypot(
                            *(g.node_xy[origin] - yx)) < math.hypot(*(xx - yx))):
                        continue                 # its next node is towards the other aircraft: turn round instead
                    try:
                        route = nx.dijkstra_path(g.g, origin, nodes[-1], weight=w)
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue
                    if self._issue(sim, x, "taxi_via", {"route": route, "to": to},
                                   "New route for %s around %s (each was waiting for the other)" % (x["callsign"], y["callsign"]),
                                   ["%s and %s stopped nose to nose for %.0f s" % (x["callsign"], y["callsign"], x.get("stopped_s") or 0.0)],
                                   [RULES["taxi"]], [{"option": "wait", "reason": "neither can move"}], 20):
                        break
                else:
                    continue
                break

    # -- runway flow (wind) -----------------------------------------------------------------------------------
    @staticmethod
    def _tailwind(sim: Simulation, obs: Dict[str, Any], end: str) -> float:
        return tailwind_kt(obs["weather"], sim.graph.end_info(end)[1]["heading_deg"])

    def _flow(self, sim: Simulation, obs: Dict[str, Any]) -> None:
        """Turn the airport around (every runway to its other end) when the wind gives the active direction a tailwind."""
        cfg = obs["runway_config"]
        arr_pav = {sim.graph.end_runway[e] for e in cfg["arrival_runways"]}
        own = [e for e in cfg["departure_runways"] if sim.graph.end_runway[e] not in arr_pav]
        if own and own != cfg["departure_runways"]:
            # a runway that also lands an arrival every ~100 s never has a gap long enough for a departure (landing
            # occupancy ~70 s): depart only from runways without arrivals, as KPHX does (arrivals 26/25L, departures 25R)
            sim.set_runway_config(cfg["arrival_runways"], own)
            cfg = {"arrival_runways": list(cfg["arrival_runways"]), "departure_runways": own}
            sim.record_decision(callsign="", action="runway_config",
                                summary="Departures from %s only, arrivals %s" % (", ".join(own), ", ".join(cfg["arrival_runways"])),
                                facts=["Arrivals land on %s about every 100 s" % ", ".join(sorted(arr_pav))],
                                rules=[RULES["takeoff"]], alternatives=[{"option": "mixed operations on the arrival runway",
                                                                         "reason": "departures would wait for gaps that never come"}],
                                next_review_s=60)
        ends = cfg["arrival_runways"] + cfg["departure_runways"]
        worst = max(self._tailwind(sim, obs, e) for e in ends)
        if worst <= FLOW_SWITCH_TAILWIND_KT:
            return
        new = {k: [sim.graph.other_end(e) for e in v] for k, v in cfg.items()}
        after = max(self._tailwind(sim, obs, e) for e in new["arrival_runways"] + new["departure_runways"])
        if after >= worst:
            return
        sim.set_runway_config(new["arrival_runways"], new["departure_runways"])
        w = obs["weather"]
        sim.record_decision(
            callsign="", action="runway_config",
            summary="Runway flow change: arrivals %s, departures %s" % (", ".join(new["arrival_runways"]), ", ".join(new["departure_runways"])),
            facts=["Wind %03d at %d kt (%s)" % (w["wind_dir"] or 0, w["wind_kt"] or 0, w["metar"]),
                   "Tailwind %.1f kt on the active runways, %.1f kt after the change" % (worst, after)],
            rules=["Runway selection by headwind; pilots refuse more than %.0f kt tailwind" % TAILWIND_LIMIT_KT],
            alternatives=[{"option": "keep the current flow", "reason": "tailwind %.1f kt and rising" % worst}], next_review_s=60)

    # -- readbacks --------------------------------------------------------------------------------------------
    def _readbacks(self, sim: Simulation, obs: Dict[str, Any]) -> None:
        for rb in obs["pending_readbacks"]:
            want = self.expected.get(rb["clearance_id"])
            heard = rb["parsed"].get("runway")
            if want is None or heard == want:
                sim.resolve_readback(rb["id"], True)
                continue
            sim.resolve_readback(rb["id"], False)
            sim.record_decision(
                callsign=rb["callsign"], action="readback_check",
                summary="Readback did not match: heard runway %s, issued %s. Asked the pilot to say again." % (heard or "none", want),
                facts=["Pilot read back: \"%s\"" % rb["text"], "Clearance %s was for runway %s" % (rb["clearance_id"], want)],
                rules=[RULES["readback"]],
                alternatives=[{"option": "accept the readback", "reason": "wrong or missing runway; the pilot would act on the wrong instruction"}],
                next_review_s=5)

    # -- arrivals --------------------------------------------------------------------------------------------------
    def _arrival(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        cs, st = a["callsign"], self.assigned.setdefault(a["callsign"], {})
        if a["phase"] == "go_around":
            st.pop("landing", None)
            st.pop("end", None)
            st.pop("go_around", None)
            return
        if a["phase"] == "final":
            if "end" in st and st["end"] not in obs["runway_config"]["arrival_runways"] and not a["landing_cleared"] and (
                    (a["eta_s"] or 999.0) > FLOW_FINISH_ETA_S or self._tailwind(sim, obs, st["end"]) > TAILWIND_LIMIT_KT):
                for k in ("end", "landing", "go_around", "exit"):     # flow changed: send it to the new direction
                    st.pop(k, None)
            if "end" not in st:
                self._assign_arrival_runway(sim, obs, a, st)
            elif not a["landing_cleared"] and "landing" not in st:
                self._landing_clearance(sim, obs, a, st)
            return
        if a["phase"] == "taxi_in" and a["speed_mps"] < 0.3 and "ground" not in st:
            st["ground"] = True
            self._issue(sim, a, "contact_tower_or_ground", {"facility": "ground", "frequency": "132.55"},
                        "Handed %s to ground" % cs, ["%s is clear of the runway" % cs], ["JO 7110.65BB 3-10-9 Runway exiting"], [], 20)
        if a["phase"] == "taxi_in" and "ground" in st and "taxi" not in st and a["speed_mps"] < 0.3 and a.get("stopped_s", 0) >= 0.0:
            params = self._taxi_params(sim, a, a["gate"], sim.graph.stands[a["gate"]].node, obs=obs)
            if params is None:
                return                  # only a way head-on into other traffic for now: wait clear of the runway
            st["taxi"] = True
            self._issue(sim, a, "taxi_via", params,
                        "Taxi %s to gate %s by the shortest route" % (cs, a["gate"]),
                        ["%s vacated the runway" % cs, "Gate %s assigned in the schedule" % a["gate"]], [RULES["taxi"]],
                        [{"option": "longer route avoiding crossings", "reason": "shortest route with a crossing penalty already chosen"}], 20)
        if (a["phase"] in ("taxi_in",) and a.get("hold") and a["speed_mps"] < 0.3 and a.get("stopped_s", 0) > 0.8
                and cs not in self.ground_hold):     # a ground hold must be released before a runway clearance (it would stick)
            self._maybe_cross(sim, obs, a)

    def _assign_arrival_runway(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], st: Dict[str, Any]) -> None:
        cfg = obs["runway_config"]["arrival_runways"]
        cur = a.get("arrival_end")
        if cur:     # approach control sequenced it onto this final: keep it (or its other end after a flow change)
            cfg = [e for e in (cur, sim.graph.other_end(cur)) if e in cfg] or cfg
        eta = a["eta_s"] or 120.0
        t = obs["t"]
        stand = sim.graph.stands[a["gate"]]
        ty = get_type(a["type"])
        best: Optional[Tuple[float, str]] = None
        notes: Dict[str, str] = {}
        for end in cfg:
            pav = sim.graph.end_runway[end]
            free_at = self.last_arrival_slot.get(pav, -1e9)
            wait = max(0.0, free_at + ARRIVAL_SLOT_S - (t + eta))
            exit_node = sim.choose_exit(end, ty, stand.node)
            route = sim.graph.route(exit_node, stand.node) if exit_node else None
            crossings = max(len(sim.graph.build_path(route).runs) - 1, 0) if route else 9
            score = wait + 20.0 * crossings
            notes[end] = "%s: slot wait %.0f s, %d crossing(s) to gate" % (end, wait, crossings)
            if best is None or score < best[0]:
                best = (score, end)
        end = best[1] if best else cfg[0]
        pav = sim.graph.end_runway[end]
        self.last_arrival_slot[pav] = max(self.last_arrival_slot.get(pav, -1e9) + ARRIVAL_SLOT_S, t + eta)
        st["end"] = end
        st["exit"] = self._exit(sim, end, ty, stand.node)
        freq = "118.7" if pav == "08/26" else "120.9"
        self._issue(sim, a, "contact_tower_or_ground", {"facility": "tower", "frequency": freq, "runway": end},
                    "Assigned %s to runway %s" % (a["callsign"], end),
                    ["%s is %.0f s from the threshold" % (a["callsign"], eta)] + list(notes.values()),
                    ["First come, first served on each landing runway"],
                    [{"option": "runway %s" % e, "reason": n} for e, n in notes.items() if e != end], 10)

    @staticmethod
    def _exit(sim: Simulation, end: str, ty: Any, dest: str) -> Optional[str]:
        """The runway exit that clears the runway soonest (the next arrival's landing clearance waits for it), with a
        small allowance for the taxi to the gate. Never an exit whose way to the gate dips back onto the same runway
        (the aircraft would stop on the runway, holding short of the runway it is on)."""
        g = sim.graph
        rw, info, _ = g.end_info(end)
        need = 1.25 * (ty.vref_mps ** 2 - EXIT_SPEED_MPS ** 2) * 0.94 / (2 * ty.landing_decel)   # as the simulator's choose_exit
        i = rw.chain.index(info["node"])
        best: Optional[Tuple[float, str]] = None
        for node, dist in g.entries(end):
            if dist < need or dist >= rw.length_m - 200:
                continue
            j = rw.chain.index(node)
            rest = g.route(node, dest)
            if rest is None:
                continue
            path = g.build_path((rw.chain[i:j + 1] if i <= j else rw.chain[j:i + 1][::-1]) + rest[1:])
            if any(run.runway == rw.id and run.s0 > 1.0 for run in path.runs):
                continue
            clear = path.runs[0].s1 + ty.length_m / 2 + 10.0 if path.runs else dist
            occupancy = dist / (0.5 * (0.96 * ty.vref_mps + EXIT_SPEED_MPS)) + path.free_flow_time(dist) - path.free_flow_time(clear)
            score = occupancy + EXIT_TAXI_WEIGHT * path.free_flow_time(clear)
            if best is None or score < best[0]:
                best = (score, node)
        return best[1] if best else sim.choose_exit(end, ty, dest)

    def _landing_clearance(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], st: Dict[str, Any]) -> None:
        eta = a["eta_s"] or 999.0
        if eta > LAND_CLEAR_ETA_S:
            return
        t = obs["t"]
        pav = sim.graph.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t + eta - 10.0, t + eta + 65.0)
        if blockers:
            if eta <= GO_AROUND_ETA_S and "go_around" not in st:
                st["go_around"] = True
                self._issue(sim, a, "go_around", {}, "Go around %s: runway %s not clear" % (a["callsign"], st["end"]),
                            ["%s is %.0f s from touchdown" % (a["callsign"], eta), "Runway %s: %s" % (pav, ", ".join(blockers))],
                            [RULES["land_withhold"]],
                            [{"option": "clear to land anyway", "reason": "would create a runway conflict"}], 60)
            return
        st["landing"] = True
        self._issue(sim, a, "cleared_to_land", {"runway": st["end"], "exit": st.get("exit")},
                    "Cleared %s to land runway %s" % (a["callsign"], st["end"]),
                    ["%s is %.0f s from touchdown" % (a["callsign"], eta), "Runway %s free of traffic and reservations through %.0f s" % (pav, eta + 65)],
                    [RULES["land"]],
                    [{"option": "hold clearance", "reason": "runway is free; waiting would risk a go-around"}], 10)

    def _maybe_cross(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        hold = a["hold"]
        pav = hold["runway"]
        t = obs["t"]
        crossers = tuple(cs for (p, cs), cid in self.claims.items() if p == pav and cid in self.cross_ids)
        blockers = self._blockers(obs, pav, a["callsign"], t, t + CROSS_WINDOW_S, crossers)   # crossers queue at the runway
        due = self._landing_due(obs, pav, CROSS_GUARD_S, a["callsign"])
        if not blockers:
            blockers = self._far_side(sim, obs, a, pav)
        key = "cross-%s" % pav
        if self.claims.get((pav, a["callsign"])) in self.cross_ids:
            return                      # already cleared to cross here (a route may cross the same runway twice)
        if blockers or due:
            if not self.assigned.setdefault(a["callsign"], {}).get("held-" + pav):
                self.assigned[a["callsign"]]["held-" + pav] = True
                why = blockers + (["%s on %.0f s final" % (due["callsign"], due["eta_s"])] if due else [])
                self._speak_hold(sim, a, pav, why)
            return
        self.assigned[a["callsign"]][key] = True
        self.assigned[a["callsign"]].pop("held-" + pav, None)
        label = sim.runway_label(pav)
        self._issue(sim, a, "cross_runway", {"runway": label},
                    "Cleared %s to cross runway %s" % (a["callsign"], label),
                    ["%s holding short of %s for %.0f s" % (a["callsign"], pav, a.get("stopped_s", 0)),
                     "Runway %s free for the next %.0f s and no landing due within %.0f s" % (pav, CROSS_WINDOW_S, CROSS_GUARD_S)],
                    [RULES["cross"]], [{"option": "keep holding", "reason": "runway is clear; holding only adds delay"}], 20)

    def _far_side(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], pav: str) -> List[str]:
        """Aircraft standing where this one would come off the runway after crossing (it would stop on the runway)."""
        g = sim.graph
        nodes = self.routes.get(a["callsign"]) or []
        if a.get("next_node") in nodes:
            nodes = nodes[nodes.index(a["next_node"]):]
        beyond, on, run = [], False, 0.0
        for u, v in zip(nodes, nodes[1:]):
            e = g.edge_between(u, v)
            if e is not None and e[0].pavement == pav:
                on = True
            elif on:
                beyond.append(v)
                run += e[0].length if e is not None else 0.0
                if run > FAR_SIDE_M:
                    break
        out = []
        for o in obs["aircraft"]:
            if o["callsign"] == a["callsign"] or o["phase"] in ("final", "go_around", "airborne", "at_gate", "parked") \
                    or o["speed_mps"] > 0.3:
                continue
            xy = np.array(g.frame.to_xy(o["lon"], o["lat"]))
            room = math.hypot(o["length_m"], o["wingspan_m"]) / 2.0 + a["wingspan_m"] / 2.0
            if any(math.hypot(*(g.node_xy[n] - xy)) < room for n in beyond):
                out.append("%s standing beyond the runway" % o["callsign"])
        return out

    def _speak_hold(self, sim: Simulation, a: Dict[str, Any], pav: str, why: List[str]) -> None:
        sim.record_decision(
            callsign=a["callsign"], action="hold_short",
            summary="%s keeps holding short of runway %s" % (a["callsign"], sim.runway_label(pav)),
            facts=["Runway %s: %s" % (pav, ", ".join(why))], rules=[RULES["cross"]],
            alternatives=[{"option": "cross now", "reason": "would conflict with: %s" % ", ".join(why)}], next_review_s=3)

    # -- departures -----------------------------------------------------------------------------------------------------
    def _departure(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        cs, st = a["callsign"], self.assigned.setdefault(a["callsign"], {})
        ph = a["phase"]
        if st.get("end") and st["end"] not in obs["runway_config"]["departure_runways"] and "luaw" not in st:
            self._reroute_departure(sim, a, st)
        if ph == "at_gate" and a["ready"]:
            if "end" not in st:
                self._assign_departure(sim, obs, a, st)
            if "push" not in st and self._lane_clear(sim, obs, a):
                st["push"] = True
                self._issue(sim, a, "pushback_approved", {}, "Pushback approved for %s from gate %s" % (cs, a["gate"]),
                            ["%s ready at gate %s" % (cs, a["gate"]), "Pushback lane clear within %.0f m" % PUSHBACK_CLEAR_M],
                            ["JO 7110.65BB 3-7-1 Ground traffic movement"], [], 15)
            return
        if ph == "taxi_out" and "push" in st and "taxi" not in st and a["speed_mps"] < 0.3:
            if "end" not in st:                       # pushed back under another controller: no runway chosen yet
                self._assign_departure(sim, obs, a, st)
                if "end" not in st:
                    return
            params = self._taxi_params(sim, a, st["end"], st["entry"], obs=obs)
            if params is None:
                return                  # only a way head-on into other traffic for now: wait on the push lane
            st["taxi"] = True
            self._issue(sim, a, "taxi_via", params,
                        "Taxi %s to runway %s via %s" % (cs, st["end"], ", ".join(st["via"]) or "shortest route"),
                        ["%s pushed back from gate %s" % (cs, a["gate"]), "Runway %s chosen: %s" % (st["end"], st["why"])],
                        [RULES["taxi"]], st["alts"], 20)
            return
        if ph == "hold_short" and a.get("hold") and "taxi" in st and cs not in self.ground_hold:
            pav = a["hold"]["runway"]
            if pav != sim.graph.end_runway[st["end"]] or not self._at_entry(sim, a):   # a crossing of its own runway
                self._maybe_cross(sim, obs, a)
            else:
                self._line_up(sim, obs, a, st)
            return
        if ph == "line_up" and "luaw" in st and not a["takeoff_cleared"] and "to" not in st:
            self._takeoff(sim, obs, a, st)

    def _reroute_departure(self, sim: Simulation, a: Dict[str, Any], st: Dict[str, Any]) -> None:
        """Flow changed: a departure still taxiing goes to the other end of its runway. A stopped one (in a queue or at
        the old end's hold point) turns round where it stands; a moving one near the runway first comes to a stop."""
        if "taxi" not in st:
            for k in ("end", "entry"):
                st.pop(k, None)             # not taxiing yet: planned again from the new configuration
            return
        pav = sim.graph.end_runway[st["end"]]
        if a["phase"] not in ("taxi_out", "hold_short"):
            return
        start = a.get("prev_node") if a["speed_mps"] < 0.2 else None
        if start is None and (a.get("hold") or a.get("next_node") in sim.graph.runways[pav].chain):
            return
        start = start or a.get("next_node")
        new = sim.graph.other_end(st["end"])
        entry = sim.choose_entry(new, start, get_type(a["type"])) if start else None
        res = self._issue(sim, a, "taxi_via", self._taxi_params(sim, a, new, entry, start),
                          "Runway flow change: taxi %s to runway %s instead of %s" % (a["callsign"], new, st["end"]),
                          ["Departure runways are now %s" % new], [RULES["taxi"]],
                          [{"option": "keep runway %s" % st["end"], "reason": "tailwind on that direction"}], 20)
        if res:
            st["end"] = new
            st.pop("entry", None)

    def _assign_departure(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], st: Dict[str, Any]) -> None:
        stand = sim.graph.stands[a["gate"]]
        ty = get_type(a["type"])
        cfg = obs["runway_config"]["departure_runways"]
        queued = {e: sum(1 for s in self.assigned.values() if s.get("end") == e and not s.get("airborne")) for e in cfg}
        best: Optional[Tuple[float, str, str]] = None
        notes: Dict[str, str] = {}
        for end in cfg:
            entry = sim.choose_entry(end, stand.lane_node, ty)
            if entry is None:
                continue
            route = sim.graph.route(stand.lane_node, entry)
            if route is None:
                continue
            path = sim.graph.build_path(route)
            crossings = len(path.runs) - 1
            shared = sim.graph.end_runway[end] in {sim.graph.end_runway[e] for e in obs["runway_config"]["arrival_runways"]}
            score = path.length / 5.0 + 40.0 * crossings + 90.0 * queued[end] + (60.0 if shared else 0.0)
            notes[end] = "%s: %.0f m taxi, %d crossing(s), %d ahead in queue%s" % (
                end, path.length, max(crossings, 0), queued[end], ", shared with arrivals" if shared else "")
            if best is None or score < best[0]:
                best = (score, end, entry)
        if best is None:
            return
        _, end, entry = best
        st.update(end=end, entry=entry, via=sim.graph.taxiways_on(sim.graph.route(stand.lane_node, entry) or []),
                  why=notes[end], alts=[{"option": "runway %s" % e, "reason": n} for e, n in notes.items() if e != end])
        sim.record_decision(callsign=a["callsign"], action="assign_runway",
                            summary="Assigned %s to depart runway %s" % (a["callsign"], end),
                            facts=["%s ready at gate %s" % (a["callsign"], a["gate"])] + list(notes.values()),
                            rules=["First come, first served; shortest taxi route with a penalty per runway crossing and per aircraft queued"],
                            alternatives=st["alts"], next_review_s=20)

    @staticmethod
    def _at_entry(sim: Simulation, a: Dict[str, Any]) -> bool:
        """Holding at its departure entry (not at a crossing of the same runway earlier on the route)."""
        if not a.get("entry_node"):
            return True
        x, y = sim.graph.frame.to_xy(a["lon"], a["lat"])
        ex, ey = sim.graph.node_xy[a["entry_node"]]
        return math.hypot(x - ex, y - ey) < 150.0

    def _lane_clear(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> bool:
        """No aircraft near the push lane, and no cleared taxi route still to pass it (it would meet the pushed aircraft
        nose to nose in the lane)."""
        g = sim.graph
        lane = g.node_xy[g.stands[a["gate"]].lane_node]
        waited = obs["t"] - self.ready_at.setdefault(a["callsign"], obs["t"])
        for cs, nodes in self.routes.items():
            if cs == a["callsign"] or waited > PUSH_DELAY_MAX_S:
                continue
            # anywhere on the rest of that route: a pushback takes ~5 min, long enough for far traffic to arrive
            if any(math.hypot(*(g.node_xy[n] - lane)) < PUSHBACK_CLEAR_M for n in nodes[1:]):
                return False
        for o in obs["aircraft"]:
            if o["callsign"] == a["callsign"] or o["phase"] in ("at_gate", "parked", "final", "airborne", "go_around"):
                continue
            x, y = sim.graph.frame.to_xy(o["lon"], o["lat"])
            if math.hypot(x - lane[0], y - lane[1]) < PUSHBACK_CLEAR_M:
                return False
        return True

    def _line_up(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], st: Dict[str, Any]) -> None:
        if "luaw" in st:
            return
        t = obs["t"]
        pav = sim.graph.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t, t + 40.0)
        due = self._landing_due(obs, pav, LINE_UP_GUARD_S, a["callsign"])
        if blockers or due:       # behind a departure that is airborne: line up now, takeoff waits for the spacing
            return
        st["luaw"] = True
        self._issue(sim, a, "line_up_and_wait", {"runway": st["end"]},
                    "%s line up and wait runway %s" % (a["callsign"], st["end"]),
                    ["Runway %s clear of traffic and reservations" % pav, "No landing due within %.0f s" % LINE_UP_GUARD_S],
                    [RULES["luaw"]], [{"option": "hold short", "reason": "runway is available now"}], 10)

    def _spacing_ok(self, sim: Simulation, st: Dict[str, Any], a: Dict[str, Any], t: float) -> bool:
        pav = sim.graph.end_runway[st["end"]]
        last = self.last_airborne.get(pav)
        if last is None:
            return True
        return t - last[0] >= departure_gap_s(last[1], get_type(a["type"]).wtc)

    def _takeoff(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any], st: Dict[str, Any]) -> None:
        t = obs["t"]
        pav = sim.graph.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t, t + 30.0)
        due = self._landing_due(obs, pav, TAKEOFF_GUARD_S, a["callsign"])
        if blockers or due or not self._spacing_ok(sim, st, a, t):
            return
        st["to"] = True
        res = self._issue(sim, a, "cleared_for_takeoff", {"runway": st["end"]},
                          "Cleared %s for takeoff runway %s" % (a["callsign"], st["end"]),
                          ["%s lined up on runway %s" % (a["callsign"], pav), "No traffic on or reserved on the runway; no landing within %.0f s" % TAKEOFF_GUARD_S,
                           "Wake/time spacing from the previous departure satisfied"],
                          [RULES["takeoff"], RULES["same_rwy_dep"]], [{"option": "keep waiting", "reason": "all conditions met"}], 15)
        if res:
            st["airborne"] = False
