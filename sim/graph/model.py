"""Loaded airport graph: geometry in a local metric frame, routing, runway chains and movement paths."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np

from sim.graph.geo import Frame, bearing_deg

DEFAULT_GRAPH = FsPath(__file__).resolve().parents[2] / "data/processed/kphx_graph.json"
KT = 0.514444
V_STRAIGHT = 15 * KT   # taxi on straights
V_TURN = 8 * KT        # in turns and on stand leads
V_CROSS = 5 * KT       # across runway pavement
RES = 2.0              # speed-limit sample spacing (m)

Piece = Tuple[Optional[str], np.ndarray, str, Optional[str], Optional[str]]  # edge id, pts, kind, pavement, ref


@dataclass
class Edge:
    id: str
    u: str
    v: str
    pts: np.ndarray  # oriented u -> v
    length: float
    kind: str
    ref: str
    pavement: Optional[str]


@dataclass
class Stand:
    name: str
    node: str
    lane_node: str
    xy: np.ndarray
    kind: str
    heading_in: float


@dataclass
class Runway:
    id: str
    chain: List[str]
    chain_s: Dict[str, float]
    ends: Dict[str, Dict[str, Any]]
    length_m: float
    polygon_ll: List[List[float]]


@dataclass
class Seg:
    s0: float
    s1: float
    edge: Optional[str]
    kind: str
    pavement: Optional[str]
    to_node: Optional[str] = None


@dataclass
class Run:
    s0: float
    s1: float
    runway: str


class Track:
    """A polyline route with arc-length lookups, per-segment metadata and a speed-limit profile."""

    def __init__(self, pieces: Sequence[Piece]) -> None:
        pts: List[np.ndarray] = []
        self.segs: List[Seg] = []
        last: Optional[np.ndarray] = None
        s = 0.0
        for eid, p, kind, pav, _ref in pieces:
            p = np.asarray(p, dtype=float)
            if last is not None and np.allclose(p[0], last, atol=1e-6):
                p = p[1:]
            if len(p) == 0:
                self.segs.append(Seg(s, s, eid, kind, pav))
                continue
            seq = np.vstack([last, p]) if last is not None else p
            d = float(np.sum(np.hypot(*np.diff(seq, axis=0).T))) if len(seq) > 1 else 0.0
            pts.append(p)
            self.segs.append(Seg(s, s + d, eid, kind, pav))
            s += d
            last = p[-1]
        self.pts = np.vstack(pts) if pts else np.zeros((1, 2))
        steps = np.hypot(*np.diff(self.pts, axis=0).T) if len(self.pts) > 1 else np.zeros(0)
        self.cum = np.concatenate([[0.0], np.cumsum(steps)])
        self.length = float(self.cum[-1])
        self.node_s: Dict[str, float] = {}
        self.runs = self._runs()
        self._vlim = self._limits()

    # -- geometry ----------------------------------------------------------------
    def xy_at(self, s: Any) -> np.ndarray:
        s = np.clip(s, 0.0, self.length)
        return np.stack([np.interp(s, self.cum, self.pts[:, 0]), np.interp(s, self.cum, self.pts[:, 1])], axis=-1)

    def heading_at(self, s: float, look: float = 4.0) -> float:
        a, b = self.xy_at(max(0.0, s - look)), self.xy_at(min(self.length, s + look))
        if np.allclose(a, b):
            b = self.xy_at(min(self.length, s + 20.0))
        return bearing_deg((a[0], a[1]), (b[0], b[1]))

    def seg_at(self, s: float) -> Optional[Seg]:
        for seg in self.segs:
            if seg.s0 - 1e-6 <= s <= seg.s1 + 1e-6:
                return seg
        return self.segs[-1] if self.segs else None

    def _runs(self) -> List[Run]:
        runs: List[Run] = []
        for seg in self.segs:
            if seg.pavement is None:
                continue
            if runs and runs[-1].runway == seg.pavement and abs(runs[-1].s1 - seg.s0) < 1e-3:
                runs[-1].s1 = seg.s1
            else:
                runs.append(Run(seg.s0, seg.s1, seg.pavement))
        return runs

    def pavement_intervals(self, s: float, half_len: float) -> Optional[str]:
        """Runway whose pavement the interval [s - half_len, s + half_len] overlaps, if any."""
        for r in self.runs:
            if s + half_len > r.s0 and s - half_len < r.s1:
                return r.runway
        return None

    # -- speed profile -------------------------------------------------------------
    def _limits(self) -> np.ndarray:
        n = int(self.length // RES) + 2
        ss = np.arange(n) * RES
        lim = np.full(n, V_STRAIGHT)
        for seg in self.segs:
            m = (ss >= seg.s0) & (ss <= seg.s1)
            if seg.kind == "lead":
                lim[m] = np.minimum(lim[m], V_TURN)
            elif seg.pavement is not None and seg.kind != "runway":
                lim[m] = np.minimum(lim[m], V_CROSS)
        if self.length > 30:
            p0, p1, p2 = self.xy_at(ss - 15), self.xy_at(ss), self.xy_at(ss + 15)
            h1 = np.degrees(np.arctan2(p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1]))
            h2 = np.degrees(np.arctan2(p2[:, 0] - p1[:, 0], p2[:, 1] - p1[:, 1]))
            turn = np.abs((h2 - h1 + 180) % 360 - 180) > 25
            lim[turn] = np.minimum(lim[turn], V_TURN)
        return lim

    def speed_limit(self, s: float) -> float:
        i = min(max(int(s // RES), 0), len(self._vlim) - 1)
        return float(self._vlim[i])

    def lookahead_limit(self, s: float, dist: float) -> float:
        """Minimum speed limit over the next dist meters (so aircraft slow before corners)."""
        i0 = min(max(int(s // RES), 0), len(self._vlim) - 1)
        i1 = min(len(self._vlim), i0 + int(dist // RES) + 2)
        return float(self._vlim[i0:i1].min())

    def free_flow_time(self, from_s: float = 0.0) -> float:
        """Seconds to drive the path (from from_s) at the speed limits with no interference."""
        return float(np.sum(RES / np.maximum(self._vlim[min(int(from_s // RES), len(self._vlim) - 1):], 0.5)))


class AirportGraph:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        self.meta = data["meta"]
        self.frame = Frame(*self.meta["origin"])
        self.node_xy: Dict[str, np.ndarray] = {
            n["id"]: np.array(self.frame.to_xy(n["lon"], n["lat"])) for n in data["nodes"]}
        self.edges: Dict[str, Edge] = {}
        self._pair: Dict[Tuple[str, str], Tuple[str, bool]] = {}
        self.g = nx.Graph()
        for e in data["edges"]:
            pts = np.array([self.frame.to_xy(lo, la) for lo, la in e["coords"]])
            edge = Edge(e["id"], e["from"], e["to"], pts, float(e["length_m"]), e["kind"], e["ref"] or "", e["pavement"])
            self.edges[edge.id] = edge
            self._pair[(edge.u, edge.v)] = (edge.id, False)
            self._pair[(edge.v, edge.u)] = (edge.id, True)
            self.g.add_edge(edge.u, edge.v, eid=edge.id, length=edge.length)
        self.stands: Dict[str, Stand] = {}
        for s in data["stands"]:
            lead = self.edge_between(s["lane_node"], s["node"])
            assert lead is not None
            pts = lead[0].pts if not lead[1] else lead[0].pts[::-1]
            heading = bearing_deg(tuple(pts[0]), tuple(pts[-1]))
            self.stands[s["name"]] = Stand(s["name"], s["node"], s["lane_node"], self.node_xy[s["node"]], s["kind"], heading)
        self.runways: Dict[str, Runway] = {}
        self.end_runway: Dict[str, str] = {}
        for r in data["runways"]:
            chain = r["nodes"]
            cs = {chain[0]: 0.0}
            for a, b in zip(chain, chain[1:]):
                cs[b] = cs[a] + self.edges[self._pair[(a, b)][0]].length
            self.runways[r["id"]] = Runway(r["id"], chain, cs, r["ends"], cs[chain[-1]], r["pavement"])
            for end in r["ends"]:
                self.end_runway[end] = r["id"]
        self.hold_runways = [(h["node"], h["runway"]) for h in data["holds"] if h["runway"]]
        self.taxiway_refs = sorted({e.ref for e in self.edges.values() if e.kind in ("taxiway", "taxilane")})

    @classmethod
    def load(cls, path: Optional[FsPath] = None) -> "AirportGraph":
        return cls(json.loads(FsPath(path or DEFAULT_GRAPH).read_text()))

    # -- lookups -------------------------------------------------------------------
    def edge_between(self, u: str, v: str) -> Optional[Tuple[Edge, bool]]:
        hit = self._pair.get((u, v))
        return (self.edges[hit[0]], hit[1]) if hit else None

    def piece(self, u: str, v: str) -> Piece:
        e, rev = self.edge_between(u, v)  # type: ignore[misc]
        pts = e.pts[::-1] if rev else e.pts
        return (e.id, pts, e.kind, e.pavement, e.ref)

    def end_info(self, end: str) -> Tuple[Runway, Dict[str, Any], int]:
        """Runway, end record and direction (+1 / -1 along the chain when landing or departing from that end)."""
        rw = self.runways[self.end_runway[end]]
        info = rw.ends[end]
        return rw, info, (1 if info["node"] == rw.chain[0] else -1)

    def other_end(self, end: str) -> str:
        rw = self.runways[self.end_runway[end]]
        return next(e for e in rw.ends if e != end)

    def lonlat(self, xy: Any) -> Tuple[float, float]:
        return self.frame.to_lonlat(float(xy[0]), float(xy[1]))

    # -- routing -------------------------------------------------------------------
    def route(self, src: str, dst: str, via: Optional[Iterable[str]] = None, cross_penalty: float = 250.0) -> Optional[List[str]]:
        letters = tuple(v.upper() for v in via) if via else ()

        def weight(u: str, v: str, d: Dict[str, Any]) -> Optional[float]:
            e = self.edges[d["eid"]]
            if e.kind == "runway":
                return None
            w = e.length
            if e.pavement:
                w += cross_penalty
            if letters:
                w *= 1.0 if any(e.ref.upper().startswith(l) for l in letters) else 4.0
            return w

        try:
            return nx.shortest_path(self.g, src, dst, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def route_length(self, nodes: Sequence[str]) -> float:
        return sum(self.edges[self._pair[(a, b)][0]].length for a, b in zip(nodes, nodes[1:]))

    def taxiways_on(self, nodes: Sequence[str]) -> List[str]:
        """Distinct taxiway letters along a route in order (connector numbers dropped: B5 -> B)."""
        out: List[str] = []
        for a, b in zip(nodes, nodes[1:]):
            e = self.edges[self._pair[(a, b)][0]]
            if e.kind == "lead" or e.pavement or not e.ref or not e.ref[0].isalpha() or e.ref.startswith("U"):
                continue
            letter = e.ref.rstrip("0123456789") or e.ref
            if not out or out[-1] != letter:
                out.append(letter)
        return out

    # -- runway helpers --------------------------------------------------------------
    def connectors(self, runway: str, node: str) -> List[str]:
        """Neighbours of a runway chain node that are on the pavement but not on the runway centerline."""
        rw = self.runways[runway]
        out = []
        for nb in self.g.neighbors(node):
            e = self.edges[self.g[node][nb]["eid"]]
            if e.kind != "runway" and nb not in rw.chain:
                out.append(nb)
        return out

    def entries(self, end: str) -> List[Tuple[str, float]]:
        """Runway chain nodes reachable from taxiways, with distance from the given end (m), nearest first."""
        rw, info, _ = self.end_info(end)
        res = [(n, abs(rw.chain_s[n] - rw.chain_s[info["node"]])) for n in rw.chain if self.connectors(rw.id, n)]
        return sorted(res, key=lambda t: t[1])

    def chain_pieces(self, runway: str, a: str, b: str) -> List[Piece]:
        rw = self.runways[runway]
        i, j = rw.chain.index(a), rw.chain.index(b)
        seq = rw.chain[i:j + 1] if i <= j else rw.chain[j:i + 1][::-1]
        return [self.piece(u, v) for u, v in zip(seq, seq[1:])]

    def build_path(self, nodes: Sequence[str], prefix: Optional[Piece] = None, tail: Sequence[Piece] = ()) -> Track:
        pieces: List[Piece] = [prefix] if prefix is not None else []
        pieces += [self.piece(u, v) for u, v in zip(nodes, nodes[1:])]
        pieces += list(tail)
        path = Track(pieces)
        first = 1 if prefix is not None else 0
        if nodes:
            path.node_s[nodes[0]] = path.segs[first - 1].s1 if first else 0.0
        if first and nodes:
            path.segs[0].to_node = nodes[0]
        for n, seg in zip(nodes[1:], path.segs[first:]):
            path.node_s[n] = seg.s1
            seg.to_node = n
        return path
