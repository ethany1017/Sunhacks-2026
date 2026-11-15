"""Adapter for the in-process KPHX simulator (sim/ at the repo root). The only backend module that imports `sim`."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # appended, so nothing in the repo root shadows backend packages

from sim.core.aircraft_types import get_type  # noqa: E402
from sim.core.engine import Simulation  # noqa: E402
from sim.core.physics import FINAL_START_M, unit  # noqa: E402
from sim.graph.model import AirportGraph  # noqa: E402

from sim.levels import HELD_OUT_SEEDS, TRAINING_SEEDS  # noqa: E402

PRACTICE = ROOT / "backend/practice"
# The one table of levels: the two real levels first (dashboard default = the first, on its first training seed), then
# practice scenarios for training the agent (scenario files under backend/practice/, or a level run at a chosen length),
# then the original smoke/busy scenario files. `level`: a sim level id; `scenario_path`: a scenario file.
LEVELS = [
    {"id": "normal_day", "name": "Normal Day", "level": "normal_day"},
    {"id": "light_rain", "name": "Light Rain", "level": "light_rain"},
    {"id": "busy_arrivals", "name": "Practice: busy arrival bank", "practice": True,
     "scenario_path": str(PRACTICE / "busy_arrivals.json"),
     "description": "10 arrivals about 110 s apart on both finals (25L, 26): sequencing, landing clearances, taxi-in."},
    {"id": "departure_push", "name": "Practice: departure push", "practice": True,
     "scenario_path": str(PRACTICE / "departure_push.json"),
     "description": "10 departures ready inside 90 s plus 2 arrivals: pushback order, taxi conflicts, departure spacing."},
    {"id": "runway_crossing", "name": "Practice: runway crossing conflict", "practice": True,
     "scenario_path": str(PRACTICE / "runway_crossing.json"),
     "description": "5 arrivals on 25L must cross 25R while 5 departures use it: hold short, crossing vs takeoff timing."},
    {"id": "wrong_readback", "name": "Practice: wrong readbacks", "practice": True,
     "scenario_path": str(PRACTICE / "wrong_readback.json"),
     "description": "8 flights, half of the runway readbacks wrong (Roger or wrong runway): catch and reject them."},
    {"id": "runway_config_change", "name": "Practice: runway config change", "practice": True, "level": "normal_day",
     "duration_s": 600.0, "seed": 1,
     "description": "A 10 min Normal Day (training seed 1): the wind shift at 330 s forces west to east flow."},
    {"id": "rain_peak", "name": "Practice: rain peak", "practice": True, "level": "light_rain", "duration_s": 750.0,
     "seed": 21,
     "description": "A 12.5 min Light Rain day (training seed 21): rain spacing, slow taxi, go-arounds, plus the wind shift."},
    {"id": "smoke", "name": "Smoke test (6 aircraft)", "practice": True, "scenario_path": str(ROOT / "sim/scenarios/kphx_smoke.json"),
     "description": "The original 6-aircraft smoke scenario."},
    {"id": "busy", "name": "Busy (12 aircraft)", "practice": True, "scenario_path": str(ROOT / "sim/scenarios/kphx_busy.json"),
     "description": "The original 12-aircraft busy scenario."},
]
HUMAN = "human"  # the sim's Human controller (sim branch, S4); until it lands the adapter falls back to "tower"
CONTROLLERS = {"human": "Human (built-in tower for now)", "agent": "Agent"}  # "Human" once the sim accepts HUMAN
# sim event kind -> (alert level, message template); data is passed through
ALERTS = {
    "pilot.unable": ("warning", "{cs} unable {action} runway {runway}: tailwind {tailwind_kt} kt above the {limit_kt} kt limit."),
    "arrival.held": ("info", "Approach control is holding {cs} for spacing to runway {runway}."),
    "aircraft.diverted": ("critical", "{cs} diverted after holding {holding_s} s (limit {limit_s} s)."),
}
RUNWAY_WIDTH_M = 45  # all three KPHX runways are 150 ft wide
FINAL_POINTS = 12    # vertices on each straight final


def _ev(t, type, callsign=None, data=None, reason=""):
    return {"t": round(t, 1), "type": type, "callsign": callsign, "data": data or {}, "reason": reason}


class RealSimulator:
    def __init__(self):
        self.graph = AirportGraph.load()  # parsed once, shared by every run
        self.sim = None
        self.level = None
        self.controller = None  # "human" | "agent"
        self._airport = None
        self._seen_readbacks = set()
        self._obs = None  # this tick's observe(), enriched; see _observe()
        self._human_real = False  # True once the sim accepted set_controller(HUMAN)

    # -- lifecycle --------------------------------------------------------------------------------
    def get_levels(self):
        return [dict(lv) for lv in LEVELS]

    def get_controllers(self):
        return dict(CONTROLLERS, **({"human": "Human"} if self._human_real else {}))

    def get_level(self):
        """The current run's table entry plus its seed and, for a sim level, duration_s, tailwind_limit_kt, effects."""
        if not self.level:
            return None
        info = self.sim.level_view() or {}
        return dict(self.level, seed=self.sim.seed, **{k: info[k] for k in ("duration_s", "tailwind_limit_kt", "effects") if k in info})

    def get_seeds(self, level):
        """{training, heldout} seed lists for a level id. Held-out seeds are for final grading only."""
        lv = self._entry(level)
        if "seed" in lv:
            return {"training": [lv["seed"]], "heldout": []}
        if "level" in lv:
            return {"training": list(TRAINING_SEEDS[lv["level"]]), "heldout": list(HELD_OUT_SEEDS[lv["level"]])}
        return {"training": [json.loads(Path(lv["scenario_path"]).read_text())["seed"]], "heldout": []}

    @staticmethod
    def _entry(level):
        lv = next((x for x in LEVELS if x["id"] == level), None)
        if lv is None:
            raise ValueError(f"unknown level {level!r}")
        return lv

    def reset(self, level, seed=None):
        """Fresh run of a level (by id) with the Human plugged in; seed None = the entry's default seed."""
        lv = self._entry(level)
        if "level" in lv:  # raises on a bad seed: nothing changed yet
            new = Simulation(level=lv["level"], seed=lv.get("seed") if seed is None else seed,
                             duration_s=lv.get("duration_s"), graph=self.graph)
        else:
            new = Simulation(lv["scenario_path"], graph=self.graph, seed=seed)
        if self.sim is not None:
            self.sim.close()
        self.sim, self._obs = new, None
        self.level, self._seen_readbacks = lv, set()
        self.set_controller("human")

    def set_controller(self, kind, obj=None):
        """kind "human" plugs in the sim's Human (built-in "tower" until the sim has one); "agent" plugs in `obj`."""
        if kind not in CONTROLLERS:
            raise ValueError(f"unknown controller {kind!r}")
        if kind == "human":
            res = self.sim.set_controller(HUMAN)
            self._human_real = bool(res.get("ok"))
            if not self._human_real:
                res = self.sim.set_controller("tower")
        else:
            res = self.sim.set_controller(obj)
        if not res.get("ok"):
            raise ValueError(res.get("error"))
        self.controller, self._obs = kind, None

    @property
    def human_impl(self):
        """Which sim controller plays the Human: "human" once the sim has it, else the stand-in "tower"."""
        return self.sim.controller_name if self.controller == "human" else None

    def get_runway_config(self):
        """{arrival_runways, departure_runways, runway}: the active runway ends, plus a one-line label."""
        return _runway_data({k: list(v) for k, v in self.sim.runway_cfg.items()})

    @property
    def t(self):
        return self.sim.t

    @property
    def finished(self):
        return self.sim.finished

    def step(self):
        """Advance one 0.1 sim-second tick. Returns this tick's events in our names (t, type, callsign, data, reason)."""
        out = []
        self._obs = None
        for e in self.sim.step():
            k, cs, d, t = e["kind"], e["entity_id"], e["data"], e["sim_time_ms"] / 1000.0
            if k == "aircraft.spawned" and d.get("kind") != "vehicle":
                ac = self.sim.aircraft[cs]
                lon, lat = self.graph.lonlat((ac.x, ac.y))
                where = f"at gate {d['gate']}" if d["kind"] == "departure" else f"on final for runway {d['runway']}"
                out.append(_ev(t, "aircraft_spawn", cs, {
                    "kind": d["kind"], "aircraft_type": d["type"], "airline": d["airline"], "gate": d["gate"],
                    "runway": d["runway"], "lat": round(lat, 7), "lon": round(lon, 7), "heading": round(ac.heading, 1),
                    "phase": ac.phase}, f"{cs} ({d['type']}) appeared {where}."))
            elif k == "aircraft.removed":
                why = {"departed": "departed and left the airspace", "diverted": "diverted to its alternate"}
                out.append(_ev(t, "aircraft_exit", cs, {"reason": d["reason"]}, f"{cs} {why.get(d['reason'], d['reason'])}."))
            elif k == "aircraft.parked":
                out.append(_ev(t, "aircraft_exit", cs, {"reason": "parked", "gate": d["gate"]}, f"{cs} parked at gate {d['gate']}."))
            elif k == "referee_fail":
                out.append(_ev(t, "referee_fail", cs, dict(d), d["reason"]))
            elif k == "referee_result":
                out.append(_ev(t, "referee_result", None, dict(d), f"Referee verdict: {d['verdict']}."))
            elif k == "weather.update":
                out.append(_ev(t, "weather", None, dict(d), f"New weather report: {d['metar']}."))
            elif k == "runway.config":
                out.append(_ev(t, "runway_change", None, _runway_data(d),
                               f"Runway configuration: {_runway_data(d)['runway']}."))
            elif k in ALERTS:
                level, msg = ALERTS[k]
                msg = msg.format(cs=cs, **d)
                out.append(_ev(t, "alert", cs, dict(d, level=level, message=msg, source=k), msg))
            elif k in ("scenario.complete", "scenario.day_end"):
                why = "Every flight was handled." if k == "scenario.complete" else "The day ended."
                out.append(_ev(t, "sim_end", None, {"why": k.split(".")[1]}, why))
        return sorted(out, key=lambda e: e["type"] == "sim_end")  # sim_end after this tick's referee_result

    # -- controller-safe views (observe(): delayed, noisy) ---------------------------------------------
    def get_airport(self):
        if self._airport is None:
            self._airport = _airport(self.graph)
        return self._airport

    def _observe(self):
        # One observe() per tick (it draws noise); issue/resolve/set_runway_config don't invalidate: changes show next tick.
        if self._obs is None:
            self._obs = self._enrich(self.sim.observe())
        return copy.deepcopy(self._obs)  # callers may mutate their copy

    def get_state(self):
        return self._observe()

    @staticmethod
    def _enrich(obs):
        current = {}
        for c in obs["clearances"]:  # issued/acknowledged, in issue order: the last one per aircraft is the current one
            current[c["callsign"]] = c
        for a in obs["aircraft"]:
            if a["kind"] != "vehicle":
                ty = get_type(a["type"])
                a.update(wake=ty.cwt, wtc=ty.wtc)
            a["clearance"] = current.get(a["callsign"])
        return obs

    def issue(self, clearance):
        """clearance: {callsign, action, params?}. Returns {ok, clearance_id, text} or {ok: False, error}."""
        return self.sim.issue_clearance(clearance["callsign"], clearance["action"], clearance.get("params"))

    def get_readbacks(self):
        """Pilot readbacks delivered since the last call and not yet resolved."""
        new = [r for r in self._observe()["pending_readbacks"] if r["id"] not in self._seen_readbacks]
        self._seen_readbacks.update(r["id"] for r in new)
        return new

    def resolve_readback(self, readback_id, accept):
        return self.sim.resolve_readback(readback_id, accept)

    def set_runway_config(self, arrival_runways, departure_runways):
        return self.sim.set_runway_config(arrival_runways, departure_runways)

    def get_weather(self):
        return self.sim.weather_view()

    def get_referee(self):
        """Controller-safe Referee view: {verdict, fails, day_end_s} (no score)."""
        return self._observe()["referee"]

    # -- static layout helpers (airport graph and aircraft type table only: controller-safe) -------------------
    def type_info(self, icao):
        """Published performance of an ICAO type: wake categories, size, approach and lift-off speeds."""
        ty = get_type(icao)
        return {"icao": ty.icao, "wtc": ty.wtc, "cwt": ty.cwt, "light": ty.light, "wingspan_m": ty.wingspan_m,
                "length_m": ty.length_m, "vref_mps": ty.vref_mps, "vlof_mps": ty.vlof_mps}

    def runway_ends(self):
        """{end: {runway, heading, threshold_node, chain}}: chain = runway centreline nodes from this end to the far end."""
        out = {}
        for r in self.graph.runways.values():
            for des, info in r.ends.items():
                chain = r.chain if info["node"] == r.chain[0] else r.chain[::-1]
                out[des] = {"runway": r.id, "heading": info["heading_deg"], "threshold_node": info["node"], "chain": list(chain)}
        return out

    def stand_nodes(self, gate):
        st = self.graph.stands[gate]
        return {"node": st.node, "lane_node": st.lane_node}

    def choose_entry(self, end, origin, icao):
        """Nearest runway entry node (from `origin`) with enough runway left for this type to take off."""
        return self.sim.choose_entry(end, origin, get_type(icao))

    def choose_exit(self, end, icao, gate):
        """The runway exit node a landing of this type on `end` would use for its gate (the pilots' default)."""
        return self.sim.choose_exit(end, get_type(icao), self.graph.stands[gate].node)

    def taxi_route(self, src, dst):
        """The shortest taxi route the pilots fly when not given an explicit one (an arrival from its exit to the gate)."""
        return self.graph.route(src, dst)

    def runway_label(self, pavement):
        """The runway end name a clearance for this pavement uses in the active configuration (what readbacks repeat)."""
        return self.sim.runway_label(pavement)

    # -- metrics only: true state, never for controller decisions ------------------------------------------
    def metrics(self):
        """{t, aircraft: true positions (snapshot views), referee: full report with score}. Backend loop/dashboard only."""
        snap = self.sim.snapshot(mark_sent=False)
        return {"t": snap["t"], "aircraft": snap["aircraft"], "referee": snap["referee"]}


