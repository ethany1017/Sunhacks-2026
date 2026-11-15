"""Aircraft state and constant-speed movement along scenario legs.

Scenario format (simif/stub/scenarios/<name>.json, data only; validated by build()):
    {"name": str, "description": str, "runway": <active designator, a key of graph.json approaches>,
     "aircraft": [{"callsign": str, "kind": "arrival" | "departure", "aircraft_type": str,
                   "spawn_t": <sim seconds>, "legs": [<leg>, ...]}]}
A leg is {"phase": <one of PHASES>} plus exactly one of:
    "nodes": [ids]                                 follow graph nodes; consecutive ids must share an edge
    "approach": <designator>, "from_nm", "to_nm"   along that graph.json approach, nm from the threshold
    "hold_s": seconds                              wait in place (holding, or an initial parked wait)
    "extend_m": metres                             straight on along the current heading (climb-out)
Moving legs run at SPEED_KT[phase] and must start where the previous one ended. After the last leg
the aircraft is parked, or leaves the world if that leg was `departed`.
"""
import math
from dataclasses import dataclass

KT = 0.514444  # m/s per knot
NM = 1852.0
SPEED_KT = {"approach": 100, "landing": 90, "rollout": 50, "taxi": 18, "holding": 0, "lineup": 10,
            "takeoff": 70, "departed": 110, "parked": 0}
LEG_KINDS = ("nodes", "approach", "hold_s", "extend_m")


class ScenarioError(ValueError):
    pass


# --- geometry on [lon, lat] points (local flat-earth, fine at airport scale) ---
def _xy(p, ref):
    lat = math.radians(ref[1])
    return ((p[0] - ref[0]) * 111412.84 * math.cos(lat), (p[1] - ref[1]) * 111132.954)


def dist(a, b):
    return math.hypot(*_xy(b, a))


def bearing(a, b):
    x, y = _xy(b, a)
    return round(math.degrees(math.atan2(x, y)) % 360, 1)


def offset(p, heading, metres):
    lat = math.radians(p[1])
    h = math.radians(heading)
    return [p[0] + metres * math.sin(h) / (111412.84 * math.cos(lat)), p[1] + metres * math.cos(h) / 111132.954]


def point_at(path, s):
    """Point and heading at distance s along a polyline."""
    for a, b in zip(path, path[1:]):
        seg = dist(a, b)
        if s <= seg or b is path[-1]:
            f = min(s / seg, 1.0) if seg else 1.0
            return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], bearing(a, b)
        s -= seg


def path_len(path):
    return sum(dist(a, b) for a, b in zip(path, path[1:]))


def _slice(path, s0, s1):
    """The part of a polyline between distances s0 < s1 from its start."""
    out, acc = [point_at(path, s0)[0]], 0.0
    for a, b in zip(path, path[1:]):
        acc += dist(a, b)
        if s0 < acc < s1:
            out.append(b)
    return out + [point_at(path, s1)[0]]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass
class Aircraft:
    callsign: str
    kind: str
    aircraft_type: str
    spawn_t: float
    legs: list  # resolved: {"phase", "duration", "path"} or, for holds, {"phase", "duration", "path": None, "at", "heading"}
    leg: int = 0
    elapsed: float = 0.0  # sim seconds into the current leg
    phase: str = ""
    lat: float = 0.0
    lon: float = 0.0
    heading: float = 0.0
    done: bool = False

    def __post_init__(self):
        self.phase = self.legs[0]["phase"]
        self._place()

    def _place(self):
        leg = self.legs[self.leg]
        if leg["path"] is None:
            p, self.heading = leg["at"], leg["heading"]
        else:
            p, self.heading = point_at(leg["path"], self.elapsed * SPEED_KT[leg["phase"]] * KT)
        self.lon, self.lat = p

    def report(self):
        speed = 0 if self.legs[self.leg]["path"] is None else SPEED_KT[self.phase]
        return {"lat": round(self.lat, 7), "lon": round(self.lon, 7), "heading": round(self.heading, 1),
                "phase": self.phase, "speed_kt": speed}

    def step(self, dt):
        """Advance dt sim seconds. Returns (seconds into the step, report) for every phase change."""
        changes, used = [], 0.0
        while not self.done:
            left = self.legs[self.leg]["duration"] - self.elapsed
            if left > dt - used:
                self.elapsed += dt - used
                break
            used += left
            if self.leg + 1 < len(self.legs):
                self.leg, self.elapsed = self.leg + 1, 0.0
                self.phase = self.legs[self.leg]["phase"]
            else:
                self.elapsed, self.done = self.legs[self.leg]["duration"], True
                self.phase = "departed" if self.phase == "departed" else "parked"
            self._place()
            changes.append((used, self.report()))
        self._place()
        return changes


