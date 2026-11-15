"""Build the routable KPHX taxi graph from OSM aeroway data and FAA pavement polygons.

Run: python -m sim.graph.build_graph
Outputs data/processed/kphx_graph.json (routing graph) and kphx_graph.geojson (map layers).
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from shapely.geometry import LineString, MultiPoint, Point, Polygon, shape
from shapely.ops import transform as shp_transform

from sim.graph.geo import Frame, XY, bearing_deg, polyline_length

ROOT = Path(__file__).resolve().parents[2]
OSM_PATH = ROOT / "data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json"
FAA_DIR = ROOT / "data/kphx_geometry"
OUT_DIR = ROOT / "data/processed"
HOLD_ATTACH_M = 15.0        # BUILD_PROMPT 5.1: attach holds within 15 m of an edge
HOLD_RESCUE_M = 40.0        # detached holds up to this far are still attached (counted as fixed)
STAND_ATTACH_M = 250.0
DEAD_END_SNAP_M = 6.0
SNAP_TOL_M = 1.0
MIN_TAXI_FRACTION = 0.95
TAXI_KINDS = ("taxiway", "taxilane")

Key = Any  # OSM node id (int) or synthetic id (str)


class Way:
    """A polyline of [key, x, y] vertices with OSM tags."""

    def __init__(self, wid: Any, kind: str, ref: Optional[str], surface: Optional[str], pts: List[list]) -> None:
        self.id, self.kind, self.ref, self.surface, self.pts = wid, kind, ref, surface, pts

    @property
    def line(self) -> LineString:
        return LineString([(p[1], p[2]) for p in self.pts])


class Builder:
    def __init__(self) -> None:
        self.osm = json.loads(OSM_PATH.read_text())["elements"]
        self.frame = Frame(-112.0116, 33.4343)
        self.report: Dict[str, Any] = {}
        self._n = 0
        self.ways: List[Way] = []
        self.pavements: Dict[str, Polygon] = {}
        self.forced: set = set()  # synthetic keys that must be graph nodes
        self.crossing_keys: List[Tuple[Key, str]] = []
        self.hold_records: List[Dict[str, Any]] = []
        self.stand_records: List[Dict[str, Any]] = []

    # -- helpers -----------------------------------------------------------------
    def new_key(self, prefix: str) -> str:
        self._n += 1
        return "%s%d" % (prefix, self._n)

    def xy(self, el: Dict[str, Any]) -> XY:
        return self.frame.to_xy(el["lon"], el["lat"])

    def insert_point(self, way: Way, x: float, y: float, prefix: str, tol: float = SNAP_TOL_M) -> Tuple[Key, float, XY]:
        """Insert the projection of (x, y) into way (or snap to a vertex). Returns key, distance, position."""
        best: Optional[Tuple[float, int, float, float]] = None
        for i in range(len(way.pts) - 1):
            ax, ay = way.pts[i][1], way.pts[i][2]
            bx, by = way.pts[i + 1][1], way.pts[i + 1][2]
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
            px, py = ax + t * dx, ay + t * dy
            d = math.hypot(x - px, y - py)
            if best is None or d < best[0]:
                best = (d, i, px, py)
        assert best is not None
        d, i, px, py = best
        for j in (i, i + 1):
            if math.hypot(way.pts[j][1] - px, way.pts[j][2] - py) < tol:
                self.forced.add(way.pts[j][0])
                return way.pts[j][0], d, (way.pts[j][1], way.pts[j][2])
        key = self.new_key(prefix)
        way.pts.insert(i + 1, [key, px, py])
        self.forced.add(key)
        return key, d, (px, py)

    def nearest_way(self, x: float, y: float, kinds: Tuple[str, ...], exclude: Optional[Way] = None,
                    bias: Optional[Dict[str, float]] = None) -> Tuple[Optional[Way], float]:
        p = Point(x, y)
        best, best_d = None, 1e18
        for w in self.ways:
            if w.kind not in kinds or w is exclude:
                continue
            d = w.line.distance(p) - (bias or {}).get(w.kind, 0.0)
            if d < best_d:
                best, best_d = w, d
        return best, best_d

    # -- loading -----------------------------------------------------------------
    def load_pavements(self) -> None:
        data = json.loads((FAA_DIR / "faa_arcgis_Runways/runways_phx_bbox.geojson").read_text())
        for f in data["features"]:
            name = f["properties"]["DESIGNATOR"]
            if "/" in name:
                self.pavements[name] = shp_transform(lambda lon, lat, z=None: self.frame.to_xy(lon, lat), shape(f["geometry"]))
        order = sorted(self.pavements, key=lambda n: -self.pavements[n].centroid.y)
        self.report["runway_order_north_to_south"] = order
        print("Runway order from FAA polygons (north to south): %s" % ", ".join(
            "%s (%.4f N)" % (n, self.frame.to_lonlat(0, self.pavements[n].centroid.y)[1]) for n in order))
        expected = ["08/26", "07L/25R", "07R/25L"]
        print("  matches expected 08/26 > 07L/25R > 07R/25L: %s" % (order == expected))

    def load_ways(self) -> None:
        runway_parts: Dict[str, List[Way]] = defaultdict(list)
        for el in self.osm:
            if el["type"] != "way":
                continue
            tags = el.get("tags", {})
            kind = tags.get("aeroway")
            if kind not in TAXI_KINDS + ("runway",):
                continue
            pts = [[n, *self.frame.to_xy(g["lon"], g["lat"])] for n, g in zip(el["nodes"], el["geometry"])]
            way = Way(el["id"], kind, tags.get("ref"), tags.get("surface"), pts)
            if kind == "runway":
                runway_parts[tags["ref"]].append(way)
            else:
                self.ways.append(way)
        for ref, parts in runway_parts.items():
            self.ways.append(self.merge_runway(ref, parts))
            print("Runway %s: merged %d OSM way(s)" % (ref, len(parts)))

    @staticmethod
    def merge_runway(ref: str, parts: List[Way]) -> Way:
        uniq: Dict[Key, list] = {}
        for p in parts:
            for pt in p.pts:
                uniq.setdefault(pt[0], pt)
        pts = list(uniq.values())
        a = min(pts, key=lambda q: q[1])
        b = max(pts, key=lambda q: q[1])
        dx, dy = b[1] - a[1], b[2] - a[2]
        pts.sort(key=lambda q: (q[1] - a[1]) * dx + (q[2] - a[2]) * dy)
        return Way("rwy-" + ref, "runway", ref, "concrete", pts)

    # -- defect fixes --------------------------------------------------------------
    def node_degree_ends(self) -> Counter:
        """Count of ways each vertex key appears in."""
        c: Counter = Counter()
        for w in self.ways:
            for k in set(p[0] for p in w.pts):
                c[k] += 1
        return c

    def dead_ends(self) -> List[Tuple[Way, int]]:
        c = self.node_degree_ends()
        out = []
        for w in self.ways:
            if w.kind == "runway":
                continue
            for idx in (0, len(w.pts) - 1):
                if c[w.pts[idx][0]] == 1:
                    out.append((w, idx))
        return out

    def fix_names(self) -> None:
        unnamed = [w for w in self.ways if w.kind == "taxiway" and not w.ref]
        by_key: Dict[Key, List[Way]] = defaultdict(list)
        for w in self.ways:
            for p in w.pts:
                by_key[p[0]].append(w)
        fixed = 0
        for _ in range(3):  # propagate through chains of unnamed ways
            for w in unnamed:
                if w.ref:
                    continue
                votes = Counter(o.ref for p in w.pts for o in by_key[p[0]] if o is not w and o.ref and o.kind == "taxiway")
                if votes:
                    w.ref = votes.most_common(1)[0][0]
                    fixed += 1
        rest = [w for w in unnamed if not w.ref]
        for w in rest:
            w.ref = "U%s" % w.id
        print("Defect: unnamed taxiway ways %d (inherited a name: %d, labelled unknown: %d)" % (len(unnamed), fixed, len(rest)))
        self.report["unnamed_ways"] = len(unnamed)

    def snap_dead_ends(self) -> None:
        ends = self.dead_ends()
        snapped = 0
        for w, idx in ends:
            x, y = w.pts[idx][1], w.pts[idx][2]
            other, d = self.nearest_way(x, y, TAXI_KINDS + ("runway",), exclude=w)
            if other is None or d > DEAD_END_SNAP_M:
                continue
            key, _, (px, py) = self.insert_point(other, x, y, "s")
            w.pts[idx] = [key, px, py]
            snapped += 1
        after = len(self.dead_ends())
        print("Defect: dead ends %d (snapped to a neighbouring way: %d, genuine dead ends remaining: %d)" % (len(ends), snapped, after))
        self.report["dead_ends_before"] = len(ends)
        self.report["dead_ends_after"] = after

    # -- attachments ---------------------------------------------------------------
    def nearest_runway(self, x: float, y: float) -> Tuple[str, float]:
        p = Point(x, y)
        name = min(self.pavements, key=lambda n: self.pavements[n].distance(p))
        return name, self.pavements[name].distance(p)

    def attach_holds(self) -> None:
        holds = [e for e in self.osm if e["type"] == "node" and e.get("tags", {}).get("aeroway") == "holding_position"]
        detached = fixed = dropped = 0
        for h in holds:
            x, y = self.xy(h)
            way, d = self.nearest_way(x, y, TAXI_KINDS)
            if way is None or d > HOLD_RESCUE_M:
                dropped += 1
                detached += 1
                continue
            if d > HOLD_ATTACH_M:
                detached += 1
                fixed += 1
            key, _, (px, py) = self.insert_point(way, x, y, "h")
            tags = h["tags"]
            htype = tags.get("holding_position:type", "movement")
            runway, rd = self.nearest_runway(px, py)
            self.hold_records.append({"osm_id": h["id"], "key": key, "type": htype, "x": px, "y": py,
                                      "runway": runway if htype in ("runway", "ILS") and rd < 250 else None})
        print("Defect: detached hold positions %d beyond %.0f m (attached within %.0f m: %d, dropped: %d); holds kept: %d"
              % (detached, HOLD_ATTACH_M, HOLD_RESCUE_M, fixed, dropped, len(self.hold_records)))
        self.report["holds_detached"] = detached

    def cut_runway_boundaries(self) -> None:
        for name, poly in self.pavements.items():
            boundary = poly.exterior
            for w in list(self.ways):
                if w.kind not in TAXI_KINDS:
                    continue
                inter = w.line.intersection(boundary)
                if inter.is_empty:
                    continue
                pts = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
                for g in pts:
                    if g.geom_type != "Point":
                        continue
                    key, _, _ = self.insert_point(w, g.x, g.y, "x")
                    if (key, name) not in self.crossing_keys:
                        self.crossing_keys.append((key, name))
        print("Crossing nodes (taxi edges meeting runway pavement boundaries): %d" % len(self.crossing_keys))

    def attach_stands(self) -> None:
        gates = [e for e in self.osm if e["type"] == "node" and e.get("tags", {}).get("aeroway") == "gate"]
        parks = []
        for e in self.osm:
            if e.get("tags", {}).get("aeroway") != "parking_position" or not e["tags"].get("ref"):
                continue
            if e["type"] == "node":
                parks.append((e["tags"]["ref"], self.xy(e)))
            elif e["type"] == "way":
                c = LineString([self.frame.to_xy(g["lon"], g["lat"]) for g in e["geometry"]]).centroid
                parks.append((e["tags"]["ref"], (c.x, c.y)))
        stands: List[Tuple[str, str, XY]] = [(g["tags"].get("ref", str(g["id"])), "gate", self.xy(g)) for g in gates]
        gate_pts = MultiPoint([s[2] for s in stands])
        for ref, p in parks:
            if gate_pts.distance(Point(p)) > 25:
                stands.append(("P" + ref, "parking", p))
        names: Counter = Counter()
        for name, skind, (x, y) in stands:
            if any(Point(x, y).within(pv) for pv in self.pavements.values()):
                continue
            way, d = self.nearest_way(x, y, TAXI_KINDS, bias={"taxilane": 20.0})
            if way is None or d > STAND_ATTACH_M:
                continue
            names[name] += 1
            uid = name if names[name] == 1 else "%s-%d" % (name, names[name])
            key, dist, (px, py) = self.insert_point(way, x, y, "a")
            self.stand_records.append({"name": uid, "kind": skind, "x": x, "y": y, "key": key, "ax": px, "ay": py, "dist": dist})
        print("Stands attached to nearest taxilane/taxiway edge: %d (%d gates)" % (
            len(self.stand_records), sum(1 for s in self.stand_records if s["kind"] == "gate")))

    # -- graph assembly ------------------------------------------------------------
    def build_edges(self) -> None:
        occ = self.node_degree_ends()
        nodes: Dict[Key, XY] = {}
        edges: List[Dict[str, Any]] = []
        seen: Dict[Tuple[Key, Key], int] = {}
        runway_chain: Dict[str, List[Key]] = {}
        counter = [0]

        def add_edge(u: Key, v: Key, pts: List[XY], way: Way) -> None:
            if u == v or polyline_length(pts) < 0.05:
                if u == v and polyline_length(pts) > 1.0:  # loop: subdivide
                    h = len(pts) // 2
                    mid = self.new_key("m")
                    nodes[mid] = pts[h]
                    add_edge(u, mid, pts[: h + 1], way)
                    add_edge(mid, v, pts[h:], way)
                return
            pair = (u, v) if str(u) < str(v) else (v, u)
            if pair in seen:  # parallel edge: subdivide the newcomer
                total = polyline_length(pts)
                acc, cut = 0.0, len(pts) // 2
                for i in range(len(pts) - 1):
                    acc += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                    if acc >= total / 2:
                        cut = i + 1
                        break
                cut = max(1, min(len(pts) - 2, cut)) if len(pts) > 2 else None
                if cut is None:
                    mx, my = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
                    pts = [pts[0], (mx, my), pts[1]]
                    cut = 1
                mid = self.new_key("m")
                nodes[mid] = pts[cut]
                seen[pair] = 1
                add_edge(u, mid, pts[: cut + 1], way)
                add_edge(mid, v, pts[cut:], way)
                return
            seen[pair] = 1
            counter[0] += 1
            edges.append({"id": "e%d" % counter[0], "from": u, "to": v, "ref": way.ref, "kind": way.kind,
                          "surface": way.surface, "pts": pts, "way": way.id})

        for w in self.ways:
            cuts = [i for i, p in enumerate(w.pts)
                    if i in (0, len(w.pts) - 1) or occ[p[0]] > 1 or p[0] in self.forced]
            if w.kind == "runway":
                runway_chain[w.ref or ""] = [w.pts[i][0] for i in cuts]
            for a, b in zip(cuts, cuts[1:]):
                u, v = w.pts[a][0], w.pts[b][0]
                nodes[u] = (w.pts[a][1], w.pts[a][2])
                nodes[v] = (w.pts[b][1], w.pts[b][2])
                add_edge(u, v, [(p[1], p[2]) for p in w.pts[a:b + 1]], w)
        self.nodes, self.edges, self.runway_chain = nodes, edges, runway_chain
        print("Raw graph: %d nodes, %d edges" % (len(nodes), len(edges)))

    def add_stand_leads(self) -> None:
        for s in self.stand_records:
            sid = "stand:" + s["name"]
            self.nodes[sid] = (s["x"], s["y"])
            pts = [self.nodes[s["key"]], (s["x"], s["y"])]
            self.edges.append({"id": "l%s" % s["name"], "from": s["key"], "to": sid, "ref": "lead", "kind": "lead",
                               "surface": None, "pts": pts, "way": None})
            s["node"] = sid

    def keep_largest_component(self) -> None:
        g = nx.Graph()
        for e in self.edges:
            g.add_edge(e["from"], e["to"], length=polyline_length(e["pts"]), kind=e["kind"])
        comps = sorted(nx.connected_components(g), key=lambda c: -sum(
            d["length"] for _, _, d in g.subgraph(c).edges(data=True) if d["kind"] == "taxiway"))
        total = sum(d["length"] for _, _, d in g.edges(data=True) if d["kind"] == "taxiway")
        keep = comps[0]
        kept = sum(d["length"] for _, _, d in g.subgraph(keep).edges(data=True) if d["kind"] == "taxiway")
        frac = kept / total
        print("Components: %d; largest holds %.1f%% of taxiway length (%.1f of %.1f km)" % (
            len(comps), 100 * frac, kept / 1000, total / 1000))
        if frac < MIN_TAXI_FRACTION:
            sys.exit("FAIL: largest connected component holds %.1f%% of taxiway length (< 95%%)" % (100 * frac))
        self.edges = [e for e in self.edges if e["from"] in keep and e["to"] in keep]
        used = {e["from"] for e in self.edges} | {e["to"] for e in self.edges}
        self.nodes = {k: v for k, v in self.nodes.items() if k in used}
        n0 = len(self.stand_records)
        self.stand_records = [s for s in self.stand_records if s["node"] in used]
        self.hold_records = [h for h in self.hold_records if h["key"] in used]
        self.crossing_keys = [c for c in self.crossing_keys if c[0] in used]
        print("Dropped stands outside main component: %d" % (n0 - len(self.stand_records)))
        self.report["taxiway_fraction_kept"] = round(frac, 4)

    def flag_pavement(self) -> None:
        for e in self.edges:
            e["pavement"] = None
            if e["kind"] == "runway":
                e["pavement"] = e["ref"]
                continue
            mid = LineString(e["pts"]).interpolate(0.5, normalized=True)
            for name, poly in self.pavements.items():
                if poly.buffer(0.5).contains(mid):
                    e["pavement"] = name
                    break

    def runway_records(self) -> List[Dict[str, Any]]:
        thresholds = [(el["tags"]["ref"], self.xy(el)) for el in self.osm
                      if el["type"] == "node" and el.get("tags", {}).get("aeroway") == "threshold"]
        recs = []
        for ref, chain in self.runway_chain.items():
            chain = [k for k in chain if k in self.nodes]
            if len(chain) < 2:
                continue
            ends = {}
            for end_key, other_key in ((chain[0], chain[1]), (chain[-1], chain[-2])):
                pos = self.nodes[end_key]
                name = min(thresholds, key=lambda t: math.hypot(t[1][0] - pos[0], t[1][1] - pos[1]))[0]
                ends[name] = {"node": end_key, "heading_deg": round(bearing_deg(pos, self.nodes[other_key]), 1)}
            length = sum(polyline_length(e["pts"]) for e in self.edges if e["kind"] == "runway" and e["ref"] == ref)
            for name, info in ends.items():
                lon, lat = self.frame.to_lonlat(*self.nodes[info["node"]])
                info.update(lon=round(lon, 7), lat=round(lat, 7))
            faa_len = self.pavements[ref].length / 2 - 150 * 0.3048 if ref in self.pavements else 0
            print("Runway %s: ends %s, centerline %.0f m (FAA pavement ~%.0f m)" % (
                ref, {k: v["heading_deg"] for k, v in ends.items()}, length, faa_len))
            recs.append({"id": ref, "name": ref, "length_m": round(length, 1), "ends": ends, "nodes": chain})
        return sorted(recs, key=lambda r: -self.pavements[r["id"]].centroid.y)

    # -- output --------------------------------------------------------------------
    def lonlat(self, key: Key) -> List[float]:
        return [round(v, 7) for v in self.frame.to_lonlat(*self.nodes[key])]

    def write(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        runways = self.runway_records()
        node_list = [{"id": str(k), "lon": self.lonlat(k)[0], "lat": self.lonlat(k)[1]} for k in self.nodes]
        edge_list = [{"id": e["id"], "from": str(e["from"]), "to": str(e["to"]), "ref": e["ref"], "kind": e["kind"],
                      "surface": e["surface"], "length_m": round(polyline_length(e["pts"]), 2),
                      "pavement": e["pavement"], "coords": self.frame.line_to_lonlat(e["pts"])}
                     for e in self.edges]
        xs = [self.lonlat(k)[0] for k in self.nodes]
        ys = [self.lonlat(k)[1] for k in self.nodes]
        for poly in self.pavements.values():
            for x, y in poly.exterior.coords:
                lon, lat = self.frame.to_lonlat(x, y)
                xs.append(lon)
                ys.append(lat)
        bounds = [min(xs), min(ys), max(xs), max(ys)]
        meta = {"center": [round((bounds[0] + bounds[2]) / 2, 6), round((bounds[1] + bounds[3]) / 2, 6)],
                "bounds": [round(b, 6) for b in bounds], "origin": [self.frame.lon0, self.frame.lat0],
                "runway_order_north_to_south": self.report["runway_order_north_to_south"], "report": self.report}
        stands = [{"id": s["name"], "name": s["name"], "kind": s["kind"], "node": s["node"],
                   "lon": self.lonlat(s["node"])[0], "lat": self.lonlat(s["node"])[1], "lane_node": str(s["key"])}
                  for s in self.stand_records]
        holds = [{"id": "H%d" % h["osm_id"], "node": str(h["key"]), "type": h["type"], "runway": h["runway"],
                  "lon": self.lonlat(h["key"])[0], "lat": self.lonlat(h["key"])[1]} for h in self.hold_records]
        crossings = [{"id": "X%s" % k, "node": str(k), "runway": r, "lon": self.lonlat(k)[0], "lat": self.lonlat(k)[1]}
                     for k, r in self.crossing_keys]
        pav = {n: self.frame.line_to_lonlat(p.exterior.coords) for n, p in self.pavements.items()}
        for r in runways:
            r["pavement"] = pav[r["id"]]
            r["nodes"] = [str(k) for k in r["nodes"]]
            for end in r["ends"].values():
                end["node"] = str(end["node"])
        graph = {"meta": meta, "nodes": node_list, "edges": edge_list, "stands": stands, "holds": holds,
                 "runways": runways, "crossings": crossings}
        (OUT_DIR / "kphx_graph.json").write_text(json.dumps(graph, separators=(",", ":")))
        (OUT_DIR / "kphx_graph.geojson").write_text(json.dumps(self.geojson(graph, pav), separators=(",", ":")))

    def geojson(self, graph: Dict[str, Any], pav: Dict[str, Any]) -> Dict[str, Any]:
        feats: List[Dict[str, Any]] = []

        def add(kind: str, geom: Dict[str, Any], **props: Any) -> None:
            feats.append({"type": "Feature", "geometry": geom, "properties": dict(kind=kind, **props)})

        for layer, kind, key in (("faa_arcgis_AM_Apron/AM_Apron_phx_bbox.geojson", "apron", "DESIGNATOR"),
                                 ("faa_arcgis_AM_Taxiway/AM_Taxiway_phx_bbox.geojson", "taxiway_pavement", "DESIGNATOR")):
            for f in json.loads((FAA_DIR / layer).read_text())["features"]:
                add(kind, f["geometry"], ref=f["properties"].get(key))
        for name, ring in pav.items():
            add("runway_pavement", {"type": "Polygon", "coordinates": [ring]}, ref=name, id=name)
        for e in graph["edges"]:
            kind = "taxilane" if e["kind"] == "lead" else e["kind"]
            if kind == "runway":
                continue
            add(kind, {"type": "LineString", "coordinates": e["coords"]}, ref=e["ref"], id=e["id"], pavement=e["pavement"])
        for r in graph["runways"]:
            coords = []
            for e in graph["edges"]:
                if e["kind"] == "runway" and e["ref"] == r["id"]:
                    coords.extend(e["coords"])
            add("runway", {"type": "LineString", "coordinates": coords}, ref=r["id"], id=r["id"])
        for s in graph["stands"]:
            add("stand", {"type": "Point", "coordinates": [s["lon"], s["lat"]]}, name=s["name"], id=s["id"])
        for h in graph["holds"]:
            add("hold", {"type": "Point", "coordinates": [h["lon"], h["lat"]]}, runway=h["runway"], id=h["id"])
        for c in graph["crossings"]:
            add("crossing", {"type": "Point", "coordinates": [c["lon"], c["lat"]]}, runway=c["runway"], id=c["id"])
        return {"type": "FeatureCollection", "meta": {"center": graph["meta"]["center"], "bounds": graph["meta"]["bounds"]},
                "features": feats}


def build() -> Dict[str, Any]:
    b = Builder()
    b.load_pavements()
    b.load_ways()
    b.fix_names()
    b.snap_dead_ends()
    b.attach_holds()
    b.cut_runway_boundaries()
    b.attach_stands()
    b.build_edges()
    b.add_stand_leads()
    b.keep_largest_component()
    b.flag_pavement()
    b.write()
    total = sum(polyline_length(e["pts"]) for e in b.edges)
    kinds = Counter(e["kind"] for e in b.edges)
    print("Output: %d nodes, %d edges %s, %.1f km, %d stands, %d holds, %d crossings -> %s" % (
        len(b.nodes), len(b.edges), dict(kinds), total / 1000, len(b.stand_records), len(b.hold_records),
        len(b.crossing_keys), OUT_DIR))
    return b.report


if __name__ == "__main__":
    build()
