"""Load and validate the three airport files (config.json, airport.geojson, graph.json)."""
import json
from collections import deque
from pathlib import Path

AIRPORT_DIR = Path(__file__).resolve().parent.parent / "airport"
FEATURE_KINDS = {"runway", "taxiway", "hold_short", "parking"}


class AirportError(ValueError):
    pass


def _need(obj, keys, where):
    if not isinstance(obj, dict):
        raise AirportError(f"{where}: expected an object")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise AirportError(f"{where}: missing required key(s) {missing}")


def _check_lonlat(pair, where):
    if not (isinstance(pair, (list, tuple)) and len(pair) >= 2):
        raise AirportError(f"{where}: coordinate must be [lon, lat], got {pair!r}")
    lon, lat = pair[0], pair[1]
    if not (isinstance(lon, (int, float)) and -180 <= lon <= 180):
        raise AirportError(f"{where}: longitude {lon!r} out of range")
    if not (isinstance(lat, (int, float)) and -90 <= lat <= 90):
        raise AirportError(f"{where}: latitude {lat!r} out of range")


def validate(config, geojson, graph):
    _need(config, ["name", "icao", "map"], "config.json")
    _need(config["map"], ["center", "zoom"], "config.json map")
    _check_lonlat(config["map"]["center"], "config.json map.center")

    _need(graph, ["nodes", "edges", "approaches"], "graph.json")
    nodes = graph["nodes"]
    for nid, n in nodes.items():
        _need(n, ["lat", "lon", "kind"], f"graph.json node {nid}")
        _check_lonlat([n["lon"], n["lat"]], f"graph.json node {nid}")
        if n["kind"] not in FEATURE_KINDS:
            raise AirportError(f"graph.json node {nid}: unknown kind {n['kind']!r}")
    adj = {nid: [] for nid in nodes}
    for i, e in enumerate(graph["edges"]):
        _need(e, ["from", "to", "kind", "name", "length_m"], f"graph.json edge #{i}")
        for end in ("from", "to"):
            if e[end] not in nodes:
                raise AirportError(f"graph.json edge #{i} ({e['name']}): '{end}' node {e[end]!r} does not exist")
        adj[e["from"]].append(e["to"])
        adj[e["to"]].append(e["from"])
    for des, a in graph["approaches"].items():
        _need(a, ["runway", "threshold_node", "path"], f"graph.json approach {des}")
        if a["threshold_node"] not in nodes:
            raise AirportError(f"graph.json approach {des}: threshold_node {a['threshold_node']!r} does not exist")
        if len(a["path"]) < 2:
            raise AirportError(f"graph.json approach {des}: path needs at least 2 points")
        for p in a["path"]:
            _check_lonlat(p, f"graph.json approach {des} path")

    _need(geojson, ["type", "features"], "airport.geojson")
    if geojson["type"] != "FeatureCollection":
        raise AirportError("airport.geojson: type must be FeatureCollection")
    runways = 0
    for i, f in enumerate(geojson["features"]):
        _need(f, ["geometry", "properties"], f"airport.geojson feature #{i}")
        props = f["properties"]
        _need(props, ["kind"], f"airport.geojson feature #{i} properties")
        kind = props["kind"]
        if kind not in FEATURE_KINDS:
            raise AirportError(f"airport.geojson feature #{i}: unknown kind {kind!r}")
        coords = f["geometry"]["coordinates"]
        for p in coords if f["geometry"]["type"] == "LineString" else [coords]:
            _check_lonlat(p, f"airport.geojson feature #{i} ({kind})")
        if kind == "runway":
            runways += 1
            _need(props, ["id", "width_m", "ends"], f"airport.geojson runway #{i}")
            for end in props["ends"]:
                _need(end, ["designator", "heading", "threshold"], f"airport.geojson runway {props['id']} end")
                _check_lonlat(end["threshold"], f"airport.geojson runway {props['id']} end {end['designator']}")
                if end["designator"] not in graph["approaches"]:
                    raise AirportError(f"runway end {end['designator']!r} has no approach in graph.json")
        elif kind == "taxiway":
            _need(props, ["name"], f"airport.geojson taxiway #{i}")
        else:
            _need(props, ["id"], f"airport.geojson {kind} #{i}")
            if props["id"] not in nodes:
                raise AirportError(f"airport.geojson {kind} {props['id']!r} has no matching graph node")
    if not runways:
        raise AirportError("airport.geojson: no runway feature")

    for nid, n in nodes.items():
        if n["kind"] != "parking":
            continue
        seen, todo = {nid}, deque([nid])
        while todo:
            cur = todo.popleft()
            if nodes[cur]["kind"] == "runway":
                break
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        else:
            raise AirportError(f"parking node {nid!r} cannot reach any runway node through the graph")


def load(directory=AIRPORT_DIR):
    directory = Path(directory)
    files = {}
    for key, name in (("config", "config.json"), ("geojson", "airport.geojson"), ("graph", "graph.json")):
        try:
            files[key] = json.loads((directory / name).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise AirportError(f"{name}: cannot load ({e})") from e
    validate(files["config"], files["geojson"], files["graph"])
    return files