def _resolve_legs(raw_legs, graph, cs):
    nodes, approaches = graph["nodes"], graph["approaches"]
    edges = {frozenset((e["from"], e["to"])) for e in graph["edges"]}
    if not isinstance(raw_legs, list) or not raw_legs:
        raise ScenarioError(f"{cs}: needs a non-empty list of legs")
    legs = []
    for i, leg in enumerate(raw_legs, 1):
        phase = leg.get("phase") if isinstance(leg, dict) else None
        if phase not in SPEED_KT:
            raise ScenarioError(f"{cs} leg {i}: unknown phase {phase!r}")
        where = f"{cs} leg {i} ({phase})"
        kinds = [k for k in LEG_KINDS if k in leg]
        if len(kinds) != 1:
            raise ScenarioError(f"{where}: needs exactly one of {', '.join(LEG_KINDS)}")
        r = {"phase": phase, "path": None}
        if "nodes" in leg:
            ids = leg["nodes"]
            if not isinstance(ids, list) or len(ids) < 2:
                raise ScenarioError(f"{where}: nodes needs at least 2 node ids")
            for n in ids:
                if n not in nodes:
                    raise ScenarioError(f"{where}: node {n!r} does not exist")
            for a, b in zip(ids, ids[1:]):
                if frozenset((a, b)) not in edges:
                    raise ScenarioError(f"{where}: no graph edge between {a!r} and {b!r}")
            r["path"] = [[nodes[n]["lon"], nodes[n]["lat"]] for n in ids]
        elif "approach" in leg:
            if leg["approach"] not in approaches:
                raise ScenarioError(f"{where}: approach {leg['approach']!r} does not exist")
            path = approaches[leg["approach"]]["path"]
            total, a, b = path_len(path), leg.get("from_nm"), leg.get("to_nm")
            if not (_num(a) and _num(b) and total / NM >= a > b >= 0):
                raise ScenarioError(f"{where}: needs {total / NM:.2f} >= from_nm > to_nm >= 0")
            r["path"] = _slice(path, total - a * NM, total - b * NM)
        elif "hold_s" in leg:
            if not (_num(leg["hold_s"]) and leg["hold_s"] >= 0):
                raise ScenarioError(f"{where}: hold_s must be a number >= 0")
            r["duration"] = leg["hold_s"]
        else:
            if not (_num(leg["extend_m"]) and leg["extend_m"] > 0):
                raise ScenarioError(f"{where}: extend_m must be a number > 0")
            r["extend_m"] = leg["extend_m"]
        if r["path"] is not None and not SPEED_KT[phase]:
            raise ScenarioError(f"{where}: phase {phase!r} cannot move; use hold_s")
        legs.append(r)

    # Second pass: place holds and extensions, and check that moving legs join up.
    end = hdg = None
    for i, r in enumerate(legs):
        where = f"{cs} leg {i + 1} ({r['phase']})"
        if "duration" in r:  # hold
            if end is None:  # initial wait: at the start of the first moving leg
                nxt = next((n for n in legs[i + 1:] if n["path"] is not None), None)
                if nxt is None:
                    raise ScenarioError(f"{where}: a hold needs a moving leg before or after it")
                r["at"], r["heading"] = nxt["path"][0], point_at(nxt["path"], 0)[1]
            else:
                r["at"], r["heading"] = end, hdg
            continue
        if "extend_m" in r:
            if end is None:
                raise ScenarioError(f"{where}: extend_m needs a moving leg before it")
            r["path"] = [end, offset(end, hdg, r.pop("extend_m"))]
        elif end is not None and dist(end, r["path"][0]) > 1:
            raise ScenarioError(f"{where}: does not start where the previous leg ended")
        length = path_len(r["path"])
        r["duration"] = length / (SPEED_KT[r["phase"]] * KT)
        end, hdg = r["path"][-1], point_at(r["path"], length)[1]
    return legs


def build(scenario, graph):
    """Validate a scenario against the airport graph and return its aircraft (not yet spawned)."""
    for key in ("name", "runway", "aircraft"):
        if key not in scenario:
            raise ScenarioError(f"scenario: missing required key {key!r}")
    if scenario["runway"] not in graph["approaches"]:
        raise ScenarioError(f"scenario: runway {scenario['runway']!r} has no approach in graph.json")
    if not isinstance(scenario["aircraft"], list) or not scenario["aircraft"]:
        raise ScenarioError("scenario: aircraft must be a non-empty list")
    out, seen = [], set()
    for i, ac in enumerate(scenario["aircraft"], 1):
        missing = [k for k in ("callsign", "kind", "aircraft_type", "spawn_t", "legs") if k not in ac]
        if missing:
            raise ScenarioError(f"scenario aircraft #{i}: missing required key(s) {missing}")
        cs = ac["callsign"]
        if cs in seen:
            raise ScenarioError(f"{cs}: duplicate callsign")
        seen.add(cs)
        if ac["kind"] not in ("arrival", "departure"):
            raise ScenarioError(f"{cs}: kind must be arrival or departure")
        if not (_num(ac["spawn_t"]) and ac["spawn_t"] >= 0):
            raise ScenarioError(f"{cs}: spawn_t must be a number >= 0")
        out.append(Aircraft(cs, ac["kind"], ac["aircraft_type"], ac["spawn_t"], _resolve_legs(ac["legs"], graph, cs)))
    return out
