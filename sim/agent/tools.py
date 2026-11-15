"""Tool surface for the Claude agent: Anthropic tool definitions (JSON schema) and a dispatcher bound to a Simulation.

The tools validate the SHAPE of a call (known callsign, runway end, graph nodes, field types) and nothing else. They never
refuse a clearance because it is unsafe; conflicts are logged by the simulator for scoring only.
Results are compact JSON-able dicts (rounded numbers, no geometry) to keep token counts small.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import csv
from functools import lru_cache

from sim.core.aircraft_types import CSV_PATH, get_type, lookup
from sim.core.engine import ACTIONS, Simulation

# -- wake spacing (JO 7110.65BB; data-catalog 3.12 and research_notes aircraft_and_rules.md) -------------------------
CWT_ORDER = "BCDEFGHI"
# TBL 5-5-4 (on approach) distance minima in NM, followers B..I. None = no wake minimum (radar minimum applies).
WAKE_NM: Dict[str, List[Optional[float]]] = {
    "A": [5, 6, 6, 7, 7, 7, 8, 8],
    "B": [3, 4, 4, 5, 5, 5, 5, 6],
    "C": [None, None, None, 3.5, 3.5, 3.5, 5, 6],
    "D": [3, 4, 4, 5, 5, 5, 6, 6],
    "E": [None, None, None, None, None, None, None, 4],
    "F": [None, None, None, None, None, None, None, 4],
}
SRS_DEP_FT = {("I", "I"): 3000, ("I", "II"): 3000, ("II", "I"): 4500, ("II", "II"): 4500}


def departure_interval_s(leader_cwt: str, follower_cwt: str) -> int:
    """WAKE-DEP-SAME (3-9-6 f, g): A leader 3 min; B or D leader 2 min; C leader 2 min to E..I; I behind E 2 min."""
    if leader_cwt == "A" and follower_cwt != "A":
        return 180
    if leader_cwt in ("B", "D") and follower_cwt in CWT_ORDER:
        return 120
    if leader_cwt == "C" and follower_cwt in "EFGHI":
        return 120
    if leader_cwt == "E" and follower_cwt == "I":
        return 120
    return 0


@lru_cache(maxsize=1)
def _srs_table() -> Dict[str, str]:
    with open(CSV_PATH, newline="") as f:
        return {r["icao_type"]: r["faa_srs"] for r in csv.DictReader(f)}


def wake_spacing(leader_type: str, follower_type: str) -> Dict[str, Any]:
    lt, ft = lookup(leader_type), lookup(follower_type)
    if lt is None or ft is None:
        bad = leader_type if lt is None else follower_type
        return {"ok": False, "error": "unknown aircraft type %r; known types come from the KPHX aircraft table (e.g. B738, A320, E75L, C172)" % bad}
    row = WAKE_NM.get(lt.cwt)
    nm = row[CWT_ORDER.index(ft.cwt)] if row and ft.cwt in CWT_ORDER else None
    l_srs, f_srs = _srs_table().get(lt.icao, "III"), _srs_table().get(ft.icao, "III")
    out: Dict[str, Any] = {
        "ok": True, "leader": {"type": lt.icao, "cwt": lt.cwt, "srs": l_srs}, "follower": {"type": ft.icao, "cwt": ft.cwt, "srs": f_srs},
        "wake_distance_nm": nm,
        "departure_interval_s": departure_interval_s(lt.cwt, ft.cwt),
        "opposite_direction_or_intersection_interval_s": 240 if lt.cwt == "A" else 180 if departure_interval_s(lt.cwt, ft.cwt) else 0,
        "rules": ["JO 7110.65BB 5-5-4 (distance)", "3-9-6 (departure intervals, measured start of roll to start of roll)"],
    }
    out["srs_departure_ft"] = 6000 if "III" in (l_srs, f_srs) else SRS_DEP_FT.get((l_srs, f_srs), 3000)
    if lt.cwt in "ABD" and ft.cwt in "HI":
        out["note"] = "no line up and wait for a small aircraft behind a departing A-D (3-9-6 note)"
    return out


# -- tool definitions ---------------------------------------------------------------------------------------------------
def _obj(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_CS = {"type": "string", "description": "Aircraft callsign exactly as shown, e.g. SWA1234"}
TOOL_DEFS: List[Dict[str, Any]] = [
    {"name": "get_airport_state", "description": "Current picture: time, weather summary, runway config, runway status, every aircraft that needs attention "
     "(phase, runway, eta, hold state), active clearances and unresolved pilot readbacks. The same picture is already in your wake message; "
     "call this only to refresh after acting.", "input_schema": _obj({}, [])},
    {"name": "get_aircraft", "description": "One aircraft: type, wake category, phase, gate, runway, eta, hold state, its clearances and last radio lines.",
     "input_schema": _obj({"callsign": _CS}, ["callsign"])},
    {"name": "get_route_options", "description": "Taxi route options for an aircraft from where it is now to a destination: a gate name (e.g. B12), "
     "a runway end (e.g. 25R, departure entry) or a graph node id. Returns the shortest route and the fewest-runway-crossings route as taxiway letters, "
     "length in metres, runway crossings and estimated taxi time.",
     "input_schema": _obj({"callsign": _CS, "destination": {"type": "string", "description": "Gate, runway end or graph node id"}}, ["callsign", "destination"])},
    {"name": "get_wake_spacing", "description": "Wake and separation minima between a leader and a follower aircraft type (ICAO type codes) from the KPHX "
     "aircraft table categories: distance in NM, departure interval in seconds, SRS distance in feet.",
     "input_schema": _obj({"leader_type": {"type": "string"}, "follower_type": {"type": "string"}}, ["leader_type", "follower_type"])},
    {"name": "get_weather", "description": "Latest weather: METAR text, wind, gust, visibility, weather phenomena, haboob progress.", "input_schema": _obj({}, [])},
    {"name": "get_runway_status", "description": "Each runway pavement: free/reserved/occupied, owner, recent occupancy and the next reserved windows (sim seconds), plus the runway configuration.",
     "input_schema": _obj({}, [])},
    {"name": "issue_clearance", "description": "Issue a clearance or instruction to an aircraft. It is spoken, the pilot reads it back after 1.5 to 3 s, and it takes effect "
     "when the readback is accepted. params by action: taxi_via {to: gate|runway end|node, via?: [taxiway letters], entry?: node}; "
     "hold_short/cross_runway/line_up_and_wait/cleared_for_takeoff/cleared_to_land {runway: runway end like 25R, exit?: node id for landing, at_taxiway?: for cross_runway}; "
     "contact_tower_or_ground {facility: tower|ground, frequency: '118.7', runway?: end}; go_around, hold_position, resume_taxi, pushback_approved take no params. "
     "Superseding clearances replace unfinished ones of the same kind.",
     "input_schema": _obj({"callsign": _CS, "action": {"type": "string", "enum": list(ACTIONS)},
                           "params": {"type": "object", "description": "Action parameters, see tool description"}}, ["callsign", "action"])},
    {"name": "speak", "description": "Transmit free text to an aircraft (advisories, traffic, wake caution, confirmations). Use FAA phraseology.",
     "input_schema": _obj({"callsign": _CS, "text": {"type": "string"}}, ["callsign", "text"])},
    {"name": "resolve_readback", "description": "Verdict on an unresolved pilot readback listed in pending_readbacks: accept=true if it matches what you issued, "
     "accept=false to say again (the pilot then reads back correctly).",
     "input_schema": _obj({"readback_id": {"type": "string"}, "accept": {"type": "boolean"}}, ["readback_id", "accept"])},
    {"name": "set_runway_config", "description": "Set the active runway ends for arrivals and departures, e.g. arrival_runways [25L, 26], departure_runways [25R, 26]. "
     "Known ends: 07L 07R 08 25L 25R 26.",
     "input_schema": _obj({"arrival_runways": {"type": "array", "items": {"type": "string"}},
                           "departure_runways": {"type": "array", "items": {"type": "string"}}}, ["arrival_runways", "departure_runways"])},
    {"name": "record_decision", "description": "Add an entry to the tower operations log for the action you just took (clearance, runway config, readback rejection). "
     "The dashboard displays it next to the aircraft. Give the observed facts behind the action, the procedure references that apply, "
     "other options that were available with a short note on each, and in how many sim seconds to check again.",
     # Wording note: phrasing this as \"why / what you weighed / what you passed over\" trips the API's reasoning_extraction classifier.
     "input_schema": _obj({"callsign": {"type": ["string", "null"], "description": "Aircraft concerned, null for airport-wide decisions"},
                           "action": {"type": "string", "description": "Short label, e.g. cleared_to_land, hold_short, weather_replan"},
                           "summary": {"type": "string", "description": "One sentence describing the action"},
                           "facts": {"type": "array", "items": {"type": "string"}, "description": "Observed facts from the airport picture, with numbers"},
                           "rules": {"type": "array", "items": {"type": "string"}, "description": "Applicable procedure references, e.g. JO 7110.65BB paragraph numbers"},
                           "alternatives": {"type": "array", "description": "Other available options, each with a short note", "items": _obj({"option": {"type": "string"}, "reason": {"type": "string"}}, ["option", "reason"])},
                           "next_review_s": {"type": "number"}},
                          ["callsign", "action", "summary", "facts", "rules", "alternatives", "next_review_s"])},
]
TOOL_NAMES = [t["name"] for t in TOOL_DEFS]
ACTION_TOOLS = ("issue_clearance", "speak", "resolve_readback", "set_runway_config", "record_decision")


def err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": msg}


def _r(v: Any, n: int = 0) -> Any:
    return round(v, n) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


# -- compact views ----------------------------------------------------------------------------------------------------------
def compact_aircraft(a: Dict[str, Any]) -> Dict[str, Any]:
    """Observation view of one aircraft reduced to what a controller needs."""
    ty = get_type(a["type"])
    out: Dict[str, Any] = {"cs": a["callsign"], "type": a["type"], "cwt": ty.cwt, "kind": a["kind"], "phase": a["phase"]}
    if a.get("gate"):
        out["gate"] = a["gate"]
    rwy = a.get("arrival_end") or a.get("dep_end")
    if rwy:
        out["rwy"] = rwy
    for key, src in (("eta_s", "eta_s"), ("stopped_s", "stopped_s")):
        if a.get(src):
            out[key] = _r(a[src])
    if a["kind"] == "arrival" and a["phase"] in ("final", "go_around"):
        out["landing_cleared"] = bool(a.get("landing_cleared"))
    if a["kind"] == "departure":
        if a["phase"] == "at_gate":
            out["ready"] = bool(a.get("ready"))
        if a["phase"] == "line_up":
            out["takeoff_cleared"] = bool(a.get("takeoff_cleared"))
    if a.get("hold"):
        out["holding_short"] = a["hold"]["runway"]
    if a.get("blocked_by"):
        out["blocked_by"] = a["blocked_by"]
    if a.get("frequency") and a["kind"] == "arrival":
        out["freq"] = a["frequency"]
    out["status"] = a.get("status_text", "")
    return out


def compact_runways(obs: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    t = obs["t"]
    for r in obs["runways"]:
        item: Dict[str, Any] = {"id": r["id"], "status": r["status"], "owner": r["owner"]}
        nxt = [{"cs": w["callsign"], "from_s": _r(w["start_s"]), "to_s": _r(w["end_s"]), "kind": w["kind"]}
               for w in r.get("occupancy", []) if w["end_s"] >= t - 20]
        if nxt:
            item["windows"] = nxt
        out.append(item)
    return out


def compact_weather(w: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("metar", "wind_dir", "wind_kt", "gust_kt", "vis_sm", "wx", "active", "haboob_progress")
    return {k: w[k] for k in keep if k in w and w[k] is not None}


def compact_state(sim: Simulation, obs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    obs = obs or sim.observe()
    aircraft = [compact_aircraft(a) for a in obs["aircraft"]
                if a["kind"] != "vehicle" and a["phase"] not in ("airborne", "parked") and not a.get("done")]
    vehicles = [{"id": a["callsign"], "status": a.get("status_text", "")} for a in obs["aircraft"] if a["kind"] == "vehicle"]
    clr = [{"id": c["id"], "cs": c["callsign"], "action": c["action"], "state": c["state"], "age_s": _r(obs["t"] - c["issued_t"]), "text": c["text"]}
           for c in obs["clearances"]]
    out: Dict[str, Any] = {
        "t": _r(obs["t"], 1), "data_age_s": _r(obs["data_age_s"], 1), "weather": compact_weather(obs["weather"]),
        "runway_config": obs["runway_config"], "runways": compact_runways(obs), "aircraft": aircraft,
        "active_clearances": clr,
        "pending_readbacks": [{"id": p["id"], "cs": p["callsign"], "clearance_id": p["clearance_id"], "heard": p["text"],
                               "heard_runway": p["parsed"].get("runway"),
                               "issued": next((c["text"] for c in obs["clearances"] if c["id"] == p["clearance_id"]), None)}
                              for p in obs["pending_readbacks"]]}
    if vehicles:
        out["vehicles"] = vehicles
    return out


# -- the dispatcher ---------------------------------------------------------------------------------------------------------------
class ToolBox:
    """Runs tool calls against one Simulation. Call from the thread that steps the simulation."""

    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.last_voice: Dict[Optional[str], str] = {}

    def call(self, name: str, args: Any) -> Dict[str, Any]:
        fn: Optional[Callable[..., Dict[str, Any]]] = getattr(self, "t_" + name, None) if name in TOOL_NAMES else None
        if fn is None:
            return err("unknown tool %r; tools: %s" % (name, ", ".join(TOOL_NAMES)))
        if not isinstance(args, dict):
            return err("tool input must be an object")
        schema = next(t for t in TOOL_DEFS if t["name"] == name)["input_schema"]
        missing = [k for k in schema["required"] if k not in args]
        if missing:
            return err("missing required field(s): %s" % ", ".join(missing))
        extra = [k for k in args if k not in schema["properties"]]
        if extra:
            return err("unexpected field(s): %s" % ", ".join(extra))
        try:
            return fn(**args)
        except (TypeError, ValueError, KeyError) as e:   # malformed arguments, never a simulation refusal
            return err("bad arguments: %s" % e)

    # -- information -----------------------------------------------------------------------------------------------------------
    def t_get_airport_state(self) -> Dict[str, Any]:
        return compact_state(self.sim)

    def _find(self, callsign: str) -> Optional[Dict[str, Any]]:
        return next((a for a in self.sim.observe()["aircraft"] if a["callsign"] == callsign), None)

    def t_get_aircraft(self, callsign: str) -> Dict[str, Any]:
        sim = self.sim
        ac = sim.aircraft.get(callsign)
        if ac is None or ac.removed:
            return err("unknown callsign %r" % callsign)
        seen = self._find(callsign)
        out = compact_aircraft(seen) if seen else {"cs": callsign, "type": ac.type.icao, "note": "not in the delayed radar picture yet"}
        out["wingspan_m"] = ac.wingspan_m
        out["clearances"] = [{"id": c.id, "action": c.action, "state": c.state, "text": c.text, "issued_t": c.issued_t}
                             for c in sim.clearances.values() if c.callsign == callsign][-4:]
        out["radio"] = [{"who": v.speaker, "text": v.text, "status": v.status} for v in sim.voice if v.callsign == callsign][-4:]
        return out

    def _destination(self, ac: Any, dest: str) -> Any:
        sim = self.sim
        origin, _ = sim._route_origin(ac)
        if origin is None:
            return None, None, "aircraft %s has no ground position to route from" % ac.callsign
        if dest in sim.graph.stands:
            return origin, sim.graph.stands[dest].node, None
        if dest in sim.graph.end_runway:
            entry = sim.choose_entry(dest, origin, ac.type)
            return (origin, entry, None) if entry else (None, None, "no usable runway entry for %s" % dest)
        if dest in sim.graph.node_xy:
            return origin, dest, None
        return None, None, "unknown destination %r: use a gate name, runway end or graph node id" % dest

    def t_get_route_options(self, callsign: str, destination: str) -> Dict[str, Any]:
        sim = self.sim
        ac = sim.aircraft.get(callsign)
        if ac is None or ac.removed or ac.kind == "vehicle":
            return err("unknown callsign %r" % callsign)
        origin, dst, e = self._destination(ac, destination)
        if e:
            return err(e)
        options = []
        seen = set()
        for label, penalty in (("shortest", 250.0), ("fewest_crossings", 6000.0)):
            nodes = sim.graph.route(origin, dst, cross_penalty=penalty)
            if not nodes or tuple(nodes) in seen:
                continue
            seen.add(tuple(nodes))
            path = sim.graph.build_path(nodes)
            options.append({"option": label, "via": sim.graph.taxiways_on(nodes), "length_m": _r(sim.graph.route_length(nodes)),
                            "runway_crossings": [sim.runway_label(r.runway) for r in path.runs],
                            "est_taxi_s": _r(path.length / 4.5)})
        if not options:
            return err("no taxi route from %s to %s" % (callsign, destination))
        return {"ok": True, "callsign": callsign, "destination": destination, "options": options,
                "hint": "issue_clearance taxi_via {to: destination, via: [letters]} to use one"}

    def t_get_wake_spacing(self, leader_type: str, follower_type: str) -> Dict[str, Any]:
        return wake_spacing(str(leader_type), str(follower_type))

    def t_get_weather(self) -> Dict[str, Any]:
        w = compact_weather(self.sim.weather_view())
        w["age_s"] = _r(self.sim.t - self.sim.weather_view().get("t", 0.0), 1)
        return w

    def t_get_runway_status(self) -> Dict[str, Any]:
        obs = self.sim.observe()
        return {"t": _r(obs["t"], 1), "runway_config": obs["runway_config"], "runways": compact_runways(obs)}

    # -- actions ------------------------------------------------------------------------------------------------------------------
    def t_issue_clearance(self, callsign: str, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if params is not None and not isinstance(params, dict):
            return err("params must be an object")
        res = self.sim.issue_clearance(callsign, action, params)
        if not res.get("ok"):
            return res
        self.last_voice[callsign] = res["voice_id"]
        return {"ok": True, "clearance_id": res["clearance_id"], "text": res["text"]}

    def t_speak(self, callsign: str, text: str) -> Dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            return err("text must be a non-empty string")
        res = self.sim.speak(callsign, text.strip()[:300])
        if res.get("ok"):
            self.last_voice[callsign] = res["voice_id"]
        return res

    def t_resolve_readback(self, readback_id: str, accept: bool) -> Dict[str, Any]:
        if not isinstance(accept, bool):
            return err("accept must be true or false")
        return self.sim.resolve_readback(readback_id, accept)

    def t_set_runway_config(self, arrival_runways: List[str], departure_runways: List[str]) -> Dict[str, Any]:
        if not isinstance(arrival_runways, list) or not isinstance(departure_runways, list):
            return err("arrival_runways and departure_runways must be lists of runway ends")
        return self.sim.set_runway_config([str(x) for x in arrival_runways], [str(x) for x in departure_runways])

    def t_record_decision(self, callsign: Optional[str], action: str, summary: str, facts: List[str], rules: List[str],
                          alternatives: List[Dict[str, str]], next_review_s: float) -> Dict[str, Any]:
        sim = self.sim
        if callsign is not None and callsign not in sim.aircraft:
            return err("unknown callsign %r" % callsign)
        if not isinstance(summary, str) or not summary.strip() or not isinstance(action, str) or not action.strip():
            return err("action and summary must be non-empty strings")
        for name, val in (("facts", facts), ("rules", rules), ("alternatives", alternatives)):
            if not isinstance(val, list):
                return err("%s must be a list" % name)
        alts = []
        for a in alternatives:
            if not isinstance(a, dict) or "option" not in a or "reason" not in a:
                return err("each alternative needs option and reason")
            alts.append({"option": str(a["option"]), "reason": str(a["reason"])})
        if isinstance(next_review_s, bool) or not isinstance(next_review_s, (int, float)):
            return err("next_review_s must be a number of sim seconds")
        return sim.record_decision(callsign=callsign, action=action.strip(), summary=summary.strip(), facts=[str(f) for f in facts],
                                   rules=[str(r) for r in rules], alternatives=alts, next_review_s=next_review_s,
                                   voice_id=self.last_voice.get(callsign))


def dispatch(sim: Simulation, name: str, args: Any) -> Dict[str, Any]:
    """One-shot convenience: run a single tool call against a simulation."""
    return ToolBox(sim).call(name, args)