def _runway_data(cfg):
    return dict(cfg, runway=f"ARR {' '.join(cfg['arrival_runways'])} / DEP {' '.join(cfg['departure_runways'])}")


def _airport(g):
    """The sim's AirportGraph translated to our {config, geojson, graph} layout."""
    runway_nodes = {n for r in g.runways.values() for n in r.chain}
    holds = {h["node"]: h for h in g.data["holds"] if h["runway"]}
    stands = {s["node"]: s for s in g.data["stands"]}
    nodes = {}
    for n in g.data["nodes"]:
        nid = n["id"]
        kind = ("runway" if nid in runway_nodes else "hold_short" if nid in holds else "parking" if nid in stands
                else "taxiway")
        nodes[nid] = {"lat": n["lat"], "lon": n["lon"], "kind": kind}
    edges = [{"id": e["id"], "from": e["from"], "to": e["to"], "kind": e["kind"], "name": e["ref"] or "",
              "length_m": e["length_m"], "pavement": e["pavement"]} for e in g.data["edges"]]
    approaches = {}
    features = []
    for r in g.runways.values():
        ends = []
        for des, info in r.ends.items():
            thr = g.node_xy[info["node"]]
            u = unit(info["heading_deg"])
            path = [list(g.lonlat(thr - u * FINAL_START_M * (1 - i / (FINAL_POINTS - 1)))) for i in range(FINAL_POINTS)]
            approaches[des] = {"runway": r.id, "threshold_node": info["node"], "path": [[round(a, 7), round(b, 7)] for a, b in path]}
            ends.append({"designator": des, "heading": info["heading_deg"], "threshold": [info["lon"], info["lat"]]})
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [e["threshold"] for e in ends]},
                         "properties": {"kind": "runway", "id": r.id, "width_m": RUNWAY_WIDTH_M, "ends": ends}})
    labelled = {}  # main taxiway letter -> index of its longest edge feature, the only one the map labels
    for e in g.data["edges"]:
        if e["kind"] == "runway":
            continue
        ref = e["ref"] or ""
        if e["kind"] == "taxiway" and ref.isalpha():
            best = labelled.get(ref)
            if best is None or features[best]["properties"]["length_m"] < e["length_m"]:
                labelled[ref] = len(features)
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": e["coords"]},
                         "properties": {"kind": "taxiway", "name": ref, "edge_kind": e["kind"], "length_m": e["length_m"],
                                        "label": False}})
    for i in labelled.values():
        features[i]["properties"]["label"] = True
    for nid, h in holds.items():
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
                         "properties": {"kind": "hold_short", "id": nid, "runway": h["runway"]}})
    for nid, s in stands.items():
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                         "properties": {"kind": "parking", "id": nid, "name": s["name"], "stand_kind": s["kind"]}})
    crossings = [{"node": c["node"], "runway": c["runway"]} for c in g.data["crossings"]]
    meta = g.meta
    return {
        "config": {"name": "Phoenix Sky Harbor (KPHX)", "icao": "KPHX", "placeholder": False,
                   "map": {"center": meta["center"], "zoom": 14.2, "bearing": 0}},
        "geojson": {"type": "FeatureCollection", "features": features},
        "graph": {"nodes": nodes, "edges": edges, "approaches": approaches, "crossings": crossings,
                  "stands": {s["name"]: s["node"] for s in g.data["stands"]}},
    }
