"""The airport's built-in rule-based tower: first-come-first-served sequencing through the Simulation tools."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

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
PUSHBACK_CLEAR_M = 90.0
FLOW_SWITCH_TAILWIND_KT = 5.0   # turn the airport around once the active direction has more tailwind than this
OPPOSING_PENALTY_M = 2000.0     # taxi routing: an edge another cleared route uses the other way counts this much longer
CROSSING_PENALTY_M = 2500.0     # taxi routing: per runway-pavement edge, so a route never trades a crossing for a detour
FLOW_FINISH_ETA_S = 150.0       # on a flow change, arrivals closer than this finish on the old direction (if still legal)
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
    """Time between takeoffs on one runway (placeholder for the wake spacing table, see docs/ASSUMPTIONS.md)."""
    if leader_wtc.startswith("H"):
        return 120.0
    if follower_wtc.startswith("L") and leader_wtc.startswith("M"):
        return 90.0
    return 60.0


class TowerController:
    name = "tower"
    status = "idle"

    def __init__(self) -> None:
        self.assigned: Dict[str, Dict[str, Any]] = {}
        self.order: Dict[str, int] = {}                   # first-come-first-served order of first sighting
        self.expected: Dict[str, Optional[str]] = {}      # clearance_id -> runway end the readback must contain
        self.last_arrival_slot: Dict[str, float] = {}     # runway pavement -> last landing slot time
        self.last_airborne: Dict[str, Tuple[float, str]] = {}   # pavement -> (t, wake class)
        self.claims: Dict[Tuple[str, str], str] = {}      # (pavement, callsign) -> clearance id that holds the runway
        self.routes: Dict[str, List[str]] = {}            # callsign -> taxi route nodes this tower cleared (until done)

    # -- entry point -----------------------------------------------------------------------------------
    def on_tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        for ev in events:
            if ev["kind"] == "aircraft.airborne":
                cs = ev["entity_id"]
                self.last_airborne[ev["data"]["runway"]] = (ev["sim_time_ms"] / 1000.0, get_type(self._type_of(sim, cs)).wtc)
                self.assigned.setdefault(cs, {})["airborne"] = True
            if ev["kind"] in ("aircraft.airborne", "aircraft.parked"):
                self.routes.pop(ev["entity_id"], None)
            if ev["kind"] == "pilot.unable":       # refused for tailwind: forget the clearance so it is planned again
                st = self.assigned.setdefault(ev["entity_id"], {})
                for k in {"cleared_to_land": ("landing",), "line_up_and_wait": ("luaw",),
                          "cleared_for_takeoff": ("to",)}.get(ev["data"]["action"], ()):
                    st.pop(k, None)
        obs: Optional[Dict[str, Any]] = None
        if any(e["kind"] == "readback" for e in events) or sim.clock.tick % DECIDE_EVERY_TICKS == 0:
            obs = sim.observe()
        if obs is None:
            return
        live = {c["id"] for c in obs["clearances"]}
        self.claims = {k: cid for k, cid in self.claims.items() if cid in live}
        self._readbacks(sim, obs)
        if sim.clock.tick % DECIDE_EVERY_TICKS == 0:
            self._flow(sim, obs)
            by_cs = {a["callsign"]: a for a in obs["aircraft"] if a["kind"] != "vehicle"}
            for cs in sorted((c for c in by_cs if c not in self.order), key=lambda c: (by_cs[c]["eta_s"] or 0.0, c)):
                self.order[cs] = len(self.order)
            for cs in sorted(by_cs, key=lambda c: self.order[c]):
                a = by_cs[cs]
                if a["kind"] == "arrival":
                    self._arrival(sim, obs, a)
                else:
                    self._departure(sim, obs, a)

    @staticmethod
    def _type_of(sim: Simulation, callsign: str) -> str:
        return sim.aircraft[callsign].type.icao

    # -- helpers ------------------------------------------------------------------------------------------
    def _issue(self, sim: Simulation, a: Dict[str, Any], action: str, params: Dict[str, Any], summary: str, facts: List[str],
               rules: List[str], alternatives: List[Dict[str, str]], review_s: float) -> Optional[Dict[str, Any]]:
        res = sim.issue_clearance(a["callsign"], action, params)
        if not res["ok"]:
            sim.record_decision(callsign=a["callsign"], action=action, summary="Could not issue %s: %s" % (action, res["error"]),
                                facts=facts, rules=rules, alternatives=alternatives, next_review_s=review_s)
            return None
        clr = sim.clearances[res["clearance_id"]]
        self.expected[clr.id] = clr.runway
        if action == "taxi_via":
            self.routes[a["callsign"]] = list(params.get("route") or [])
        pav = sim.runway_pavement(clr.runway) if action in ("cleared_to_land", "cross_runway", "line_up_and_wait", "cleared_for_takeoff") else None
        if pav:
            self.claims[(pav, a["callsign"])] = clr.id
        sim.record_decision(callsign=a["callsign"], action=action, summary=summary, facts=facts, rules=rules,
                            alternatives=alternatives, next_review_s=review_s, voice_id=res["voice_id"])
        return res

    def _taxi_params(self, sim: Simulation, a: Dict[str, Any], to: str, dst: Optional[str],
                     origin: Optional[str] = None) -> Dict[str, Any]:
        """An explicit route that avoids taxiing head-on into a route already cleared the other way (ground control's
        one-way flows), or the simulator's shortest route if there is no usable node to start from."""
        import networkx as nx
        origin = origin or a.get("next_node")
        if not origin or not dst:
            return {"to": to}
        g = sim.graph
        against = {(v, u) for cs, nodes in self.routes.items() if cs != a["callsign"] for u, v in zip(nodes, nodes[1:])}

        def weight(u: str, v: str, d: Dict[str, Any]) -> Optional[float]:
            e = g.edges[d["eid"]]
            if e.kind == "runway":
                return None
            return e.length + (CROSSING_PENALTY_M if e.pavement else 0.0) + (OPPOSING_PENALTY_M if (u, v) in against else 0.0)
        try:
            return {"route": nx.dijkstra_path(g.g, origin, dst, weight=weight), "to": to}   # one-way search: (u, v) is the direction
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"to": to}

    @staticmethod
    def _runway(obs: Dict[str, Any], pav: str) -> Dict[str, Any]:
        return next(r for r in obs["runways"] if r["id"] == pav)

    def _blockers(self, obs: Dict[str, Any], pav: str, me: str, t0: float, t1: float) -> List[str]:
        """Who occupies or has reserved this pavement during [t0, t1] (absolute sim seconds)."""
        out = ["%s cleared" % cs for (p, cs) in self.claims if p == pav and cs != me]
        for w in self._runway(obs, pav)["occupancy"]:
            if w["callsign"] == me:
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

    # -- runway flow (wind) -----------------------------------------------------------------------------------
    @staticmethod
    def _tailwind(sim: Simulation, obs: Dict[str, Any], end: str) -> float:
        return tailwind_kt(obs["weather"], sim.graph.end_info(end)[1]["heading_deg"])

    def _flow(self, sim: Simulation, obs: Dict[str, Any]) -> None:
        """Turn the airport around (every runway to its other end) when the wind gives the active direction a tailwind."""
        cfg = obs["runway_config"]
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
            st["taxi"] = True
            self._issue(sim, a, "taxi_via", self._taxi_params(sim, a, a["gate"], sim.graph.stands[a["gate"]].node),
                        "Taxi %s to gate %s by the shortest route" % (cs, a["gate"]),
                        ["%s vacated the runway" % cs, "Gate %s assigned in the schedule" % a["gate"]], [RULES["taxi"]],
                        [{"option": "longer route avoiding crossings", "reason": "shortest route with a crossing penalty already chosen"}], 20)
        if a["phase"] in ("taxi_in",) and a.get("hold") and a["speed_mps"] < 0.3 and a.get("stopped_s", 0) > 0.8:
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
        st["exit"] = sim.choose_exit(end, ty, stand.node)
        freq = "118.7" if pav == "08/26" else "120.9"
        self._issue(sim, a, "contact_tower_or_ground", {"facility": "tower", "frequency": freq, "runway": end},
                    "Assigned %s to runway %s" % (a["callsign"], end),
                    ["%s is %.0f s from the threshold" % (a["callsign"], eta)] + list(notes.values()),
                    ["First come, first served on each landing runway"],
                    [{"option": "runway %s" % e, "reason": n} for e, n in notes.items() if e != end], 10)

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
        blockers = self._blockers(obs, pav, a["callsign"], t, t + CROSS_WINDOW_S)
        due = self._landing_due(obs, pav, CROSS_GUARD_S, a["callsign"])
        key = "cross-%s" % pav
        if self.assigned.setdefault(a["callsign"], {}).get(key) and not blockers and not due:
            return
        if blockers or due:
            if not self.assigned[a["callsign"]].get("held-" + pav):
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
            st["taxi"] = True
            self._issue(sim, a, "taxi_via", self._taxi_params(sim, a, st["end"], st["entry"]),
                        "Taxi %s to runway %s via %s" % (cs, st["end"], ", ".join(st["via"]) or "shortest route"),
                        ["%s pushed back from gate %s" % (cs, a["gate"]), "Runway %s chosen: %s" % (st["end"], st["why"])],
                        [RULES["taxi"]], st["alts"], 20)
            return
        if ph == "hold_short" and a.get("hold") and "taxi" in st:
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
        lane = sim.graph.node_xy[sim.graph.stands[a["gate"]].lane_node]
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
        if blockers or due or not self._spacing_ok(sim, st, a, t):
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
