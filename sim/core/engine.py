"""The simulation: owns the clock, the true state and the tool surface used by controllers.

The airport runs itself with its built-in rule-based tower (sim/tower). Any other controller is an object with
`name`, `status` and `on_tick(sim, events)` passed to `set_controller`; the simulator never imports one.

Tools (issue_clearance, speak, set_runway_config, record_decision, observe) only validate the SHAPE of a call:
known callsign, known runway, route nodes that exist in the graph. They never refuse a clearance as unsafe.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from sim.core.decisions import make_decision, snapshot_summary
from sim.core import views
from sim.core.aircraft_types import AircraftType, get_type
from sim.core.clock import SimClock, SPEEDS
from sim.core.entities import (GROUND_PHASES, Aircraft, Clearance, Readback, Vehicle, VoiceLine)
from sim.core.eventlog import EventLog, brief
from sim.core.physics import (CROSS_EST_S, EXIT_SPEED, FINAL_START_M, REENTRY_M, GO_AROUND_A_M, GO_AROUND_CLIMB_S, HOLD_OFFSET_M,
                              A_BRAKE, Physics, unit)
from sim.core.reservations import Reservations
from sim.core.scenario import Flight, Scenario
from sim.graph.model import AirportGraph, Piece, Track
from sim.metrics.score import compute_score
from sim.voice import phraseology
from sim.voice.pilots import make_readback
from sim.weather.replay import WeatherReplay

ACTIONS = ("taxi_via", "hold_short", "cross_runway", "line_up_and_wait", "cleared_for_takeoff", "cleared_to_land",
           "go_around", "hold_position", "resume_taxi", "pushback_approved", "contact_tower_or_ground")
RUNWAY_PARAM_ACTIONS = ("hold_short", "cross_runway", "line_up_and_wait", "cleared_for_takeoff", "cleared_to_land")
REVERSING = ("hold_short", "hold_position", "go_around")
LINE_UP_M = 30.0           # distance past the runway centerline node where an aircraft lines up
AIR_SEP_SAME_RWY_M = 4630.0   # 2.5 nm on the same final
AIR_SEP_OTHER_M = 1500.0
GROUND_SEP_MARGIN_M = 5.0   # wingtip clearance below which a ground pair counts as a separation loss
DEADLOCK_S = 60.0
DEFAULT_CONFIG = {"arrival_runways": ["25L", "26"], "departure_runways": ["25R", "26"]}
Resolved = Dict[str, Any]


def _err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": msg}


class Simulation:
    def __init__(self, scenario_path: Any, graph_path: Any = None, seed: Optional[int] = None, controller: Any = "tower",
                 run_dir: Any = None, graph: Optional[AirportGraph] = None) -> None:
        self.scenario_path = Path(scenario_path)
        self.graph = graph or AirportGraph.load(Path(graph_path) if graph_path else None)
        self._seed_override = seed
        self._run_dir = Path(run_dir) if run_dir else None
        self._resets = 0
        self.controller_name = ""
        self.controller: Any = None
        self.set_controller(controller)
        self._init_state()

    # -- lifecycle -----------------------------------------------------------------------------
    def _init_state(self) -> None:
        sc = Scenario.load(self.scenario_path)
        self.scenario = sc
        self.seed = self._seed_override if self._seed_override is not None else sc.seed
        self.rng = np.random.default_rng(self.seed)
        self.clock = SimClock()
        run_dir = self._run_dir
        if run_dir is not None and self._resets:
            run_dir = run_dir.parent / ("%s-r%d" % (run_dir.name, self._resets))
        run_id = run_dir.name if run_dir else "%s-seed%d" % (sc.name, self.seed)
        self.log = EventLog(run_id, run_dir)
        self.weather = WeatherReplay(**sc.weather)
        self.res = Reservations(list(self.graph.runways))
        self.physics = Physics(self)
        self.obs = views.ObservationBuffer()
        self.aircraft: Dict[str, Aircraft] = {}
        self.clearances: Dict[str, Clearance] = {}
        self.active: Dict[str, List[Clearance]] = {}
        self.voice: List[VoiceLine] = []
        self.decisions: Dict[str, Dict[str, Any]] = {}
        self.readbacks: Dict[str, Readback] = {}
        self.runway_cfg = {k: list(v) for k, v in DEFAULT_CONFIG.items()}
        self.pending: List[Tuple[float, int, Flight]] = sorted(
            [((f.scheduled_sim_s - FINAL_START_M / (get_type(f.type).vref_mps + 10.0) if f.kind == "arrival" else sc.start_sim_s), i, f)
             for i, f in enumerate(sc.flights)],
            key=lambda x: (max(x[0], sc.start_sim_s), x[1]))
        self._seq = 0
        self._phase_prev: Dict[str, str] = {}
        self._ctl_cursor = 0
        self._open_pairs: Set[Tuple[str, ...]] = set()
        self._wait_since: Dict[Tuple[str, ...], float] = {}
        self._deadlocks: Set[Tuple[str, ...]] = set()
        self._counters = {"c": 0, "v": 0, "d": 0, "r": 0}
        self._score_cache: Tuple[int, Dict[str, Any]] = (-1, {})
        self.finished = False
        self._reset_sent()
        self.log.emit("scenario.start", None, 0, {"scenario": sc.name, "seed": self.seed, "flights": len(sc.flights),
                                                  "duration_s": sc.duration_s})
        if sc.haboob_trigger_s is not None:
            self.log.emit("weather.scheduled", None, 0, {"trigger_s": sc.haboob_trigger_s})
        for v in sc.vehicles:
            self._spawn_vehicle(v)

    def set_controller(self, controller: Any) -> Dict[str, Any]:
        """Swap the controller: "tower" (the built-in rule-based tower) or a controller object.

        Must run on the simulation thread. The outgoing controller is shut down if it has a `shutdown` method; a tower
        built mid-run is primed with the current clearances so it does not repeat them.
        """
        old = getattr(self, "controller", None)
        if isinstance(controller, str):
            if controller != "tower":
                return _err("unknown controller %r" % controller)
            from sim.tower.controller import TowerController
            new: Any = TowerController()
            if getattr(self, "aircraft", None):
                from sim.tower.handover import prime_tower
                prime_tower(new, self)
        else:
            new = controller
        if old is not None and old is not new and hasattr(old, "shutdown"):
            old.shutdown()
        self.controller = new
        self.controller_name = new.name
        return {"ok": True}

    def close(self) -> None:
        self.log.close()

    @property
    def t(self) -> float:
        return self.clock.t

    def emit(self, kind: str, entity: Optional[str], data: Dict[str, Any]) -> Dict[str, Any]:
        return self.log.emit(kind, entity, self.clock.t_ms, data)

    def _next_id(self, prefix: str) -> str:
        key = {"c": "c", "v": "v", "d": "d", "r": "rb"}[prefix]
        self._counters[prefix] += 1
        return "%s-%d" % (key, self._counters[prefix])

    # -- stepping -------------------------------------------------------------------------------
    def step(self) -> List[Dict[str, Any]]:
        start = len(self.log.events)
        self.clock.advance()
        t = self.t
        self._weather_tick(t)
        self._spawn_tick(t)
        self._readback_tick(t)
        self._call_controller()
        self._physics_tick(t)
        self._update_reservations(t)
        self._detect(t)
        self.obs.record(self)
        self._phase_events()
        if not self.finished and not self.pending and all(a.done or a.kind == "vehicle" for a in self.aircraft.values()):
            self.finished = True
            self.emit("scenario.complete", None, {"t": t})
        return self.log.events[start:]

    def run_to_completion(self, max_s: Optional[float] = None) -> None:
        limit = max_s if max_s is not None else self.scenario.duration_s * 4 + 600
        while not self.finished and self.t < limit:
            self.step()
        self.log.flush()

    def _call_controller(self) -> None:
        events = self.log.events[self._ctl_cursor:]
        self._ctl_cursor = len(self.log.events)
        self.controller.on_tick(self, events)

    # -- weather ---------------------------------------------------------------------------------
    def _weather_tick(self, t: float) -> None:
        trig = self.scenario.haboob_trigger_s
        if trig is not None and self.weather.trigger_s is None and t >= trig:
            self.trigger_haboob()
        for row in self.weather.step(t):
            self.emit("weather.update", "KPHX", {k: row.get(k) for k in ("metar", "wind_dir", "wind_kt", "gust_kt", "vis_sm", "wx")}
                      | {"low_vis": self.weather.low_vis, "haboob_progress": self.weather.current.get("haboob_progress")})

    def trigger_haboob(self) -> Dict[str, Any]:
        if not self.weather.trigger(self.t):
            return _err("haboob already triggered")
        self.emit("weather.haboob_triggered", "KPHX", {"trigger_s": self.t})
        return {"ok": True}

    def weather_view(self) -> Dict[str, Any]:
        w = dict(self.weather.current)
        w.pop("_min", None)
        w["t"] = round(w.get("t", 0.0), 1)
        return w

    # -- spawning ----------------------------------------------------------------------------------
    def _spawn_tick(self, t: float) -> None:
        while self.pending and max(self.pending[0][0], self.scenario.start_sim_s) <= t:
            _, _, f = self.pending.pop(0)
            self._spawn_flight(f, t)
        for ac in self.aircraft.values():
            if ac.kind == "departure" and not ac.ready and ac.phase == "at_gate" and t >= ac.scheduled_s:
                ac.ready = True
                self.emit("departure.ready", ac.callsign, {"gate": ac.gate, "scheduled_s": ac.scheduled_s})

    def _spawn_flight(self, f: Flight, t: float) -> None:
        self._seq += 1
        ty = get_type(f.type)
        ac = Aircraft(callsign=f.callsign, type=ty, kind=f.kind, airline=f.airline, seq=self._seq, gate=f.gate,
                      scheduled_s=f.scheduled_sim_s, spawn_t=t)
        if f.kind == "departure":
            st = self.graph.stands[f.gate]
            ac.x, ac.y = float(st.xy[0]), float(st.xy[1])
            ac.heading, ac.phase = st.heading_in, "at_gate"
        else:
            end = f.runway_preference or self.runway_cfg["arrival_runways"][0]
            rw, info, _ = self.graph.end_info(end)
            u = unit(info["heading_deg"])
            a0 = min(FINAL_START_M, max(1500.0, (f.scheduled_sim_s - t) * (ty.vref_mps + 10.0)))
            thr = self.graph.node_xy[info["node"]]
            pos = thr - u * a0
            ac.x, ac.y, ac.heading, ac.runway, ac.phase = float(pos[0]), float(pos[1]), info["heading_deg"], end, "final"
            ac.speed = ty.vref_mps + min(max((a0 - 1500.0) / 9000.0, 0.0), 1.0) * 20.0
            ac.alt_m = a0 * math.tan(math.radians(3.0))
        self.aircraft[ac.callsign] = ac
        self.emit("aircraft.spawned", ac.callsign, {"type": ty.icao, "kind": f.kind, "airline": f.airline, "gate": f.gate,
                                                    "scheduled_s": f.scheduled_sim_s, "runway": ac.runway})

    def _spawn_vehicle(self, v: Any) -> None:
        path = self.graph.build_path(v.route)
        ac = Vehicle(callsign=v.id, type=get_type("C172"), kind="vehicle", airline="GSE", seq=10_000, phase="vehicle_moving",
                     patrol=list(v.route), patrol_speed=v.speed_mps)
        ac.type = _vehicle_type()
        ac.path, ac.s = path, 0.0
        ac.x, ac.y = float(path.pts[0][0]), float(path.pts[0][1])
        ac.heading = path.heading_at(0.0)
        self.aircraft[v.id] = ac
        self.emit("aircraft.spawned", v.id, {"kind": "vehicle", "route_nodes": len(v.route)})

    # -- pilots and readbacks ----------------------------------------------------------------------
    def _readback_tick(self, t: float) -> None:
        for rb in list(self.readbacks.values()):
            if rb.delivered or rb.due_t > t:
                continue
            c = self.clearances[rb.clearance_id]
            rb.delivered = True
            if c.state not in ("issued", "acknowledged"):
                rb.resolved = True
                continue
            text, parsed, error = make_readback(self.rng, self.scenario.readback_error_rate, c.action, c.callsign,
                                                c.params, c.runway, rb.retry)
            voice = self._add_voice("pilot", c.callsign, text, "ok" if error is None else "pending")
            rb.voice_id, rb.parsed, rb.error = voice.id, parsed, error
            self.emit("readback", c.callsign, {"id": rb.id, "clearance_id": c.id, "voice_id": voice.id, "text": text,
                                               "parsed": parsed, "status": voice.status, "error_truth": error})
            if error is None:
                rb.resolved = True
                self._acknowledge(c)

    def _acknowledge(self, c: Clearance) -> None:
        if c.state == "issued":
            c.state, c.ack_t = "acknowledged", self.t
            self.emit("clearance.acknowledged", c.callsign, {"clearance_id": c.id, "action": c.action})
        self._apply(c)

    def resolve_readback(self, readback_id: str, accept: bool) -> Dict[str, Any]:
        """The controller's verdict on a pilot readback: accept it or say again (the pilot then reads back correctly)."""
        rb = self.readbacks.get(readback_id)
        if rb is None or not rb.delivered:
            return _err("unknown or undelivered readback %r" % readback_id)
        if rb.resolved:
            return {"ok": True, "note": "already resolved"}
        rb.resolved = True
        c = self.clearances[rb.clearance_id]
        voice = next(v for v in self.voice if v.id == rb.voice_id)
        if accept:
            voice.status = "ok"
            self.emit("readback.accepted", c.callsign, {"readback_id": rb.id, "clearance_id": c.id, "error_truth": rb.error})
            if rb.error:
                self.emit("readback.error_accepted", c.callsign, {"readback_id": rb.id, "error": rb.error})
                ac = self.aircraft[c.callsign]
                if c.action == "hold_short":
                    ac.ignore_hold = True
                    return {"ok": True}
            self._acknowledge(c)
            return {"ok": True}
        voice.status = "corrected"
        c.version += 1
        say = self._add_voice("agent", c.callsign, phraseology.correction_text(c.callsign, c.text), "ok")
        self.emit("readback.rejected", c.callsign, {"readback_id": rb.id, "clearance_id": c.id, "voice_id": say.id})
        nxt = Readback(self._next_id("r"), c.callsign, c.id, "", {}, None, self.t + float(self.rng.uniform(1.5, 3.0)), retry=True)
        self.readbacks[nxt.id] = nxt
        return {"ok": True, "voice_id": say.id}

    def _add_voice(self, speaker: str, callsign: str, text: str, status: str = "ok") -> VoiceLine:
        v = VoiceLine(self._next_id("v"), round(self.t, 1), speaker, callsign, text, status)
        self.voice.append(v)
        self.emit("speech", callsign, {"voice_id": v.id, "speaker": speaker, "text": text, "status": status})
        return v

    def speak(self, callsign: str, text: str, speaker: str = "agent") -> Dict[str, Any]:
        if callsign not in self.aircraft:
            return _err("unknown callsign %r" % callsign)
        return {"ok": True, "voice_id": self._add_voice(speaker, callsign, text).id}

    # -- tools: clearances ---------------------------------------------------------------------------
    def runway_pavement(self, name: Optional[str]) -> Optional[str]:
        if name in self.graph.end_runway:
            return self.graph.end_runway[name]
        return name if name in self.graph.runways else None

    def runway_label(self, pavement: str) -> str:
        """The runway end name controllers use for this pavement in the current flow."""
        rw = self.graph.runways[pavement]
        used = self.runway_cfg["arrival_runways"] + self.runway_cfg["departure_runways"]
        for end in rw.ends:
            if end in used:
                return end
        west = sorted(rw.ends, key=lambda e: -rw.ends[e]["heading_deg"])[0]
        return west

    def _norm_runway(self, val: Any) -> Optional[str]:
        if not isinstance(val, str):
            return None
        if val in self.graph.end_runway:
            return val
        return self.runway_label(val) if val in self.graph.runways else None

    def issue_clearance(self, callsign: str, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ac = self.aircraft.get(callsign)
        if ac is None or ac.removed or ac.kind == "vehicle":
            return _err("unknown callsign %r" % callsign)
        if action not in ACTIONS:
            return _err("unknown action %r; expected one of %s" % (action, ", ".join(ACTIONS)))
        p = dict(params or {})
        if "runway" in p or action in RUNWAY_PARAM_ACTIONS:
            default = ac.dep_end if action in ("line_up_and_wait", "cleared_for_takeoff") else ac.runway
            raw = p.get("runway", default)
            rwy = self._norm_runway(raw)
            if rwy is None:
                return _err("unknown or missing runway %r" % (raw,))
            p["runway"] = rwy
        resolved: Optional[Resolved] = None
        if action == "taxi_via":
            res = self._resolve_taxi(ac, p)
            if isinstance(res, str):
                return _err(res)
            resolved = res
            p.update(self._taxi_text_params(ac, res))
        wind = self.weather_view()
        text = phraseology.clearance_text(action, callsign, p, wind)
        runway = p.get("runway") or p.get("departure_runway") or (p.get("hold_short") or [None])[0]
        c = Clearance(self._next_id("c"), callsign, action, text, p, runway, round(self.t, 1))
        if resolved is not None:
            c.params["_resolved"] = resolved
        self._supersede(callsign, c)
        self.clearances[c.id] = c
        self.active.setdefault(callsign, []).append(c)
        voice = self._add_voice("agent", callsign, text)
        c.voice_id = voice.id
        public = {k: v for k, v in c.params.items() if k != "_resolved"}
        self.emit("clearance.issued", callsign, {"clearance_id": c.id, "action": action, "params": public, "text": text,
                                                 "voice_id": voice.id})
        rb = Readback(self._next_id("r"), callsign, c.id, "", {}, None, self.t + float(self.rng.uniform(1.5, 3.0)))
        self.readbacks[rb.id] = rb
        return {"ok": True, "clearance_id": c.id, "voice_id": voice.id, "text": text}

    def _supersede(self, callsign: str, new: Clearance) -> None:
        movement = ("taxi_via", "hold_short", "hold_position", "resume_taxi", "cross_runway")
        for old in list(self.active.get(callsign, [])):
            if old.state in ("complete", "amended", "cancelled"):
                continue
            same_family = old.action == new.action or (old.action in movement and new.action in movement
                                                       and new.action != "cross_runway")
            if old.action == "line_up_and_wait" and new.action == "cleared_for_takeoff":
                same_family = False
            if same_family:
                old.state = "amended"
                self.active[callsign].remove(old)
                reversal = new.action in REVERSING and old.action not in REVERSING
                self.emit("clearance.amended", callsign, {"clearance_id": old.id, "by": new.id, "reversal": reversal})

    def _complete(self, c: Clearance) -> None:
        if c.state in ("complete", "amended", "cancelled"):
            return
        c.state, c.done_t = "complete", round(self.t, 1)
        if c in self.active.get(c.callsign, []):
            self.active[c.callsign].remove(c)
        self.emit("clearance.complete", c.callsign, {"clearance_id": c.id, "action": c.action})

    # -- taxi route resolution -----------------------------------------------------------------------
    def _route_origin(self, ac: Aircraft) -> Tuple[Optional[str], Optional[Piece]]:
        p = ac.path
        if p is None:
            if ac.phase == "at_gate" and ac.gate:
                return self.graph.stands[ac.gate].node, None
            return None, None
        seg = p.seg_at(ac.s)
        if seg is None or seg.to_node is None:
            return None, None
        if abs(ac.s - seg.s1) < 0.5:
            return seg.to_node, None
        if seg.edge is None:
            return None, None
        e = self.graph.edges[seg.edge]
        idx = np.nonzero((p.cum > ac.s) & (p.cum < seg.s1))[0]
        pts = np.vstack([[ac.x, ac.y], p.pts[idx], p.xy_at(seg.s1)])
        return seg.to_node, (seg.edge, pts, e.kind, e.pavement, e.ref)

    def choose_entry(self, end: str, origin: str, ty: AircraftType) -> Optional[str]:
        """Runway entry node with enough runway left for this type, nearest by taxi distance."""
        rw = self.graph.runways[self.graph.end_runway[end]]
        need = 1.35 * ty.vlof_mps ** 2 / (2 * ty.takeoff_accel) + 250.0
        best: Optional[Tuple[float, str]] = None
        for node, dist in self.graph.entries(end):
            if rw.length_m - dist < need:
                continue
            r = self.graph.route(origin, node)
            if r is None:
                continue
            length = self.graph.route_length(r)
            if best is None or length < best[0]:
                best = (length, node)
        return best[1] if best else None

    def choose_exit(self, end: str, ty: AircraftType, dest: Optional[str]) -> Optional[str]:
        """A runway exit far enough down the runway to stop by the exit speed, weighing the taxi distance to the gate."""
        rw, info, _ = self.graph.end_info(end)
        need = 1.25 * (ty.vref_mps ** 2 - EXIT_SPEED ** 2) * 0.94 / (2 * ty.landing_decel)
        cands = [(n, d) for n, d in self.graph.entries(end) if d >= need and d < rw.length_m - 200]
        if not cands:
            cands = [(n, d) for n, d in self.graph.entries(end) if d < rw.length_m - 200][-1:]
        best: Optional[Tuple[float, str]] = None
        for node, dist in cands:
            r = self.graph.route(node, dest) if dest else [node]
            if r is None:
                continue
            score = dist * 0.6 + self.graph.route_length(r) * 0.4
            if best is None or score < best[0]:
                best = (score, node)
        return best[1] if best else None

    def _resolve_taxi(self, ac: Aircraft, p: Dict[str, Any]) -> Any:
        origin, _ = self._route_origin(ac)
        if origin is None:
            return "aircraft %s has no ground position to taxi from" % ac.callsign
        via = p.get("via")
        if via:
            unknown = [v for v in via if not any(r.upper().startswith(v.upper()) for r in self.graph.taxiway_refs)]
            if unknown:
                return "unknown taxiway(s) %s" % unknown
        dep_end: Optional[str] = None
        entry: Optional[str] = None
        if p.get("route"):
            nodes = [str(n) for n in p["route"]]
            bad = [n for n in nodes if n not in self.graph.node_xy]
            if bad:
                return "route contains unknown graph nodes %s" % bad[:3]
            if any(self.graph.edge_between(a, b) is None for a, b in zip(nodes, nodes[1:])):
                return "route nodes are not connected by graph edges"
            dst = nodes[-1]
        else:
            to = p.get("to")
            if to in self.graph.stands:
                dst = self.graph.stands[to].node
            elif to in self.graph.end_runway:
                dep_end = to
                entry = p.get("entry") or self.choose_entry(to, origin, ac.type)
                if entry is None or entry not in self.graph.runways[self.graph.end_runway[to]].chain:
                    return "no usable runway entry for %s" % to
                dst = entry
            elif isinstance(to, str) and to in self.graph.node_xy:
                dst = to
            else:
                return "taxi_via needs 'to' (stand name, runway end or node id) or 'route'; got %r" % (to,)
        route = self.graph.route(origin, dst, via=via)
        if route is None:
            return "no taxi route from %s to %s" % (origin, dst)
        return {"dst": dst, "via": via, "dep_end": dep_end, "entry": entry, "gate": p.get("to") if p.get("to") in self.graph.stands else None}

    def _taxi_text_params(self, ac: Aircraft, res: Resolved) -> Dict[str, Any]:
        origin, _ = self._route_origin(ac)
        nodes = self.graph.route(origin, res["dst"], via=res["via"]) or []
        tail = self._lineup_tail(res["dep_end"], res["entry"]) if res["dep_end"] else ()
        path = self.graph.build_path(nodes, tail=tail)
        runs = path.runs
        crossings = runs[:-1] if res["dep_end"] and runs else runs
        out: Dict[str, Any] = {"taxiways": self.graph.taxiways_on(nodes), "hold_short": [], "cross": []}
        if res["dep_end"]:
            out["departure_runway"] = res["dep_end"]
        if res["gate"]:
            out["gate"] = res["gate"]
        if crossings:
            out["hold_short"] = [self.runway_label(crossings[0].runway)]
        return out

    def _lineup_tail(self, end: str, entry: str) -> Tuple[Piece, ...]:
        rw, info, _ = self.graph.end_info(end)
        a = self.graph.node_xy[entry]
        b = a + unit(info["heading_deg"]) * LINE_UP_M
        return ((None, np.array([a, b]), "taxiway", rw.id, None),)

    # -- clearance effects -----------------------------------------------------------------------------
    def _apply(self, c: Clearance) -> None:
        ac = self.aircraft.get(c.callsign)
        if ac is None or ac.removed or c.state not in ("acknowledged",):
            return
        t = self.t
        act, p = c.action, c.params
        applied = True
        if act == "pushback_approved":
            applied = self._start_pushback(ac, c)
        elif act == "taxi_via":
            applied = self._apply_taxi(ac, c)
        elif act == "hold_short":
            applied = self._hold_short(ac, p["runway"])
        elif act == "cross_runway":
            applied = self._cross(ac, c)
        elif act == "line_up_and_wait":
            applied = self._clear_final_run(ac, c)
            if applied:
                self.res.set(self.runway_pavement(ac.dep_end) or "", ac.callsign, "line_up", t, t + 60.0)
        elif act == "cleared_for_takeoff":
            ac.takeoff_cleared = True
            applied = self._clear_final_run(ac, c) if ac.path and ac.phase != "takeoff_roll" else True
            self.res.set(self.runway_pavement(ac.dep_end) or "", ac.callsign, "takeoff", t, t + 70.0)
        elif act == "cleared_to_land":
            if ac.phase == "final" or ac.phase == "go_around":
                ac.landing_cleared = True
                ac.runway = p["runway"]
                ac.exit_node = p.get("exit") if p.get("exit") in self.graph.node_xy else None
            else:
                applied = False
        elif act == "go_around":
            applied = ac.phase == "final" and self._go_around(ac, "controller instruction")
        elif act == "hold_position":
            if ac.path is not None:
                ac.hold_s = ac.s + ac.speed ** 2 / (2 * A_BRAKE)
            else:
                applied = False
        elif act == "resume_taxi":
            ac.hold_s = None
            self._complete(c)
        elif act == "contact_tower_or_ground":
            ac.frequency = p.get("facility", "ground")
            if p.get("runway") and ac.phase == "final" and not ac.landing_cleared:
                ac.runway = p["runway"]
            self._complete(c)
        if not applied:
            self.emit("clearance.no_effect", ac.callsign, {"clearance_id": c.id, "action": act, "phase": ac.phase})

    def _start_pushback(self, ac: Aircraft, c: Clearance) -> bool:
        if ac.phase != "at_gate" or ac.kind != "departure":
            return False
        st = self.graph.stands[ac.gate or ""]
        path = self.graph.build_path([st.node, st.lane_node])
        mean = self.scenario.pushback_mean_s
        dur = max(30.0, float(self.rng.normal(mean, 0.1 * mean)))
        from sim.core.physics import PUSH_V
        ac.path, ac.s, ac.speed = path, 0.0, 0.0
        ac.pushback_wait_s = max(0.0, dur - path.length / PUSH_V)
        ac.pushback_started_t = self.t
        ac.dep_pushback_s = dur
        ac.unimpeded_s = dur
        ac.phase = "pushback"
        self.emit("pushback.start", ac.callsign, {"duration_s": round(dur, 1)})
        return True

    def _pushback_done(self, ac: Aircraft, t: float) -> None:
        ac.phase = "taxi_out"
        self.emit("pushback.complete", ac.callsign, {})
        for c in list(self.active.get(ac.callsign, [])):
            if c.action == "pushback_approved":
                self._complete(c)

    def _apply_taxi(self, ac: Aircraft, c: Clearance) -> bool:
        res: Resolved = c.params["_resolved"]
        origin, prefix = self._route_origin(ac)
        if origin is None:
            return False
        nodes = self.graph.route(origin, res["dst"], via=res["via"])
        if nodes is None:
            return False
        tail = self._lineup_tail(res["dep_end"], res["entry"]) if res["dep_end"] else ()
        path = self.graph.build_path(nodes, prefix=prefix, tail=tail)
        at_gate = ac.phase == "at_gate"
        ac.path, ac.s = path, 0.0
        ac.cleared_runs = {i for i, r in enumerate(path.runs) if r.s0 <= 0.5}
        ac.hold_s = None
        if ac.touchdown_t is not None:
            ac.roll_s_exit, ac.phase = 0.0, "taxi_in"
        if res["dep_end"]:
            ac.dep_end, ac.entry_node = res["dep_end"], res["entry"]
        if ac.kind == "departure":
            ac.unimpeded_s += path.free_flow_time()
        if at_gate:
            ac.phase = "taxi_out" if ac.kind == "departure" else "taxi_in"
        self.emit("taxi.route", ac.callsign, {"nodes": len(nodes), "length_m": round(path.length), "runs": [r.runway for r in path.runs]})
        return True

    def _run_index(self, ac: Aircraft, runway: str) -> Optional[int]:
        if ac.path is None:
            return None
        half = ac.length_m / 2
        for i, r in enumerate(ac.path.runs):
            if r.runway == runway and i not in ac.cleared_runs and r.s1 > ac.s - half:
                return i
        return None

    def _hold_short(self, ac: Aircraft, end: str) -> bool:
        pav = self.runway_pavement(end)
        if ac.path is None or pav is None:
            return False
        half = ac.length_m / 2
        stopped = False
        for i, r in enumerate(ac.path.runs):
            if r.runway == pav and r.s0 - half - HOLD_OFFSET_M > ac.s + ac.speed ** 2 / (2 * A_BRAKE):
                ac.cleared_runs.discard(i)
                stopped = True
        ac.ignore_hold = False
        return stopped

    def _cross(self, ac: Aircraft, c: Clearance) -> bool:
        pav = self.runway_pavement(c.params["runway"])
        i = self._run_index(ac, pav or "")
        if i is None or ac.path is None:
            return False
        ac.cleared_runs.add(i)
        run = ac.path.runs[i]
        est = max(run.s0 - ac.s, 0.0) / 3.0 + (run.s1 - run.s0) / 2.57 + 8.0
        self.res.set(run.runway, ac.callsign, "crossing", self.t, self.t + est)
        return True

    def _clear_final_run(self, ac: Aircraft, c: Clearance) -> bool:
        if ac.path is None or not ac.path.runs or ac.entry_node is None:
            return False
        ac.cleared_runs.add(len(ac.path.runs) - 1)
        return True

    def _go_around(self, ac: Aircraft, reason: str) -> bool:
        ac.phase, ac.landing_cleared = "go_around", False
        ac.climb_until = self.t + GO_AROUND_CLIMB_S
        self.res.release(ac.callsign, "landing")
        self.emit("go_around", ac.callsign, {"reason": reason, "runway": ac.runway})
        for c in list(self.active.get(ac.callsign, [])):
            if c.action == "cleared_to_land":
                c.state = "cancelled"
                self.active[ac.callsign].remove(c)
                self.emit("clearance.cancelled", ac.callsign, {"clearance_id": c.id, "reason": reason})
            elif c.action == "go_around":
                self._complete(c)
        return True

    # -- tools: runway config, decisions, observation -----------------------------------------------------
    def set_runway_config(self, arrival_runways: List[str], departure_runways: List[str]) -> Dict[str, Any]:
        for e in list(arrival_runways) + list(departure_runways):
            if e not in self.graph.end_runway:
                return _err("unknown runway end %r; known: %s" % (e, ", ".join(sorted(self.graph.end_runway))))
        if not arrival_runways and not departure_runways:
            return _err("at least one runway must be configured")
        self.runway_cfg = {"arrival_runways": list(arrival_runways), "departure_runways": list(departure_runways)}
        self.emit("runway.config", None, dict(self.runway_cfg))
        return {"ok": True}

    def record_decision(self, **fields: Any) -> Dict[str, Any]:
        obs = self.observe()
        d = make_decision(self._next_id("d"), self.t, snapshot_summary(self.t, obs, self.weather_view()), **fields)
        ev = self.emit("decision", d["callsign"], {"decision_id": d["id"], "action": d["action"], "summary": d["summary"]})
        d["linked_event_ids"] = list(fields.get("linked_event_ids", [])) + [ev["event_id"]]
        self.decisions[d["id"]] = d
        return {"ok": True, "decision_id": d["id"]}

    def observe(self) -> Dict[str, Any]:
        age, ac_list = self.obs.observe(self.scenario.observation_delay_s, self.scenario.observation_noise_m, self.rng)
        pending = [{"id": r.id, "callsign": r.callsign, "clearance_id": r.clearance_id, "text": next(
            (v.text for v in self.voice if v.id == r.voice_id), ""), "parsed": r.parsed}
            for r in self.readbacks.values() if r.delivered and not r.resolved]
        return {"t": self.t, "data_age_s": age, "aircraft": ac_list,
                "runways": [self.res.view(r, self.t) for r in self.graph.runways],
                "runway_config": dict(self.runway_cfg), "weather": self.weather_view(), "pending_readbacks": pending,
                "clearances": [c.view() for c in self.clearances.values() if c.state in ("issued", "acknowledged")]}

    # -- physics tick ----------------------------------------------------------------------------------------
    def runway_busy(self, runway: str, ac: Aircraft) -> bool:
        if any(o != ac.callsign for o in self.res.occupants(runway)):
            return True
        mine = next((r.start_s for r in self.res.items[runway] if r.owner == ac.callsign), None)
        for r in self.res.overlapping(runway, self.t, self.t + CROSS_EST_S, exclude=ac.callsign):
            if r.kind in ("landing", "takeoff") or mine is None or r.start_s < mine:
                return True
        return False

    def _physics_tick(self, t: float) -> None:
        movers = [a for a in self.aircraft.values()
                  if not a.removed and a.path is not None and (a.phase in GROUND_PHASES or a.phase == "landing_roll")]
        for ac in list(self.aircraft.values()):
            if ac.removed or ac.phase in ("at_gate", "parked"):
                continue
            ph = ac.phase
            if ph == "final":
                self._tick_final(ac, t)
            elif ph == "go_around":
                if self.physics.step_go_around(ac, t):
                    ac.phase = "final"
                    ac.wp = self._reentry_point(ac)
            elif ph == "pushback":
                if self.physics.step_pushback(ac, t):
                    self._pushback_done(ac, t)
            elif ph in ("takeoff_roll", "airborne"):
                self.physics.step_takeoff(ac)
                if ph == "airborne" and ac.airborne_t is not None and t - ac.airborne_t > 40.0:
                    ac.removed = True
                    self.emit("aircraft.removed", ac.callsign, {"reason": "departed"})
            elif ac.path is not None:
                self.physics.step_ground(ac, movers)
                if ac.kind == "vehicle":
                    self._vehicle_turnaround(ac)
                else:
                    self._after_ground(ac, t)
            if ac.kind != "vehicle":
                self._stopped_timer(ac, t)
        self._complete_clearances(t)

    def _stopped_timer(self, ac: Aircraft, t: float) -> None:
        if ac.speed < 0.1 and ac.phase in GROUND_PHASES:
            if ac.stopped_since is None:
                ac.stopped_since = t
        else:
            ac.stopped_since = None

    def _vehicle_turnaround(self, ac: Aircraft) -> None:
        assert ac.path is not None and isinstance(ac, Vehicle)
        if ac.s >= ac.path.length - 0.3 and ac.speed < 0.5:
            ac.forward = not ac.forward
            nodes = ac.patrol if ac.forward else ac.patrol[::-1]
            ac.path, ac.s = self.graph.build_path(nodes), 0.0

    def _reentry_point(self, ac: Aircraft) -> Any:
        rw, info, _ = self.graph.end_info(ac.runway or "")
        return tuple(self.graph.node_xy[info["node"]] - unit(info["heading_deg"]) * REENTRY_M)

    def _tick_final(self, ac: Aircraft, t: float) -> None:
        a = self.physics.approach_distance(ac)
        if ac.wp is None and not ac.landing_cleared and a < GO_AROUND_A_M and ac.runway:
            self._go_around(ac, "no landing clearance")
            return
        if self.physics.step_final(ac):
            self._touchdown(ac, t)

    def _touchdown(self, ac: Aircraft, t: float) -> None:
        end = ac.runway or ""
        rw, info, d = self.graph.end_info(end)
        occupants = [o for o in self.res.occupants(rw.id) if o != ac.callsign]
        if occupants:
            self.emit("conflict", ac.callsign, {"type": "landing_on_occupied_runway", "runway": rw.id, "landing": ac.callsign,
                                                 "occupants": occupants})
        stand = self.graph.stands[ac.gate or ""]
        exit_node = ac.exit_node or self.choose_exit(end, ac.type, stand.node)
        if exit_node is None or exit_node not in rw.chain:
            exit_node = rw.chain[-1 if d == 1 else 0]
        i, j = rw.chain.index(info["node"]), rw.chain.index(exit_node)
        chain = rw.chain[i:j + 1] if i <= j else rw.chain[j:i + 1][::-1]
        rest = self.graph.route(exit_node, stand.node) or [exit_node]
        thr = self.graph.node_xy[info["node"]]
        prefix: Optional[Piece] = None
        if math.hypot(ac.x - thr[0], ac.y - thr[1]) > 0.5:
            prefix = (None, np.array([[ac.x, ac.y], thr]), "runway", rw.id, None)
        path = self.graph.build_path(chain + rest[1:], prefix=prefix)
        ac.path, ac.s, ac.phase, ac.touchdown_t = path, 0.0, "landing_roll", t
        ac.alt_m = 0.0
        ac.cleared_runs = {0}
        ac.exit_node = exit_node
        ac.roll_vtd = max(ac.speed * 0.96, EXIT_SPEED + 5.0)
        ac.roll_s_exit = path.node_s.get(exit_node, path.runs[0].s1 if path.runs else 100.0)
        run0 = path.runs[0] if path.runs else None
        clear_of_runway = (run0.s1 + ac.length_m / 2 + 10.0) if run0 else 0.0
        ac.hold_s = min(clear_of_runway, path.length)
        roll = ac.roll_s_exit / (0.5 * (ac.roll_vtd + EXIT_SPEED))
        ac.unimpeded_s = roll + path.free_flow_time(ac.roll_s_exit)
        ac.speed = min(ac.speed, ac.roll_vtd + 6.0)
        self.emit("aircraft.touchdown", ac.callsign, {"runway": rw.id, "end": end, "exit": exit_node,
                                                      "exit_distance_m": round(ac.roll_s_exit)})

    def liftoff(self, ac: Aircraft) -> None:
        ac.phase, ac.airborne_t = "airborne", self.t
        ac.alt_m = 1.0
        self.res.release(ac.callsign, "takeoff")
        self.emit("aircraft.airborne", ac.callsign, {"runway": self.runway_pavement(ac.dep_end), "end": ac.dep_end,
                                                    "speed_mps": round(ac.speed, 1)})
        ac.unimpeded_s += ac.type.vlof_mps / ac.type.takeoff_accel
        self._finish(ac, "departure")
        for c in list(self.active.get(ac.callsign, [])):
            if c.action in ("cleared_for_takeoff", "taxi_via", "line_up_and_wait"):
                self._complete(c)

    def _begin_takeoff(self, ac: Aircraft) -> None:
        rw, info, d = self.graph.end_info(ac.dep_end or "")
        far = rw.chain[-1 if d == 1 else 0]
        pts = np.vstack([np.array([ac.x, ac.y])] + [p[1] for p in self.graph.chain_pieces(rw.id, ac.entry_node or "", far)])
        keep = [pts[0]] + [q for q, c in zip(pts[1:], np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])[1:])
                           if c > LINE_UP_M]
        runway_piece: Piece = (None, np.array(keep), "runway", rw.id, None)
        end_dir = unit(info["heading_deg"])
        ext: Piece = (None, np.array([keep[-1], keep[-1] + end_dir * 15000.0]), "runway", None, None)
        ac.path, ac.s, ac.phase = Track([runway_piece, ext]), 0.0, "takeoff_roll"
        ac.cleared_runs, ac.hold_s = {0}, None
        self.res.set(rw.id, ac.callsign, "takeoff", self.t, self.t + 70.0)
        self.emit("takeoff.roll", ac.callsign, {"runway": rw.id, "end": ac.dep_end})

    def _after_ground(self, ac: Aircraft, t: float) -> None:
        p = ac.path
        assert p is not None
        half = ac.length_m / 2
        if ac.kind == "arrival":
            if ac.touchdown_t is not None:
                run0 = p.runs[0] if p.runs else None
                if ac.s < ac.roll_s_exit:
                    ac.phase = "landing_roll"
                elif run0 is not None and ac.s - half < run0.s1 and ac.phase != "taxi_in":
                    ac.phase = "runway_exit"
                else:
                    ac.phase = "taxi_in"
            if ac.s >= p.length - 0.3 and ac.speed < 0.3 and ac.phase == "taxi_in":
                self._arrive_at_gate(ac, t)
            return
        if ac.phase in ("taxi_out", "hold_short", "line_up"):
            final = p.runs[-1] if (ac.entry_node and p.runs) else None
            at_end = ac.s >= p.length - 0.3 and ac.speed < 0.3
            if final is not None and ac.s + half > final.s0 and (len(p.runs) - 1) in ac.cleared_runs:
                ac.phase = "line_up"
            elif ac.speed < 0.2 and ac.stop_why in ("run", "busy", "hold") and ac.stop_s - ac.s < 3.0:
                ac.phase = "hold_short"
            elif not at_end or ac.entry_node is None:
                ac.phase = "taxi_out"
            if ac.phase == "line_up" and at_end:
                if ac.takeoff_cleared:
                    self._begin_takeoff(ac)
                else:
                    self.res.set(self.runway_pavement(ac.dep_end) or "", ac.callsign, "line_up", t - 1.0, t + 30.0)
                    for c in list(self.active.get(ac.callsign, [])):
                        if c.action == "line_up_and_wait":
                            self._complete(c)

    def _arrive_at_gate(self, ac: Aircraft, t: float) -> None:
        ac.phase, ac.speed = "parked", 0.0
        self.emit("aircraft.parked", ac.callsign, {"gate": ac.gate})
        self._finish(ac, "arrival")
        for c in list(self.active.get(ac.callsign, [])):
            if c.action in ("taxi_via",):
                self._complete(c)

    def _finish(self, ac: Aircraft, kind: str) -> None:
        if ac.done:
            return
        ac.done = True
        if kind == "arrival":
            actual = self.t - (ac.touchdown_t or self.t)
        else:
            actual = self.t - ac.scheduled_s
        self.emit("aircraft.completed", ac.callsign, {
            "kind": kind, "actual_s": round(actual, 1), "unimpeded_s": round(ac.unimpeded_s, 1),
            "delay_s": round(actual - ac.unimpeded_s, 1), "type": ac.type.icao})

    def _complete_clearances(self, t: float) -> None:
        for cs, lst in list(self.active.items()):
            ac = self.aircraft.get(cs)
            if ac is None:
                continue
            half = ac.length_m / 2
            for c in list(lst):
                if c.state != "acknowledged":
                    continue
                p = ac.path
                if c.action == "cross_runway" and p is not None:
                    pav = self.runway_pavement(c.params["runway"])
                    run = next((r for r in p.runs if r.runway == pav and r.s0 - 1.0 <= ac.s), None)
                    if run is not None and ac.s - half > run.s1:
                        self._complete(c)
                        self.res.release(cs, "crossing")
                elif c.action == "hold_short" and ac.speed < 0.2 and ac.stop_why in ("run", "busy"):
                    self._complete(c)
                elif c.action == "hold_position" and ac.speed < 0.2:
                    self._complete(c)
                elif c.action == "cleared_to_land" and ac.phase == "taxi_in":
                    self._complete(c)
                elif c.action == "go_around" and ac.phase == "go_around":
                    self._complete(c)

    # -- reservations and occupancy ------------------------------------------------------------------------------
    def _update_reservations(self, t: float) -> None:
        for ac in self.aircraft.values():
            if ac.removed or ac.kind == "vehicle":
                continue
            if ac.phase == "final" and ac.landing_cleared and ac.runway:
                eta = self.physics.approach_distance(ac) / max(ac.speed, 30.0)
                self.res.set(self.runway_pavement(ac.runway) or "", ac.callsign, "landing", t + max(eta - 8.0, 0.0), t + eta + 65.0)
            elif ac.phase in ("landing_roll", "runway_exit"):
                self.res.set(self.runway_pavement(ac.runway) or "", ac.callsign, "landing", t - 1.0, t + 12.0)
            elif ac.phase == "taxi_in" and ac.touchdown_t is not None:
                self.res.release(ac.callsign, "landing")
            elif ac.phase == "takeoff_roll":
                self.res.set(self.runway_pavement(ac.dep_end) or "", ac.callsign, "takeoff", t - 1.0, t + 15.0)
            elif ac.phase == "line_up" and not ac.takeoff_cleared:
                for r in self.res.items[self.runway_pavement(ac.dep_end) or ""]:
                    if r.owner == ac.callsign and r.kind == "line_up":
                        r.end_s = max(r.end_s, t + 30.0)
            self._track_occupancy(ac, t)

    def _track_occupancy(self, ac: Aircraft, t: float) -> None:
        on: Optional[str] = None
        if ac.path is not None and ac.alt_m < 3.0 and (ac.phase in GROUND_PHASES or ac.phase in ("landing_roll", "takeoff_roll")):
            on = ac.path.pavement_intervals(ac.s, ac.length_m / 2)
        for rw in self.graph.runways:
            if rw == on:
                if ac.callsign not in self.res.open[rw]:
                    self.res.enter(rw, ac.callsign, t)
                    self.emit("runway.enter", ac.callsign, {"runway": rw})
            elif ac.callsign in self.res.open[rw]:
                self.res.leave(rw, ac.callsign, t)
                self.emit("runway.exit", ac.callsign, {"runway": rw})
                self.res.release(ac.callsign, "crossing", rw)
                if ac.kind == "arrival":
                    self.res.release(ac.callsign, "landing", rw)
                if ac.phase in ("airborne",):
                    self.res.release(ac.callsign, "takeoff", rw)

    # -- conflict detection ---------------------------------------------------------------------------------------
    def _detect(self, t: float) -> None:
        live = [a for a in self.aircraft.values() if not a.removed and a.phase not in ("at_gate", "parked")]
        for rw in self.graph.runways:
            occ = self.res.occupants(rw)
            current = set()
            for i in range(len(occ)):
                for j in range(i + 1, len(occ)):
                    key = ("overlap", rw, occ[i], occ[j])
                    current.add(key)
                    if key not in self._open_pairs:
                        self._open_pairs.add(key)
                        self.emit("conflict", occ[i], {"type": "runway_overlap", "runway": rw, "a": occ[i], "b": occ[j]})
            for key in [k for k in self._open_pairs if k[0] == "overlap" and k[1] == rw and k not in current]:
                self._open_pairs.discard(key)
        ground = [a for a in live if a.phase in GROUND_PHASES or a.phase in ("landing_roll", "takeoff_roll")]
        air = [a for a in live if a.phase in ("final", "go_around")]
        seen = set()
        for grp, kind in ((ground, "ground"), (air, "air")):
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    a, b = grp[i], grp[j]
                    d = math.hypot(a.x - b.x, a.y - b.y)
                    if kind == "ground":
                        limit = (a.wingspan_m + b.wingspan_m) / 2 + GROUND_SEP_MARGIN_M
                    else:
                        same = self.runway_pavement(a.runway) == self.runway_pavement(b.runway)
                        limit = AIR_SEP_SAME_RWY_M if same else AIR_SEP_OTHER_M
                    key = ("sep", a.callsign, b.callsign)
                    if d < limit:
                        seen.add(key)
                        if key not in self._open_pairs:
                            self._open_pairs.add(key)
                            self.emit("separation_loss", a.callsign, {"a": a.callsign, "b": b.callsign, "kind": kind,
                                                                       "distance_m": round(d, 1), "minimum_m": round(limit, 1)})
        for key in [k for k in self._open_pairs if k[0] == "sep" and k not in seen]:
            self._open_pairs.discard(key)
        self._detect_deadlocks(t, ground)

    def _detect_deadlocks(self, t: float, ground: List[Aircraft]) -> None:
        waits = {a.callsign: a.blocked_by for a in ground if a.blocked_by and a.speed < 0.1 and ":" not in a.blocked_by}
        cycles = set()
        for start in waits:
            seen, cur = [], start
            while cur in waits and cur not in seen:
                seen.append(cur)
                cur = waits[cur]
            if cur in seen:
                cycles.add(tuple(sorted(seen[seen.index(cur):])))
        for cyc in cycles:
            since = self._wait_since.setdefault(cyc, t)
            if t - since >= DEADLOCK_S and cyc not in self._deadlocks:
                self._deadlocks.add(cyc)
                self.emit("deadlock", cyc[0], {"aircraft": list(cyc), "waited_s": round(t - since, 1)})
        for key in [k for k in self._wait_since if k not in cycles]:
            del self._wait_since[key]

    def _phase_events(self) -> None:
        for ac in self.aircraft.values():
            if self._phase_prev.get(ac.callsign) != ac.phase:
                self.emit("aircraft.phase", ac.callsign, {"from": self._phase_prev.get(ac.callsign), "to": ac.phase})
                self._phase_prev[ac.callsign] = ac.phase

    # -- control and snapshots -------------------------------------------------------------------------------------
    def control(self, action: str, value: Any = None) -> Dict[str, Any]:
        if action == "pause":
            self.clock.paused = True
        elif action == "resume":
            self.clock.paused = False
        elif action == "speed":
            try:
                self.clock.set_speed(int(value))
            except (TypeError, ValueError):
                return _err("speed must be one of %s" % (SPEEDS,))
        elif action == "trigger_haboob":
            res = self.trigger_haboob()
            if not res["ok"]:
                return res
        elif action == "reset":
            speed, paused = self.clock.speed, self.clock.paused
            self.log.close()
            self._resets += 1
            self._init_state()
            self.clock.speed, self.clock.paused = speed, paused
        else:
            return _err("unknown control action %r" % action)
        return {"ok": True, "state": {"paused": self.clock.paused, "speed": self.clock.speed, "controller": self.controller_name}}

    def _reset_sent(self) -> None:
        """Forget what has been sent to clients: the next delta() starts from the current log position."""
        self._sent: Dict[str, Any] = {"aircraft": {}, "clearances": {}, "voice": {}, "runways": "", "weather": "", "score": "",
                                      "meta": "", "decisions": 0, "events": len(self.log.events)}

    def score(self) -> Dict[str, Any]:
        n = len(self.log.events)
        if self._score_cache[0] != n:
            self._score_cache = (n, compute_score(self.log.events, self.t))
        return self._score_cache[1]

    def _meta(self) -> Dict[str, Any]:
        return {"speed": self.clock.speed, "paused": self.clock.paused,
                "agent": {"status": getattr(self.controller, "status", "idle"), "controller": self.controller_name},
                "scenario": {"name": self.scenario.name, "duration_s": self.scenario.duration_s,
                             "haboob_triggered": self.weather.trigger_s is not None}}

    def _runway_views(self) -> List[Dict[str, Any]]:
        return [self.res.view(r, self.t) for r in self.graph.runways]

    def snapshot(self, mark_sent: bool = True) -> Dict[str, Any]:
        """Full contract snapshot. With mark_sent the next delta() is relative to this snapshot."""
        live = [a for a in self.aircraft.values() if not a.removed]
        aircraft = [views.aircraft_view(self, a) for a in live]
        runways = self._runway_views()
        weather = self.weather_view()
        if mark_sent:
            self._reset_sent()
            self._sent["aircraft"] = {a["id"]: json.dumps(a, sort_keys=True) for a in aircraft}
            self._sent["clearances"] = {c.id: (c.state, c.ack_t, c.done_t) for c in self.clearances.values()}
            self._sent.update(runways=json.dumps(runways, sort_keys=True), weather=json.dumps(weather, sort_keys=True),
                              score=json.dumps(self.score(), sort_keys=True), meta=json.dumps(self._meta(), sort_keys=True),
                              decisions=len(self.decisions), events=len(self.log.events))
            self._sent["voice"] = {v.id: v.status for v in self.voice}
        return dict({"type": "snapshot", "t": self.t}, **self._meta(), weather=weather, aircraft=aircraft, runways=runways,
                    clearances=[c.view() for c in self.clearances.values()],
                    decisions=list(self.decisions.values()), voice=[v.view() for v in self.voice], score=self.score())

    def delta(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": "delta", "t": self.t}
        live = {a.callsign: a for a in self.aircraft.values() if not a.removed}
        changed = []
        for cs, a in live.items():
            v = views.aircraft_view(self, a)
            fp = json.dumps(v, sort_keys=True)
            if self._sent["aircraft"].get(cs) != fp:
                self._sent["aircraft"][cs] = fp
                changed.append(v)
        if changed:
            out["aircraft"] = changed
        removed = [cs for cs in self._sent["aircraft"] if cs not in live]
        for cs in removed:
            del self._sent["aircraft"][cs]
        if removed:
            out["removed"] = removed
        cl = [c.view() for c in self.clearances.values() if self._sent["clearances"].get(c.id) != (c.state, c.ack_t, c.done_t)]
        if cl:
            out["clearances"] = cl
            for c in self.clearances.values():
                self._sent["clearances"][c.id] = (c.state, c.ack_t, c.done_t)
        for key, val in (("runways", self._runway_views()), ("weather", self.weather_view()), ("score", self.score())):
            fp = json.dumps(val, sort_keys=True)
            if self._sent[key] != fp:
                self._sent[key] = fp
                out[key] = val
        meta = self._meta()
        fp = json.dumps(meta, sort_keys=True)
        if self._sent["meta"] != fp:
            self._sent["meta"] = fp
            out.update(meta)
        decisions = list(self.decisions.values())
        if len(decisions) > self._sent["decisions"]:
            out["decisions"] = decisions[self._sent["decisions"]:]
            self._sent["decisions"] = len(decisions)
        voice = [v for v in self.voice if self._sent["voice"].get(v.id) != v.status]
        if voice:
            out["voice"] = [v.view() for v in voice]
            self._sent["voice"].update({v.id: v.status for v in voice})
        if len(self.log.events) > self._sent["events"]:
            out["events"] = [brief(e) for e in self.log.events[self._sent["events"]:]][-200:]
            self._sent["events"] = len(self.log.events)
        return out


def _vehicle_type() -> AircraftType:
    return AircraftType("GSE", "L", "I", "F", 3.0, 6.0, 10.0, 15.0, 1.5, 1.4)
