"""Generate the demo scenarios from the airline mix, the aircraft CSV and the graph stands (seeded, reproducible).

Run: python -m sim.scenarios.generate
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from sim.graph.model import AirportGraph

OUT = Path(__file__).resolve().parent
OSM = Path(__file__).resolve().parents[2] / "data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json"
# airline -> (weight, fleet at KPHX drawn from the aircraft CSV); weights are placeholders for the BTS carrier mix
AIRLINES: Dict[str, Any] = {
    "SWA": (40, ["B737", "B738", "B38M", "B39M"]), "AAL": (24, ["A319", "A320", "A321", "B738"]),
    "SKW": (10, ["E75L", "CRJ7", "CRJ9"]), "ENY": (5, ["E75S", "E170"]), "DAL": (7, ["B738", "A321", "B752"]),
    "UAL": (6, ["B738", "B739", "A320"]), "FFT": (4, ["A320", "A21N"]), "ASA": (4, ["B738", "B39M"]),
}
GA_TYPES = ["C172", "SR22", "PC12"]


def pick_airline(rng: np.random.Generator) -> str:
    names = list(AIRLINES)
    w = np.array([AIRLINES[n][0] for n in names], dtype=float)
    return str(rng.choice(names, p=w / w.sum()))


def ga_stands(graph: AirportGraph) -> List[str]:
    for el in json.loads(OSM.read_text())["elements"]:
        if el.get("tags", {}).get("name") == "General Aviation Ramp" and el["type"] == "way":
            poly = Polygon([graph.frame.to_xy(g["lon"], g["lat"]) for g in el["geometry"]])
            names = [s.name for s in graph.stands.values() if s.kind == "parking" and poly.contains(Point(*s.xy))]
            if names:
                return sorted(names)
    return sorted(s.name for s in graph.stands.values() if s.kind == "parking")


def usable_gates(graph: AirportGraph) -> List[str]:
    """Gates with a taxi route to a departure entry of 25R and 26."""
    ok = []
    entries = {e: graph.entries(e)[:3] for e in ("25R", "26")}
    for s in graph.stands.values():
        if s.kind != "gate":
            continue
        if all(graph.route(s.lane_node, n) for e in entries.values() for n, _ in e[:1]):
            ok.append(s.name)
    return sorted(ok)


def spread(rng: np.random.Generator, gates: List[str], n: int) -> List[str]:
    """Pick n gates spread over the concourses (first letter) rather than clustered."""
    groups: Dict[str, List[str]] = {}
    for g in gates:
        groups.setdefault(g[0], []).append(g)
    keys = sorted(groups)
    rng.shuffle(keys)
    chosen: List[str] = []
    i = 0
    while len(chosen) < n:
        pool = [g for g in groups[keys[i % len(keys)]] if g not in chosen]
        if pool:
            chosen.append(str(rng.choice(pool)))
        i += 1
    return chosen


def vehicle_route(graph: AirportGraph, rng: np.random.Generator, avoid: List[LineString]) -> Optional[List[str]]:
    import networkx as nx
    sub = nx.Graph()
    for e in graph.edges.values():
        if e.kind in ("taxilane", "taxiway") and e.pavement is None:
            sub.add_edge(e.u, e.v, length=e.length)
    nodes = sorted(sub.nodes)
    keep_out = unary_union(avoid).buffer(70.0)
    for _ in range(400):
        a, b = rng.choice(nodes, 2, replace=False)
        try:
            route = nx.shortest_path(sub, str(a), str(b), weight="length")
        except nx.NetworkXNoPath:
            continue
        line = LineString([graph.node_xy[n] for n in route])
        if 300 < line.length < 1000 and not line.intersects(keep_out):
            return [str(n) for n in route]
    return None


def build(name: str, seed: int, n_arr: int, n_dep: int, arr_etas: List[float], dep_ready: List[float], haboob: Optional[float],
          ga: bool, vehicle: bool, pushback_mean_s: float, duration: float) -> Dict[str, Any]:
    graph = AirportGraph.load()
    rng = np.random.default_rng(seed)
    gates = usable_gates(graph)
    chosen = spread(rng, gates, n_arr + n_dep)
    numbers = rng.choice(np.arange(100, 4900), size=n_arr + n_dep, replace=False)
    flights: List[Dict[str, Any]] = []
    prefs = ["26", "25L"]
    for i in range(n_arr + n_dep):
        arr = i < n_arr
        airline = pick_airline(rng)
        ty = str(rng.choice(AIRLINES[airline][1]))
        flights.append({"callsign": "%s%d" % (airline, numbers[i]), "airline": airline, "type": ty,
                        "kind": "arrival" if arr else "departure",
                        "scheduled_sim_s": float(arr_etas[i] if arr else dep_ready[i - n_arr]), "gate": chosen[i],
                        "runway_preference": prefs[i % 2] if arr else None})
    if ga:
        gs = ga_stands(graph)
        dep_idx = [i for i, f in enumerate(flights) if f["kind"] == "departure"][2]
        flights[dep_idx].update(callsign="N%d%s" % (int(rng.integers(100, 999)), "SP"), airline="GA",
                                type=str(rng.choice(GA_TYPES)), gate=str(rng.choice(gs)))
    avoid: List[LineString] = []
    for f in flights:
        st = graph.stands[f["gate"]]
        for end in ("25R", "26", "25L"):
            for n, _ in graph.entries(end)[:2]:
                r = graph.route(st.lane_node, n)
                if r:
                    avoid.append(LineString([graph.node_xy[x] for x in r]))
    vehicles: List[Dict[str, Any]] = []
    if vehicle:
        route = vehicle_route(graph, rng, avoid)
        if route:
            vehicles.append({"id": "GSE1", "route": route, "speed_mps": 6.0})
    sc: Dict[str, Any] = {"name": name, "seed": seed, "duration_s": duration, "start_sim_s": 0.0,
                          "pushback_mean_s": pushback_mean_s, "readback_error_rate": 0.1 if haboob else 0.0,
                          "flights": sorted(flights, key=lambda f: f["scheduled_sim_s"]), "vehicles": vehicles}
    if haboob is not None:
        sc["haboob_trigger_s"] = haboob
        sc["weather"] = {"start_utc": "2025-08-26 00:20", "sec_per_min": 2.0}
    return sc


def main() -> None:
    smoke = build("kphx_smoke", 11, 3, 3, [95, 205, 315], [5, 70, 140], None, False, False, 60.0, 300.0)
    haboob = build("kphx_haboob", 25, 6, 6, [75, 140, 205, 270, 335, 395], [0, 15, 40, 70, 105, 145], 180.0, True, True, 75.0, 480.0)
    for sc in (smoke, haboob):
        (OUT / (sc["name"] + ".json")).write_text(json.dumps(sc, indent=1) + "\n")
        print("wrote", sc["name"], [(f["callsign"], f["type"], f["kind"][:3], f["gate"], f["scheduled_sim_s"]) for f in sc["flights"]])


if __name__ == "__main__":
    main()
