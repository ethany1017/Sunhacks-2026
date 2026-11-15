"""Adapter for the in-process KPHX simulator (sim/ at the repo root). The only backend module that imports `sim`."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # appended, so nothing in the repo root shadows backend packages

from sim.core.aircraft_types import get_type  # noqa: E402
from sim.core.engine import Simulation  # noqa: E402
from sim.core.physics import FINAL_START_M, unit  # noqa: E402
from sim.graph.model import AirportGraph  # noqa: E402

# The one table of levels. Repoint scenario_path here when the real levels (Normal Day, Light Rain) land.
LEVELS = [
    {"id": "smoke", "name": "Smoke test (6 aircraft)", "scenario_path": str(ROOT / "sim/scenarios/kphx_smoke.json")},
    {"id": "busy", "name": "Busy (12 aircraft)", "scenario_path": str(ROOT / "sim/scenarios/kphx_busy.json")},
]
CONTROLLERS = {"human": "Human (built-in tower for now)", "agent": "Agent"}
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

    # -- lifecycle --------------------------------------------------------------------------------
    def get_levels(self):
        return [dict(lv) for lv in LEVELS]

    def get_controllers(self):
        return dict(CONTROLLERS)

    def get_level(self):
        return dict(self.level, seed=self.sim.seed) if self.level else None

    def reset(self, level, seed=None):
        """Fresh run of a level (by id). The previous controller is not carried over: call set_controller next."""
        lv = next((x for x in LEVELS if x["id"] == level), None)
        if lv is None:
            raise ValueError(f"unknown level {level!r}")
        if self.sim is not None:
            self.sim.close()
        self.sim = Simulation(lv["scenario_path"], graph=self.graph, seed=seed)
        self.level, self.controller, self._seen_readbacks = lv, "human", set()  # Simulation starts with the tower

    def set_controller(self, kind, obj=None):
        """kind "human" plugs in the sim's built-in tower; kind "agent" plugs in `obj` (name, status, on_tick)."""
        if kind not in CONTROLLERS:
            raise ValueError(f"unknown controller {kind!r}")
        res = self.sim.set_controller("tower" if kind == "human" else obj)
        if not res.get("ok"):
            raise ValueError(res.get("error"))
        self.controller = kind

    @property
    def t(self):
        return self.sim.t

    @property
    def finished(self):
        return self.sim.finished

    def step(self):
        """Advance one 0.1 sim-second tick. Returns this tick's events in our names (t, type, callsign, data, reason)."""
        out = []
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
            elif k in ("scenario.complete", "scenario.day_end"):
                why = "Every flight was handled." if k == "scenario.complete" else "The day ended."
                out.append(_ev(t, "sim_end", None, {"why": k.split(".")[1]}, why))
        return sorted(out, key=lambda e: e["type"] == "sim_end")  # sim_end after this tick's referee_result

    # -- controller-safe views (observe(): delayed, noisy) ---------------------------------------------
    def get_airport(self):
        if self._airport is None:
            self._airport = _airport(self.graph)
        return self._airport

    def get_state(self):
        obs = self.sim.observe()
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
        new = [r for r in self.sim.observe()["pending_readbacks"] if r["id"] not in self._seen_readbacks]
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
        return self.sim.observe()["referee"]

    # -- metrics only: true state, never for controller decisions ------------------------------------------
    def metrics(self):
        """{t, aircraft: true positions (snapshot views), referee: full report with score}. Backend loop/dashboard only."""
        snap = self.sim.snapshot(mark_sent=False)
        return {"t": snap["t"], "aircraft": snap["aircraft"], "referee": snap["referee"]}


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
