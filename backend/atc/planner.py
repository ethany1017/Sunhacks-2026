"""Deterministic planner: the built-in tower's procedures (sim/tower/controller.py, Gus's rule-based tower), ported to
run behind our safety engine and through the simif adapter.

The decision logic is the tower's, with its constants, decision order and ground probes. What differs:
- it reads only simif views: get_state() (observe()), get_readbacks(), the static layout (si.layout(), runway_ends(),
  type_info(), choose_entry/choose_exit, runway_label/runway_pavement); never the simulator itself;
- what the tower learnt from raw sim events (takeoff roll, airborne, parked, a pilot's "unable") comes from the
  observed phases and the clearance list;
- every instruction is a proposal -> safety.check -> simif.issue -> clearance event; a rejection is logged and the
  planner tries again on a later pass (it waits), never around the engine;
- key decisions (runway flow, arrival runway, which departure takes the runway) are options + a default; `choose`
  picks one (A4 plugs Claude in there; here it always takes the default).
"""
import math
import re

import networkx as nx
import numpy as np

from . import safety

LAND_CLEAR_ETA_S = 70.0         # clear to land inside this ETA when the runway is free
GO_AROUND_ETA_S = 22.0          # no clearance and runway still blocked this close in: go around
ARRIVAL_SLOT_S = 100.0          # minimum spacing between landings on one runway (sim separation is 2.5 nm)
LINE_UP_GUARD_S = 100.0         # no line-up if a landing is due within this
TAKEOFF_GUARD_S = 55.0          # no takeoff roll if a landing is due within this
CROSS_GUARD_S = 90.0            # no crossing if a landing on that runway is due within this
CROSS_WINDOW_S = 45.0
FAR_SIDE_M = 150.0              # no crossing while an aircraft stands on the first this-many metres after the runway
PUSHBACK_CLEAR_M = 90.0
PUSH_DELAY_MAX_S = 200.0        # ... unless the departure has waited at the gate this long
FLOW_SWITCH_TAILWIND_KT = 5.0   # turn the airport around once the active direction has more tailwind than this
OPPOSING_PENALTY_M = 2000.0     # taxi routing: an edge another cleared route uses the other way counts this much longer
CROSSING_PENALTY_M = 2500.0     # taxi routing: per runway-pavement edge, so a route never trades a crossing for a detour
REPEAT_CROSSING_PENALTY_M = 20000.0   # taxi routing: per edge of a runway the route would otherwise use twice
PROBE_S = 20.0                  # ground conflict probe: look this far ahead along each cleared taxi route
STOP_CLEAR_S = 60.0             # a held aircraft must stop clear of the other's route this far ahead
PROBE_MARGIN_M = 5.0            # margin the probe keeps between two taxiing aircraft beyond their half diagonals
RELEASE_GRACE_S = 10.0          # a just-released aircraft counts as moving (it is about to) for this long
HOLD_MAX_S = 90.0               # a probe hold is released after this long even if the traffic is still there
RESUME_TAXI_SPEED = 7.7         # m/s a held aircraft is assumed to reach (accelerating from rest) when it may resume
TAXI_ACCEL = 0.9                # m/s^2 a taxiing aircraft is assumed to pick up speed at
RELEASE_EXTRA_M = 15.0          # a hold is only released with this much more room than the margin
EXIT_SPEED_MPS = 10.0           # speed at the runway exit (the simulator's pilots: sim/core/physics.py EXIT_SPEED)
EXIT_TAXI_WEIGHT = 0.1          # exit choice: one second of runway occupancy is worth ten seconds of taxi to the gate
UNJAM_AFTER_S = 15.0            # two aircraft waiting for each other this long: reroute one of them
UNJAM_RETRY_S = 45.0            # and do not try again for the same aircraft sooner than this
PUSH_KEEP_CLEAR_M = 60.0        # taxi routing: keep this far from an aircraft pushing back while another way exists
TAXI_WAIT_S = 60.0              # a new taxi clearance waits up to this long for a way that is not head-on into traffic
FLOW_FINISH_ETA_S = 150.0       # on a flow change, arrivals closer than this finish on the old direction (if still legal)
TAILWIND_LIMIT_KT = 10.0        # pilots' tailwind limit when the level does not say (sim/levels.py TAILWIND_LIMIT_KT)
ROLLOUT_S = 72.0                # typical landing occupancy, dry (docs/LEVELS.md measures 69-77 s), times occupancy_mult
CLAIMS = ("cleared_to_land", "cross_runway", "line_up_and_wait", "cleared_for_takeoff")   # clearances that hold a runway
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
FREQ = {"08/26": "118.7"}       # tower frequency per landing runway (the tower's: 120.9 otherwise)


def departure_gap_s(leader_wtc, follower_wtc):
    """Time from one takeoff roll to the next on one runway (JO 7110.65 5-8-3: 2 min behind a heavy; otherwise about one
    minute, which also keeps the Referee's 6,000 ft airborne gap)."""
    if leader_wtc.startswith("H"):
        return 120.0
    if follower_wtc.startswith("L") and leader_wtc.startswith("M"):
        return 90.0
    return 60.0


def tailwind_kt(weather, heading_deg):
    """Tailwind component of the reported wind on a runway heading; negative = headwind (as sim/levels.py)."""
    if not weather.get("wind_kt"):
        return 0.0
    return -float(weather["wind_kt"]) * math.cos(math.radians(float(weather.get("wind_dir") or 0) - heading_deg))


def _headings(f, h0):
    """Heading (rad) along a predicted track; where it does not move, the current heading."""
    d = np.diff(f, axis=0)
    h = np.arctan2(d[:, 0], d[:, 1])
    still = np.hypot(d[:, 0], d[:, 1]) < 0.2
    h = np.concatenate([h, h[-1:]]) if len(h) else np.array([math.radians(h0)])
    h[np.concatenate([still, still[-1:]]) if len(still) else [True]] = math.radians(h0)
    return h


def _boxes_meet(fa, a, fb, b, margin):
    """Per predicted second: do the two footprints (length x wingspan rectangles turned along their tracks, grown by
    `margin`) overlap? Separating-axis test, vectorised over time."""
    ha, hb = _headings(fa, a["heading_deg"]), _headings(fb, b["heading_deg"])
    axes = [np.stack([np.sin(h), np.cos(h)], -1) for h in (ha, hb)] + [np.stack([np.cos(h), -np.sin(h)], -1) for h in (ha, hb)]
    fwd_a, fwd_b, side_a, side_b = axes
    d = fb - fa
    apart = np.zeros(len(fa), dtype=bool)
    for n in axes:
        reach = ((a["length_m"] / 2 + margin) * np.abs((fwd_a * n).sum(-1)) + (a["wingspan_m"] / 2 + margin) * np.abs((side_a * n).sum(-1))
                 + (b["length_m"] / 2 + margin) * np.abs((fwd_b * n).sum(-1)) + (b["wingspan_m"] / 2 + margin) * np.abs((side_b * n).sum(-1)))
        apart |= np.abs((d * n).sum(-1)) > reach
    return ~apart


class Planner:
    def __init__(self, si, emit):
        self.si, self.emit = si, emit
        self.g = si.layout()
        self.ends = si.runway_ends()
        self.choose = lambda decision: decision["default"]   # A4: Claude picks among decision["options"]
        self.assigned = {}
        self.order = {}                 # first-come-first-served order of first sighting
        self.expected = {}              # clearance id -> (callsign, action, runway end the readback must contain or None)
        self.rejected = {}              # (callsign, action) -> rule of the last recorded rejection (one record per episode)
        self.last_arrival_slot = {}     # runway pavement -> last landing slot time
        self.last_airborne = {}         # pavement -> (t, wake class, callsign, cwt)
        self.claims = {}                # (pavement, callsign) -> clearance id that holds the runway
        self.claim_action = {}          # clearance id -> action
        self.routes = {}                # callsign -> taxi route nodes this planner cleared (until done)
        self.wtc = {}
        self.cross_ids = set()
        self._issued_now = set()
        self.ready_at = {}
        self.taxi_wait = {}
        self.unjammed = {}
        self.ground_hold = {}
        self.hold_since = {}
        self.released = {}
        self.phase = {}                 # callsign -> last observed phase
        self.watch = {}                 # clearance id -> (callsign, flag) forgotten if the pilot cancels it ("unable")
        self.pushbacks = {}             # callsign -> {xy, t, lane, stand_xy}: approved pushbacks (safety stagger/sweep)
        self.sequences = {}
        self._tracks = {}

    # -- entry point -----------------------------------------------------------------------------------------------
    def tick(self, obs):
        self.obs, self.t = obs, obs["t"]
        live = {c["id"]: c for c in obs["clearances"]}
        self.live = live
        by_cs = {a["callsign"]: a for a in obs["aircraft"] if a["kind"] != "vehicle"}
        for a in by_cs.values():
            a["x"], a["y"] = (float(v) for v in self.g.frame.to_xy(a["lon"], a["lat"]))
        self.ac = by_cs
        self._adopt(obs, by_cs)
        self._track(by_cs, live)
        self.claims = {k: cid for k, cid in self.claims.items() if cid in live}
        self.pic = self._picture(by_cs)
        self._readbacks()
        self._flow(obs)
        for cs in sorted((c for c in by_cs if c not in self.order), key=lambda c: (by_cs[c]["eta_s"] or 0.0, c)):
            self.order[cs] = len(self.order)
        for a in by_cs.values():
            self.wtc[a["callsign"]] = a.get("wtc") or self.si.type_info(a["type"])["wtc"]
            nodes = self.routes.get(a["callsign"])
            if nodes and a.get("next_node") in nodes:    # forget the part of a cleared route already taxied
                self.routes[a["callsign"]] = nodes[max(nodes.index(a["next_node"]) - 1, 0):]
        for cs in sorted(by_cs, key=lambda c: self._priority(by_cs[c])):
            a = by_cs[cs]
            if a["kind"] == "arrival":
                self._arrival(obs, a)
            else:
                self._departure(obs, a)
        busy = self._issued_now | {c["callsign"] for c in obs["clearances"] if c["state"] == "issued"}
        self._ground_probe(by_cs, busy, obs["t"])
        self._unjam(obs, by_cs, busy)
        self._issued_now = set()
        self._sequence()

    def _adopt(self, obs, by_cs):
        """Hand-over: take over traffic another controller (the Human) already moved along. Runs on this planner's first
        pass that sees traffic; the runner builds a fresh planner on every switch to the Agent, so no flag from an
        earlier stint survives. Reads only the observation and sets only this planner's own bookkeeping (its progress
        flags, runway claims of live clearances, the last takeoff per runway): every instruction it then sends still
        goes through _propose and the safety engine."""
        if self.order or not by_cs:
            return
        for c in obs["clearances"]:       # live runway clearances someone else issued still hold their runway
            end = next((e for e in self.ends if re.search(rf"runway {self.si.spoken_runway(e)}(?! (left|right|center))",
                                                          c["text"])), None)
            if c["action"] in CLAIMS and end and c["callsign"] in by_cs:
                pav = self.g.end_runway[end]
                self.claims[(pav, c["callsign"])] = c["id"]
                self.claim_action[c["id"]] = c["action"]
                if c["action"] == "cross_runway":
                    self.cross_ids.add(c["id"])
        t_seen = obs["t"] - float(obs.get("data_age_s") or 0.0)
        for cs, a in by_cs.items():
            st, ph, cl = self.assigned.setdefault(cs, {}), a["phase"], a.get("clearance") or {}
            act = cl.get("action") if cl.get("state") in ("issued", "acknowledged") else None   # its live clearance
            # its taxi route is not observed: assume the shortest one from where it is to where it is going (what the
            # pilots fly by default), so this planner's routing and conflict probe see it
            dst = (self.g.stands[a["gate"]].node if a["kind"] == "arrival" and a.get("gate") in self.g.stands
                   else a.get("entry_node"))
            if ph in ("taxi_in", "taxi_out", "hold_short") and a.get("next_node") and dst:
                r = self.si.taxi_route(a["next_node"], dst)
                if r and len(r) > 1:
                    self.routes[cs] = list(r)
            if (act == "hold_position" or a.get("status_text") == "Holding position") and ph in (
                    "taxi_in", "taxi_out", "hold_short"):                                # held by the previous controller:
                self.ground_hold[cs] = "the traffic it was held for"                   # released by the probe as its own
                self.hold_since[cs] = obs["t"] - float(a.get("stopped_s") or 0.0)
            if a["kind"] == "arrival":
                end = a.get("arrival_end")
                landing = a.get("landing_cleared") or act == "cleared_to_land"
                if end in self.ends and (landing or (ph == "final" and act == "contact_tower_or_ground")):
                    st["end"] = end
                    st["exit"] = self._exit(end, a, self.g.stands[a["gate"]].node)
                    if landing:
                        st["landing"] = True
                        if act == "cleared_to_land":
                            self.watch[cl["id"]] = (cs, "landing")
                if ph == "taxi_in" and a.get("frequency") == "ground":
                    st["ground"] = True
                    if act in ("taxi_via", "hold_short", "cross_runway", "resume_taxi"):
                        st["taxi"] = True
                continue
            if ph == "pushback" or (ph == "at_gate" and act == "pushback_approved") or ph in (
                    "taxi_out", "hold_short", "line_up", "takeoff_roll", "airborne"):
                st["push"] = True
            if ph in ("at_gate", "pushback") and st.get("push"):
                sn = self.g.stands[a["gate"]]
                self.pushbacks[cs] = {"xy": tuple(self.g.node_xy[sn.lane_node]), "t": self.t, "lane": sn.lane_node,
                                      "stand_xy": tuple(self.g.node_xy[sn.node])}
            if a.get("dep_end") in self.ends and ph in ("taxi_out", "hold_short", "line_up", "takeoff_roll", "airborne"):
                st.update(end=a["dep_end"], entry=a.get("entry_node"), taxi=True)
            if "end" in st and (ph in ("line_up", "takeoff_roll", "airborne")
                                or act in ("line_up_and_wait", "cleared_for_takeoff")):  # incl. one not yet moving
                st["luaw"] = True
                if act == "line_up_and_wait":
                    self.watch[cl["id"]] = (cs, "luaw")
                if a.get("takeoff_cleared") or act == "cleared_for_takeoff":
                    st["to"] = True
                    if act == "cleared_for_takeoff":
                        self.watch[cl["id"]] = (cs, "to")
            if ph == "airborne" and a.get("runway"):    # just departed: count its takeoff as now (conservative spacing)
                self.last_airborne.setdefault(a["runway"], (t_seen, self.wtc.get(cs) or a.get("wtc") or "M", cs, a.get("wake")))

    def _track(self, by_cs, live):
        """What the tower read off raw simulator events, taken from the observation instead: takeoff roll (departure spacing counts
        from its start), airborne / parked (route done), and runway clearances the pilot cancelled ("unable")."""
        t_seen = self.t - float(self.obs.get("data_age_s") or 0.0)
        for cs, a in by_cs.items():
            ph, prev = a["phase"], self.phase.get(cs)
            if ph == "takeoff_roll" and prev != "takeoff_roll":
                st = self.assigned.get(cs, {})
                pav = a.get("runway") or (self.g.end_runway.get(st["end"]) if st.get("end") else None)
                if pav:
                    self.last_airborne[pav] = (t_seen, self.wtc.get(cs, "M"), cs, a.get("wake"))
            if ph == "airborne":
                self.assigned.setdefault(cs, {})["airborne"] = True
            if ph in ("airborne", "parked"):
                self.routes.pop(cs, None)
            if ph == "go_around" and prev != "go_around":
                self.emit(self.t, "alert", cs, {"level": "warning", "message": f"{cs} is going around."},
                          f"{cs} is going around; it rejoins the final on its own.")
            self.phase[cs] = ph
        for cs in [c for c in self.routes if c not in by_cs]:
            self.routes.pop(cs)
        for cs in [c for c in self.pushbacks if c not in by_cs or by_cs[c]["phase"] not in ("at_gate", "pushback")]:
            self.pushbacks.pop(cs)
        for cid, (cs, flag) in list(self.watch.items()):
            if cid in live:
                continue
            self.watch.pop(cid)
            a, st = by_cs.get(cs), self.assigned.get(cs, {})
            if a is None:
                continue
            cancelled = ((flag == "landing" and a["phase"] == "final" and not a.get("landing_cleared"))
                         or (flag == "luaw" and a["phase"] == "hold_short")
                         or (flag == "to" and a["phase"] == "line_up" and not a.get("takeoff_cleared")))
            if cancelled:
                st.pop(flag, None)

    def _priority(self, a):
        """Who gets a runway first this pass: aircraft waiting to cross, then departures at the hold point in the order
        they got there, then everyone else first come, first served."""
        if a.get("hold"):
            return (0 if a["kind"] == "arrival" else 1, -float(a.get("stopped_s") or 0.0))
        return (2, float(self.order[a["callsign"]]))

    # -- safety picture and proposals -----------------------------------------------------------------------------
    def _picture(self, by_cs):
        """The safety engine's view: the observation plus what this planner cleared."""
        t = self.t
        occupants = {}
        for r in self.obs["runways"]:
            occupants[r["id"]] = sorted({w["callsign"] for w in r["occupancy"] if w["kind"] == "occupied" and w["end_s"] >= t - 0.15})
        rc = [{"callsign": cs, "action": self.claim_action.get(cid), "pavement": pav, "t": t}
              for (pav, cs), cid in self.claims.items()]
        for cs, a in by_cs.items():
            if a["phase"] == "final" and a.get("landing_cleared") and not any(x["callsign"] == cs for x in rc):
                rc.append({"callsign": cs, "action": "cleared_to_land", "pavement": a["runway"], "t": t})
        routes, route_d, parked = {}, {}, {}
        for cs, a in by_cs.items():
            r = self.routes.get(cs)
            if r and a["phase"] in ("taxi_in", "taxi_out", "hold_short", "runway_exit", "landing_roll"):
                routes[cs] = r
                route_d[cs] = self._route_d(a, r)
            elif a["phase"] in ("taxi_out", "taxi_in", "hold_short") and a["speed_mps"] < 0.3 and a.get("next_node"):
                parked[cs] = a["next_node"]
        lvl = self.obs.get("level") or {}
        eff = lvl.get("effects") or {}
        wx = self.obs["weather"]
        live_actions = {}
        for c in self.obs["clearances"]:
            live_actions.setdefault(c["callsign"], set()).add(c["action"])
        last_roll = {pav: {"t": v[0], "wtc": v[1], "callsign": v[2], "cwt": v[3]} for pav, v in self.last_airborne.items()}
        return {"t": t, "aircraft": by_cs, "live_actions": live_actions, "occupants": occupants, "clear_at": {},
                "runway_clearances": rc, "last_roll": last_roll, "routes": routes, "route_d": route_d, "parked_on": parked,
                "paths": {}, "pushbacks": self.pushbacks, "hot_nodes": {},
                "tailwind": {e: self._tailwind(e) for e in self.ends}, "tailwind_limit": self._limit(),
                "rain": (wx.get("vis_sm") or 10.0) < safety.RAIN_VIS_SM or eff.get("occupancy_mult", 1.0) > 1.0,
                "rollout_s": ROLLOUT_S * eff.get("occupancy_mult", 1.0)}

    def _route_d(self, a, route):
        """{edge: (distance to its start, distance to its end)} along a route from where the aircraft is now."""
        nn = a.get("next_node")
        i = route.index(nn) if nn in route else 0
        xy = self.g.node_xy[route[i]]
        out, d = {}, math.hypot(a["x"] - xy[0], a["y"] - xy[1])
        if i > 0:
            out[(route[i - 1], route[i])] = (0.0, d)
        for u, v in zip(route[i:], route[i + 1:]):
            e = self.g.edge_between(u, v)
            L = e[0].length if e is not None else 0.0
            out[(u, v)] = (d, d + L)
            d += L
        return out

    def _propose(self, a, action, params, summary, facts=(), rules=(), alternatives=(), decision=None):
        """proposal -> safety_check -> issue -> clearance. Returns the issue() result, or None if rejected / failed."""
        cs = a["callsign"]
        reason = summary + (" (" + "; ".join(facts) + ")" if facts else "") + "."
        pav = self.si.runway_pavement(params.get("runway")) if action in CLAIMS else None
        end = params.get("runway") if params.get("runway") in self.ends else (params.get("to") if params.get("to") in self.ends else None)
        prop = {"callsign": cs, "action": action, "end": end, "pavement": pav, "route": params.get("route"),
                "eta": a.get("eta_s") or 0.0}
        if params.get("route") and "x" in a:
            prop["route_d"] = self._route_d(a, params["route"])
        if action == "pushback_approved":
            sn = self.g.stands[a["gate"]]
            prop.update(lane_xy=tuple(self.g.node_xy[sn.lane_node]), push_from=tuple(self.g.node_xy[sn.node]),
                        push_to=tuple(self.g.node_xy[sn.lane_node]))
        ok, rule, why = safety.check(self.pic, prop)
        data = {"action": action, "params": params, "rules": list(rules),
                "alternatives": [dict(x) for x in alternatives]}
        if decision:
            data["decision"] = decision
        key = (cs, action)
        if not ok:
            if self.rejected.get(key) != rule:
                self.rejected[key] = rule
                self.emit(self.t, "proposal", cs, data, reason)
                self.emit(self.t, "safety_check", cs, {"action": action, "approved": False, "rule": rule, "reason": why}, why)
                self.emit(self.t, "alert", cs, {"level": "info", "message": why, "source": "safety", "action": action,
                                                "rule": rule},
                          f"Safety engine blocked {action.replace('_', ' ')} for {cs}: {why}")
            return None
        self.rejected.pop(key, None)
        self.emit(self.t, "proposal", cs, data, reason)
        self.emit(self.t, "safety_check", cs, {"action": action, "approved": True, "rule": rule, "reason": why}, why)
        res = self.si.issue({"callsign": cs, "action": action, "params": params})
        if not res.get("ok"):
            self.emit(self.t, "alert", cs, {"level": "warning", "message": res.get("error"), "action": action},
                      f"The simulator refused {action} for {cs}: {res.get('error')}")
            return None
        cid = res["clearance_id"]
        self._issued_now.add(cs)
        if action in ("taxi_via", "cross_runway"):
            self.ground_hold.pop(cs, None)     # a new route or crossing replaces a hold position
        if action == "taxi_via":
            self.routes[cs] = list(params.get("route") or [])
            if self.routes[cs]:
                self.pic["routes"][cs] = self.routes[cs]
                self.pic["route_d"][cs] = prop.get("route_d") or {}
        if pav:
            self.claims[(pav, cs)] = cid
            self.claim_action[cid] = action
            if action == "cross_runway":
                self.cross_ids.add(cid)
            self.pic["runway_clearances"].append({"callsign": cs, "action": action, "pavement": pav, "t": self.t})
            self.pic["live_actions"].setdefault(cs, set()).add(action)
        self.expected[cid] = (cs, action, self._expected_runway(action, params))
        self.emit(self.t, "clearance", cs, {"clearance_id": cid, "action": action, "params": params,
                                            "text": res.get("text", "")}, res.get("text") or reason)
        return res

    def _expected_runway(self, action, params):
        """The runway end the readback must contain (None: no runway in this instruction)."""
        if action in safety.RUNWAY_ACTIONS:
            return params.get("runway")
        if action == "taxi_via":
            if params.get("to") in self.ends:
                return params["to"]
            route = params.get("route") or []
            for u, v in zip(route, route[1:]):
                e = self.g.edge_between(u, v)
                if e is not None and e[0].pavement:
                    return self.si.runway_label(e[0].pavement)
            return None
        if action == "contact_tower_or_ground":
            return params.get("runway")
        return None

    def _note(self, cs, action, summary, facts=(), decision=None):
        """A decision that sends no instruction (runway choice, keep holding): recorded with its reason."""
        data = {"action": action, "facts": list(facts)}
        if decision:
            data["decision"] = decision
        self.emit(self.t, "alert", cs or None, dict(data, level="info", message=summary, source="planner"),
                  summary + (" (" + "; ".join(facts) + ")" if facts else "") + ".")

    def answer_readbacks(self, obs):
        """Between decision passes: accept or reject readbacks as soon as they arrive."""
        self.t, self.live = obs["t"], {c["id"]: c for c in obs["clearances"]}
        self._readbacks()

    # -- readbacks --------------------------------------------------------------------------------------------------
    def _readbacks(self):
        for rb in self.si.get_readbacks():
            exp = self.expected.get(rb["clearance_id"])
            heard = rb.get("parsed") or {}
            if exp is None:
                # Not issued by this controller (e.g. sent by the Human before a mid-run switch): check it against the
                # clearance as observed (its action, and a runway named in its text); never accept it unchecked.
                c = self.live.get(rb["clearance_id"])
                hr = heard.get("runway")
                said = hr is None or hr not in self.ends or re.search(
                    rf"runway {self.si.spoken_runway(hr)}(?! (left|right|center))", c["text"] if c else "")
                ok = bool(c) and heard.get("action") == c["action"] and bool(said)
                why = (f"Readback matches the {c['action'].replace('_', ' ')} clearance issued before this controller "
                       f"took over." if ok else "Readback does not match the clearance in force (issued before this "
                       "controller took over); asked the pilot to read it back again.")
            else:
                _, action, rwy = exp
                ok = heard.get("action") == action and (rwy is None or heard.get("runway") == rwy)
                why = (f"Readback matches the {action.replace('_', ' ')} clearance" + (f" for runway {rwy}." if rwy else ".")
                       if ok else f"Readback does not match: expected {action.replace('_', ' ')}"
                       + (f" runway {rwy}" if rwy else "") + f", heard {heard.get('action') or 'no instruction'}"
                       + (f" runway {heard.get('runway')}" if heard.get("runway") else "") + ".")
            self.si.resolve_readback(rb["id"], ok)
            cs = rb["callsign"]
            self.emit(self.t, "readback", cs, {"readback_id": rb["id"], "clearance_id": rb["clearance_id"],
                                               "transcript": rb["text"], "matched": ok}, why)
            if not ok:
                self.emit(self.t, "readback_correction", cs, {"readback_id": rb["id"], "clearance_id": rb["clearance_id"],
                                                              "transcript": rb["text"]},
                          f"Wrong readback from {cs}; asked the pilot to read the clearance back again. {why}")
                self.emit(self.t, "alert", cs, {"level": "warning", "message": why, "source": "readback"}, why)

    # -- helpers ----------------------------------------------------------------------------------------------------
    def _limit(self):
        return (self.obs.get("level") or {}).get("tailwind_limit_kt") or TAILWIND_LIMIT_KT

    def _tailwind(self, end):
        return tailwind_kt(self.obs["weather"], self.ends[end]["heading"])

    def _taxi_params(self, a, to, dst, origin=None, obs=None):
        """An explicit route that avoids taxiing head-on into a route already cleared the other way and past an aircraft
        pushing back, or the simulator's shortest route if there is no usable node to start from. With `obs` (a new
        taxi clearance that may wait) None while the only way is head-on into other traffic, for up to TAXI_WAIT_S."""
        origin = origin or a.get("next_node")
        if not origin or not dst:
            return {"to": to}
        g = self.g
        against = {(v, u) for cs, nodes in self.routes.items() if cs != a["callsign"] for u, v in zip(nodes, nodes[1:])}
        pushing = [np.array(g.frame.to_xy(o["lon"], o["lat"])) for o in (obs or {}).get("aircraft", [])
                   if o["phase"] == "pushback" and o["callsign"] != a["callsign"]]
        blocked = {n for n, xy in g.node_xy.items() for p in pushing if math.hypot(xy[0] - p[0], xy[1] - p[1]) < PUSH_KEEP_CLEAR_M}

        def weight(hard, avoid):
            def w(u, v, d):
                e = g.edges[d["eid"]]
                if e.kind == "runway" or (hard and ((u, v) in against or v in blocked)):
                    return None
                return (e.length + (CROSSING_PENALTY_M if e.pavement else 0.0) + (OPPOSING_PENALTY_M if (u, v) in against else 0.0)
                        + (REPEAT_CROSSING_PENALTY_M if e.pavement in avoid else 0.0))
            return w

        def twice(route):
            runs = [r.runway for r in g.build_path(route).runs]
            return frozenset(p for p in runs if runs.count(p) > 1)
        fallback = None
        for hard in (True, False):      # never head-on into another cleared route if any other way exists
            try:
                route = nx.dijkstra_path(g.g, origin, dst, weight=weight(hard, frozenset()))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                if hard and obs is not None and obs["t"] - self.taxi_wait.setdefault(a["callsign"], obs["t"]) < TAXI_WAIT_S:
                    return None
                continue
            again = twice(route)
            if again:
                try:
                    route = nx.dijkstra_path(g.g, origin, dst, weight=weight(hard, again))
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    pass
            if not twice(route):
                return {"route": route, "to": to}
            fallback = fallback or route
        return {"route": fallback, "to": to} if fallback else {"to": to}

    def _runway(self, obs, pav):
        return next(r for r in obs["runways"] if r["id"] == pav)

    def _blockers(self, obs, pav, me, t0, t1, ignore=()):
        """Who occupies or has reserved this pavement during [t0, t1] (absolute sim seconds)."""
        out = ["%s cleared" % cs for (p, cs) in self.claims if p == pav and cs != me and cs not in ignore]
        for w in self._runway(obs, pav)["occupancy"]:
            if w["callsign"] == me or w["callsign"] in ignore:
                continue
            if w["kind"] == "occupied" and w["end_s"] >= obs["t"] - 0.15:
                out.append("%s on runway" % w["callsign"])
            elif w["kind"] == "reserved" and w["start_s"] < t1 and w["end_s"] > t0:
                out.append("%s reserved" % w["callsign"])
        return sorted(set(out))

    @staticmethod
    def _landing_due(obs, pav, within_s, me):
        for o in obs["aircraft"]:
            if (o["kind"] == "arrival" and o["phase"] == "final" and o["callsign"] != me and o.get("arrival_end")
                    and o["runway"] == pav and (o["eta_s"] or 999) <= within_s):
                return o
        return None

    # -- ground conflict probe --------------------------------------------------------------------------------------
    def _future(self, a, speed, from_rest=False, secs=PROBE_S):
        """Where this aircraft will be each second for `secs` at `speed` (or accelerating from rest to it): along its
        cleared route when the planner knows it, else straight ahead on its heading."""
        g = self.g
        here = np.array(g.frame.to_xy(a["lon"], a["lat"]), dtype=float)
        nodes = self.routes.get(a["callsign"]) or []
        nxt = a.get("next_node")
        v0 = 0.0 if from_rest else speed
        top = RESUME_TAXI_SPEED if (from_rest or 0.3 < speed < RESUME_TAXI_SPEED) else speed
        v = np.minimum(v0 + TAXI_ACCEL * (np.arange(int(secs)) + 0.5), max(top, v0))
        t = np.concatenate([[0.0], np.cumsum(v)])
        if nxt in nodes and a["phase"] != "pushback":
            rest = tuple(nodes[nodes.index(nxt):])
            cached = self._tracks.get(a["callsign"])
            if cached is None or cached[0] != rest:
                pts = np.vstack([g.build_path(list(rest)).pts]) if len(rest) > 1 else np.array([g.node_xy[nxt]])
                self._tracks[a["callsign"]] = cached = (rest, pts)
            pts = np.vstack([here, cached[1]])
        elif a["phase"] == "pushback" and a.get("gate") in g.stands:
            pts = np.vstack([here, g.node_xy[g.stands[a["gate"]].lane_node]])
        else:
            h = math.radians(a["heading_deg"])
            pts = np.vstack([here, here + np.array([math.sin(h), math.cos(h)]) * (secs * max(speed, 1.0))])
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        t = np.minimum(t, cum[-1])
        return np.stack([np.interp(t, cum, pts[:, 0]), np.interp(t, cum, pts[:, 1])], axis=-1)

    def _ground_probe(self, by_cs, busy, obs_t):
        """Ground control's scan: two taxiing aircraft whose next 20 s bring their wingtips together get a hold for one
        of them until the other has passed. Same-direction traffic is left to the pilots."""
        ground = [a for a in by_cs.values() if a["phase"] in ("taxi_in", "taxi_out", "runway_exit", "pushback", "hold_short", "line_up")
                  and not a.get("done")]
        crossing = {cs for (p, cs) in self.claims}
        on_rwy = {c for occ in self.pic["occupants"].values() for c in occ}

        def ahead_of_runway(a):     # a crossing clearance is live and the runway is still on the route ahead (or under it)
            cs = a["callsign"]
            if cs not in crossing:
                return False
            pavs, nodes, nn = {p for (p, c) in self.claims if c == cs}, self.routes.get(cs) or [], a.get("next_node")
            rest = nodes[max(nodes.index(nn) - 1, 0):] if nn in nodes else nodes
            return cs in on_rwy or not rest or any((e := self.g.edge_between(u, v)) is not None and e[0].pavement in pavs
                                                   for u, v in zip(rest, rest[1:]))
        holdable = lambda a: (a["phase"] in ("taxi_in", "taxi_out") and not ahead_of_runway(a) and not a.get("hold")
                              and a["callsign"] not in busy)
        starting = {cs for cs, t0 in self.released.items() if obs_t - t0 < RELEASE_GRACE_S}
        moving = lambda a: a["speed_mps"] > 0.3 or (a["callsign"] in starting and a["phase"] in ("taxi_in", "taxi_out", "hold_short"))
        fut = {a["callsign"]: self._future(a, a["speed_mps"], from_rest=a["speed_mps"] <= 0.3) if moving(a)
               else self._future(a, a["speed_mps"] if a["phase"] == "pushback" else 0.0) for a in ground}
        for a in ground:
            if a["phase"] == "pushback" and a["speed_mps"] < 0.05:     # still at the stand: not moving yet
                fut[a["callsign"]] = fut[a["callsign"]][:1].repeat(len(fut[a["callsign"]]), axis=0)

        def waits_for(b, a):
            return not moving(b) and b["phase"] != "pushback" and b.get("blocked_by") == a["callsign"]

        def reach(a):
            return math.hypot(a["length_m"], a["wingspan_m"]) / 2.0

        def clash(a, b, fa, fb, now_ok=False):
            if not moving(a):
                return False
            if waits_for(b, a):     # it is waiting for this one: this one goes first, unless it would hit it where it stands
                return bool(_boxes_meet(fa, a, fb[:1].repeat(len(fa), axis=0), b, 0.0).any())
            meet = _boxes_meet(fa, a, fb, b, PROBE_MARGIN_M + (RELEASE_EXTRA_M if now_ok else 0.0))
            return bool(meet.any() and (now_ok or not meet[0]))

        def stop_at(a):
            f = fut[a["callsign"]]
            return f[min(len(f) - 1, int(math.ceil(3.0 + a["speed_mps"] / 2.6)))]
        for cs, other in list(self.ground_hold.items()):
            a = by_cs.get(cs)
            if a is None or a["phase"] not in ("taxi_in", "taxi_out", "hold_short"):
                self.ground_hold.pop(cs, None)
                continue
            if cs in busy or cs in crossing:
                continue
            fa = self._future(a, RESUME_TAXI_SPEED, from_rest=True)
            going = dict(a, speed_mps=RESUME_TAXI_SPEED)
            held_long = obs_t - self.hold_since.get(cs, obs_t) > HOLD_MAX_S
            hits = [b for b in ground if b["callsign"] != cs and clash(going, b, fa, fut[b["callsign"]], now_ok=True)]
            if not hits or (held_long and not any(moving(b) for b in hits)):   # time-out: never into one moving (or just released)
                if self._propose(a, "resume_taxi", {}, "%s resume taxi: %s has passed" % (cs, other),
                                 ["No traffic within %.0f m of %s's route in the next %.0f s" % (PROBE_MARGIN_M, cs, PROBE_S)],
                                 [RULES["taxi"]]):
                    self.ground_hold.pop(cs)
                    self.released[cs] = obs_t
                    starting.add(cs)
                    fut[cs] = fa
        ahead = {}
        for i, a0 in enumerate(ground):
            for b0 in ground[i + 1:]:
                a, b = a0, b0
                ca, cb = a["callsign"], b["callsign"]
                if (ca in self.ground_hold) + (cb in self.ground_hold) == 2:
                    continue
                if ca in self.ground_hold:      # a held aircraft is still an obstacle: only the other one can be held
                    a, b, ca, cb = b, a, cb, ca
                if not clash(a, b, fut[ca], fut[cb]):
                    if not clash(b, a, fut[cb], fut[ca]):        # symmetric: a standing aircraft first in the list
                        continue
                    a, b, ca, cb = b, a, cb, ca

                def clear_of_all(x, y):
                    for z in ground:
                        cz = z["callsign"]
                        if cz == x["callsign"] or (z is not y and not moving(z)):
                            continue
                        if cz not in ahead:
                            ahead[cz] = self._future(z, RESUME_TAXI_SPEED, secs=STOP_CLEAR_S)
                        if float(np.min(np.hypot(*(ahead[cz] - stop_at(x)).T))) < reach(x) + reach(z):
                            return False
                    return True
                cands = [x for x, y in ((a, b), (b, a)) if holdable(x) and x["speed_mps"] > 0.3 and clear_of_all(x, y)]
                if not cands and not moving(b) and holdable(a) and a["speed_mps"] > 0.3:
                    cands = [a]     # taxiing into a standing aircraft: stop it even if it stops in a route (released as any probe hold)
                if not cands:
                    continue
                who = sorted(cands, key=lambda x: (x["kind"] == "arrival", -self.order[x["callsign"]]))[0]
                other = cb if who is a else ca
                if self._propose(who, "hold_position", {}, "%s hold position for %s" % (who["callsign"], other),
                                 ["%s and %s would pass closer than %.0f m within %.0f s" % (ca, cb, PROBE_MARGIN_M, PROBE_S)],
                                 [RULES["taxi"]], [{"option": "let both continue", "reason": "wingtip conflict"}]):
                    self.ground_hold[who["callsign"]] = other
                    self.hold_since[who["callsign"]] = obs_t

    def _unjam(self, obs, by_cs, busy):
        """Two aircraft stopped nose to nose, each waiting for the other: give one of them a new route that keeps clear
        of the other (turning round where it stands if needed)."""
        g = self.g
        t = obs["t"]
        for a in by_cs.values():
            b = by_cs.get(a.get("blocked_by") or "")
            if (b is None or b.get("blocked_by") != a["callsign"] or a["speed_mps"] > 0.1 or b["speed_mps"] > 0.1
                    or min(a.get("stopped_s") or 0.0, b.get("stopped_s") or 0.0) < UNJAM_AFTER_S
                    or t - self.unjammed.get(a["callsign"], -1e9) < UNJAM_RETRY_S):
                continue
            self.unjammed[a["callsign"]] = self.unjammed[b["callsign"]] = t
            for x, y in sorted(((a, b), (b, a)), key=lambda p: p[0]["kind"] == "arrival"):
                nodes = self.routes.get(x["callsign"]) or []
                st = self.assigned.get(x["callsign"], {})
                to = st.get("end") if x["kind"] == "departure" else x.get("gate")
                if not nodes or not to or x["callsign"] in busy or x["phase"] not in ("taxi_in", "taxi_out", "hold_short"):
                    continue
                yx = np.array(g.frame.to_xy(y["lon"], y["lat"]))
                room = math.hypot(y["length_m"], y["wingspan_m"]) / 2.0 + 10.0
                near = {n for n, xy in g.node_xy.items() if math.hypot(xy[0] - yx[0], xy[1] - yx[1]) < room}
                under = {eid for eid, e in g.edges.items()
                         if float(np.min(np.hypot(*(np.asarray(e.pts) - yx).T))) < room + x["wingspan_m"] / 2.0 + 15.0}

                def w(u, v, d):
                    e = g.edges[d["eid"]]
                    if e.kind == "runway" or u in near or v in near or d["eid"] in under:
                        return None
                    return e.length + (CROSSING_PENALTY_M if e.pavement else 0.0)
                xx = np.array(g.frame.to_xy(x["lon"], x["lat"]))
                for origin in (x.get("next_node"), x.get("prev_node")):
                    if not origin or origin in near or (origin == x.get("next_node") and math.hypot(
                            *(g.node_xy[origin] - yx)) < math.hypot(*(xx - yx))):
                        continue
                    try:
                        route = nx.dijkstra_path(g.g, origin, nodes[-1], weight=w)
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue
                    if self._propose(x, "taxi_via", {"route": route, "to": to},
                                     "New route for %s around %s (each was waiting for the other)" % (x["callsign"], y["callsign"]),
                                     ["%s and %s stopped nose to nose for %.0f s" % (x["callsign"], y["callsign"], x.get("stopped_s") or 0.0)],
                                     [RULES["taxi"]], [{"option": "wait", "reason": "neither can move"}]):
                        break
                else:
                    continue
                break

    # -- runway flow (wind) ----------------------------------------------------------------------------------------
    def _set_config(self, arr, dep, reason, decision=None):
        prop = {"callsign": None, "action": "set_runway_config", "ends": list(arr) + list(dep)}
        ok, rule, why = safety.check(self.pic, prop)
        data = {"action": "set_runway_config", "params": {"arrival_runways": list(arr), "departure_runways": list(dep)}}
        if decision:
            data["decision"] = decision
        if not ok and self.rejected.get((None, "set_runway_config")) == rule:
            return False
        self.rejected[(None, "set_runway_config")] = None if ok else rule
        self.emit(self.t, "proposal", None, data, reason)
        self.emit(self.t, "safety_check", None, {"action": "set_runway_config", "approved": ok, "rule": rule, "reason": why}, why)
        if ok:
            self.si.set_runway_config(list(arr), list(dep))
        return ok

    def _flow(self, obs):
        """Turn the airport around (every runway to its other end) when the wind gives the active direction a tailwind."""
        g = self.g
        cfg = obs["runway_config"]
        arr_pav = {g.end_runway[e] for e in cfg["arrival_runways"]}
        own = [e for e in cfg["departure_runways"] if g.end_runway[e] not in arr_pav]
        if own and own != cfg["departure_runways"]:
            # a runway that also lands an arrival every ~100 s never has a gap long enough for a departure: depart only
            # from runways without arrivals, as KPHX does (arrivals 26/25L, departures 25R)
            if self._set_config(cfg["arrival_runways"], own, "Departures from %s only, arrivals %s: arrivals land on %s "
                                "about every 100 s, a departure would wait for gaps that never come"
                                % (", ".join(own), ", ".join(cfg["arrival_runways"]), ", ".join(sorted(arr_pav)))):
                cfg = {"arrival_runways": list(cfg["arrival_runways"]), "departure_runways": own}
        ends = cfg["arrival_runways"] + cfg["departure_runways"]
        worst = max(self._tailwind(e) for e in ends)
        if worst <= FLOW_SWITCH_TAILWIND_KT:
            return
        new = {k: [g.other_end(e) for e in v] for k, v in cfg.items()}
        after = max(self._tailwind(e) for e in new["arrival_runways"] + new["departure_runways"])
        if after >= worst:
            return
        options = [{"id": "switch", "label": "turn the airport round: arrivals %s, departures %s" % (
                        ", ".join(new["arrival_runways"]), ", ".join(new["departure_runways"])), "tailwind_kt": round(after, 1)},
                   {"id": "keep", "label": "keep arrivals %s, departures %s" % (
                       ", ".join(cfg["arrival_runways"]), ", ".join(cfg["departure_runways"])), "tailwind_kt": round(worst, 1)}]
        decision = {"kind": "runway_flow", "options": options, "default": "switch", "callsign": None}
        decision["choice"] = self.choose(decision)
        if decision["choice"] != "switch":
            return
        w = obs["weather"]
        self._set_config(new["arrival_runways"], new["departure_runways"],
                         "Runway flow change: wind %03d at %d kt gives %.1f kt tailwind on the active runways (%.1f kt "
                         "after the change); pilots refuse more than %.0f kt" % (
                             w.get("wind_dir") or 0, w.get("wind_kt") or 0, worst, after, self._limit()), decision)

    # -- arrivals ---------------------------------------------------------------------------------------------------
    def _arrival(self, obs, a):
        cs, st = a["callsign"], self.assigned.setdefault(a["callsign"], {})
        if a["phase"] == "go_around":
            st.pop("landing", None)
            st.pop("end", None)
            st.pop("go_around", None)
            return
        if a["phase"] == "final":
            if "end" in st and st["end"] not in obs["runway_config"]["arrival_runways"] and not a["landing_cleared"] and (
                    (a["eta_s"] or 999.0) > FLOW_FINISH_ETA_S or self._tailwind(st["end"]) > self._limit()):
                for k in ("end", "landing", "go_around", "exit"):     # flow changed: send it to the new direction
                    st.pop(k, None)
            if "end" not in st:
                self._assign_arrival_runway(obs, a, st)
            elif not a["landing_cleared"] and "landing" not in st:
                self._landing_clearance(obs, a, st)
            return
        if a["phase"] == "taxi_in" and a["speed_mps"] < 0.3 and "ground" not in st:
            if self._propose(a, "contact_tower_or_ground", {"facility": "ground", "frequency": "132.55"},
                             "Handed %s to ground" % cs, ["%s is clear of the runway" % cs], ["JO 7110.65BB 3-10-9 Runway exiting"]):
                st["ground"] = True
        if a["phase"] == "taxi_in" and "ground" in st and "taxi" not in st and a["speed_mps"] < 0.3 and a.get("stopped_s", 0) >= 0.0:
            params = self._taxi_params(a, a["gate"], self.g.stands[a["gate"]].node, obs=obs)
            if params is None:
                return
            if self._propose(a, "taxi_via", params, "Taxi %s to gate %s by the shortest route" % (cs, a["gate"]),
                             ["%s vacated the runway" % cs, "Gate %s assigned in the schedule" % a["gate"]], [RULES["taxi"]],
                             [{"option": "longer route avoiding crossings", "reason": "shortest route with a crossing penalty already chosen"}]):
                st["taxi"] = True
        if (a["phase"] in ("taxi_in",) and a.get("hold") and a["speed_mps"] < 0.3 and a.get("stopped_s", 0) > 0.8
                and cs not in self.ground_hold):
            self._maybe_cross(obs, a)

    def _assign_arrival_runway(self, obs, a, st):
        g = self.g
        cfg = obs["runway_config"]["arrival_runways"]
        cur = a.get("arrival_end")
        if cur:     # approach control sequenced it onto this final: keep it (or its other end after a flow change)
            cfg = [e for e in (cur, g.other_end(cur)) if e in cfg] or cfg
        eta = a["eta_s"] or 120.0
        t = obs["t"]
        stand = g.stands[a["gate"]]
        scored, notes = [], {}
        for end in cfg:
            pav = g.end_runway[end]
            free_at = self.last_arrival_slot.get(pav, -1e9)
            wait = max(0.0, free_at + ARRIVAL_SLOT_S - (t + eta))
            exit_node = self.si.choose_exit(end, a["type"], a["gate"])
            route = g.route(exit_node, stand.node) if exit_node else None
            crossings = max(len(g.build_path(route).runs) - 1, 0) if route else 9
            scored.append((wait + 20.0 * crossings, end))
            notes[end] = "%s: slot wait %.0f s, %d crossing(s) to gate" % (end, wait, crossings)
        best = min(scored)[1] if scored else cfg[0]
        decision = {"kind": "arrival_runway", "callsign": a["callsign"], "default": best,
                    "options": [{"id": e, "label": notes.get(e, e)} for e in cfg]}
        end = self.choose(decision)
        if end is None:
            return
        decision["choice"] = end
        pav = g.end_runway[end]
        self.last_arrival_slot[pav] = max(self.last_arrival_slot.get(pav, -1e9) + ARRIVAL_SLOT_S, t + eta)
        st["end"] = end
        st["exit"] = self._exit(end, a, stand.node)
        self._propose(a, "contact_tower_or_ground", {"facility": "tower", "frequency": FREQ.get(pav, "120.9"), "runway": end},
                      "Assigned %s to runway %s, first come first served on each landing runway" % (a["callsign"], end),
                      ["%s is %.0f s from the threshold" % (a["callsign"], eta)] + list(notes.values()),
                      ["First come, first served on each landing runway"],
                      [{"option": "runway %s" % e, "reason": n} for e, n in notes.items() if e != end], decision=decision)

    def _exit(self, end, a, dest):
        """The runway exit that clears the runway soonest, with a small allowance for the taxi to the gate. Never an exit
        whose way to the gate dips back onto the same runway."""
        g = self.g
        ty = self.si.type_info(a["type"])
        rw, info, _ = g.end_info(end)
        need = 1.25 * (ty["vref_mps"] ** 2 - EXIT_SPEED_MPS ** 2) * 0.94 / (2 * ty["landing_decel"])
        i = rw.chain.index(info["node"])
        best = None
        for node, dist in g.entries(end):
            if dist < need or dist >= rw.length_m - 200:
                continue
            j = rw.chain.index(node)
            rest = g.route(node, dest)
            if rest is None:
                continue
            path = g.build_path((rw.chain[i:j + 1] if i <= j else rw.chain[j:i + 1][::-1]) + rest[1:])
            if any(run.runway == rw.id and run.s0 > 1.0 for run in path.runs):
                continue
            clear = path.runs[0].s1 + ty["length_m"] / 2 + 10.0 if path.runs else dist
            occupancy = dist / (0.5 * (0.96 * ty["vref_mps"] + EXIT_SPEED_MPS)) + path.free_flow_time(dist) - path.free_flow_time(clear)
            score = occupancy + EXIT_TAXI_WEIGHT * path.free_flow_time(clear)
            if best is None or score < best[0]:
                best = (score, node)
        return best[1] if best else self.si.choose_exit(end, a["type"], a["gate"])

    def _landing_clearance(self, obs, a, st):
        eta = a["eta_s"] or 999.0
        if eta > LAND_CLEAR_ETA_S:
            return
        t = obs["t"]
        pav = self.g.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t + eta - 10.0, t + eta + 65.0)
        if not blockers:
            res = self._propose(a, "cleared_to_land", {"runway": st["end"], "exit": st.get("exit")},
                                "Cleared %s to land runway %s" % (a["callsign"], st["end"]),
                                ["%s is %.0f s from touchdown" % (a["callsign"], eta),
                                 "Runway %s free of traffic and reservations through %.0f s" % (pav, eta + 65)],
                                [RULES["land"]], [{"option": "hold clearance", "reason": "runway is free; waiting would risk a go-around"}])
            if res:
                st["landing"] = True
                self.watch[res["clearance_id"]] = (a["callsign"], "landing")
                return
            blockers = ["the safety engine withheld the landing clearance"]
        if eta <= GO_AROUND_ETA_S and "go_around" not in st:
            if self._propose(a, "go_around", {}, "Go around %s: runway %s not clear" % (a["callsign"], st["end"]),
                             ["%s is %.0f s from touchdown" % (a["callsign"], eta), "Runway %s: %s" % (pav, ", ".join(blockers))],
                             [RULES["land_withhold"]], [{"option": "clear to land anyway", "reason": "would create a runway conflict"}]):
                st["go_around"] = True

    def _maybe_cross(self, obs, a):
        hold = a["hold"]
        pav = hold["runway"]
        t = obs["t"]
        crossers = tuple(cs for (p, cs), cid in self.claims.items() if p == pav and cid in self.cross_ids)
        blockers = self._blockers(obs, pav, a["callsign"], t, t + CROSS_WINDOW_S, crossers)
        due = self._landing_due(obs, pav, CROSS_GUARD_S, a["callsign"])
        if not blockers:
            blockers = self._far_side(obs, a, pav)
        if self.claims.get((pav, a["callsign"])) in self.cross_ids:
            return
        st = self.assigned.setdefault(a["callsign"], {})
        if blockers or due:
            if not st.get("held-" + pav):
                st["held-" + pav] = True
                why = blockers + (["%s on %.0f s final" % (due["callsign"], due["eta_s"])] if due else [])
                self._note(a["callsign"], "hold_short", "%s keeps holding short of runway %s" % (a["callsign"], self.si.runway_label(pav)),
                           ["Runway %s: %s" % (pav, ", ".join(why))])
            return
        label = self.si.runway_label(pav)
        if self._propose(a, "cross_runway", {"runway": label}, "Cleared %s to cross runway %s" % (a["callsign"], label),
                         ["%s holding short of %s for %.0f s" % (a["callsign"], pav, a.get("stopped_s", 0)),
                          "Runway %s free for the next %.0f s and no landing due within %.0f s" % (pav, CROSS_WINDOW_S, CROSS_GUARD_S)],
                         [RULES["cross"]], [{"option": "keep holding", "reason": "runway is clear; holding only adds delay"}]):
            st.pop("held-" + pav, None)

    def _far_side(self, obs, a, pav):
        """Aircraft standing where this one would come off the runway after crossing (it would stop on the runway)."""
        g = self.g
        nodes = self.routes.get(a["callsign"]) or []
        if a.get("next_node") in nodes:
            nodes = nodes[nodes.index(a["next_node"]):]
        beyond, on, run = [], False, 0.0
        for u, v in zip(nodes, nodes[1:]):
            e = g.edge_between(u, v)
            if e is not None and e[0].pavement == pav:
                on = True
            elif on:
                beyond.append(v)
                run += e[0].length if e is not None else 0.0
                if run > FAR_SIDE_M:
                    break
        out = []
        for o in obs["aircraft"]:
            if o["callsign"] == a["callsign"] or o["phase"] in ("final", "go_around", "airborne", "at_gate", "parked") \
                    or o["speed_mps"] > 0.3:
                continue
            xy = np.array(g.frame.to_xy(o["lon"], o["lat"]))
            room = math.hypot(o["length_m"], o["wingspan_m"]) / 2.0 + a["wingspan_m"] / 2.0
            if any(math.hypot(*(g.node_xy[n] - xy)) < room for n in beyond):
                out.append("%s standing beyond the runway" % o["callsign"])
        return out

    # -- departures -------------------------------------------------------------------------------------------------
    def _departure(self, obs, a):
        cs, st = a["callsign"], self.assigned.setdefault(a["callsign"], {})
        ph = a["phase"]
        rr = st.get("reroute")
        if rr and rr[0] not in self.live:
            st.pop("reroute")
            if rr[2] and a.get("entry_node") and a["entry_node"] != rr[2] and "luaw" not in st:
                st["end"] = rr[1]   # the new route never took effect (the pilot had moved on): reroute again
        if st.get("end") and st["end"] not in obs["runway_config"]["departure_runways"] and "luaw" not in st:
            self._reroute_departure(a, st)
        if ph == "at_gate" and a["ready"]:
            if "end" not in st:
                self._assign_departure(obs, a, st)
            if "push" not in st and self._lane_clear(obs, a):
                if self._propose(a, "pushback_approved", {}, "Pushback approved for %s from gate %s" % (cs, a["gate"]),
                                 ["%s ready at gate %s" % (cs, a["gate"]), "Pushback lane clear within %.0f m" % PUSHBACK_CLEAR_M],
                                 ["JO 7110.65BB 3-7-1 Ground traffic movement"]):
                    st["push"] = True
                    sn = self.g.stands[a["gate"]]
                    self.pushbacks[cs] = {"xy": tuple(self.g.node_xy[sn.lane_node]), "t": self.t, "lane": sn.lane_node,
                                          "stand_xy": tuple(self.g.node_xy[sn.node])}
            return
        if ph == "taxi_out" and "push" in st and "taxi" not in st and a["speed_mps"] < 0.3:
            if "end" not in st:
                self._assign_departure(obs, a, st)
                if "end" not in st:
                    return
            params = self._taxi_params(a, st["end"], st["entry"], obs=obs)
            if params is None:
                return
            if self._propose(a, "taxi_via", params,
                             "Taxi %s to runway %s via %s" % (cs, st["end"], ", ".join(st["via"]) or "shortest route"),
                             ["%s pushed back from gate %s" % (cs, a["gate"]), "Runway %s chosen: %s" % (st["end"], st["why"])],
                             [RULES["taxi"]], st["alts"]):
                st["taxi"] = True
            return
        if ph == "hold_short" and a.get("hold") and "taxi" in st and cs not in self.ground_hold:
            pav = a["hold"]["runway"]
            if pav != self.g.end_runway[st["end"]] or not self._at_entry(a):   # a crossing of its own runway
                self._maybe_cross(obs, a)
            else:
                self._line_up(obs, a, st)
            return
        if ph == "line_up" and "luaw" in st and not a["takeoff_cleared"] and "to" not in st:
            self._takeoff(obs, a, st)

    def _reroute_departure(self, a, st):
        """Flow changed: a departure still taxiing goes to the other end of its runway."""
        if "taxi" not in st:
            for k in ("end", "entry"):
                st.pop(k, None)
            return
        g = self.g
        pav = g.end_runway[st["end"]]
        if a["phase"] not in ("taxi_out", "hold_short"):
            return
        start = a.get("prev_node") if a["speed_mps"] < 0.2 else None
        if start is None and (a.get("hold") or a.get("next_node") in g.runways[pav].chain):
            return
        start = start or a.get("next_node")
        new = g.other_end(st["end"])
        entry = self.si.choose_entry(new, start, a["type"]) if start else None
        params = self._taxi_params(a, new, entry, start)
        res = self._propose(a, "taxi_via", params,
                            "Runway flow change: taxi %s to runway %s instead of %s" % (a["callsign"], new, st["end"]),
                            ["Departure runways are now %s" % new], [RULES["taxi"]],
                            [{"option": "keep runway %s" % st["end"], "reason": "tailwind on that direction"}])
        if res:
            route = params.get("route") or []
            st["reroute"] = (res["clearance_id"], st["end"], route[-1] if route else None)
            st["end"] = new
            st.pop("entry", None)

    def _assign_departure(self, obs, a, st):
        g = self.g
        stand = g.stands[a["gate"]]
        cfg = obs["runway_config"]["departure_runways"]
        queued = {e: sum(1 for s in self.assigned.values() if s.get("end") == e and not s.get("airborne")) for e in cfg}
        best, notes = None, {}
        for end in cfg:
            entry = self.si.choose_entry(end, stand.lane_node, a["type"])
            if entry is None:
                continue
            route = g.route(stand.lane_node, entry)
            if route is None:
                continue
            path = g.build_path(route)
            crossings = len(path.runs) - 1
            shared = g.end_runway[end] in {g.end_runway[e] for e in obs["runway_config"]["arrival_runways"]}
            score = path.length / 5.0 + 40.0 * crossings + 90.0 * queued[end] + (60.0 if shared else 0.0)
            notes[end] = "%s: %.0f m taxi, %d crossing(s), %d ahead in queue%s" % (
                end, path.length, max(crossings, 0), queued[end], ", shared with arrivals" if shared else "")
            if best is None or score < best[0]:
                best = (score, end, entry)
        if best is None:
            return
        _, end, entry = best
        st.update(end=end, entry=entry, via=g.taxiways_on(g.route(stand.lane_node, entry) or []),
                  why=notes[end], alts=[{"option": "runway %s" % e, "reason": n} for e, n in notes.items() if e != end])
        self._note(a["callsign"], "assign_runway", "Assigned %s to depart runway %s" % (a["callsign"], end),
                   ["%s ready at gate %s" % (a["callsign"], a["gate"])] + list(notes.values()))

    def _at_entry(self, a):
        """Holding at its departure entry (not at a crossing of the same runway earlier on the route)."""
        if not a.get("entry_node"):
            return True
        ex, ey = self.g.node_xy[a["entry_node"]]
        return math.hypot(a["x"] - ex, a["y"] - ey) < 150.0

    def _lane_clear(self, obs, a):
        """No aircraft near the push lane, and no cleared taxi route still to pass it."""
        g = self.g
        lane = g.node_xy[g.stands[a["gate"]].lane_node]
        waited = obs["t"] - self.ready_at.setdefault(a["callsign"], obs["t"])
        for cs, nodes in self.routes.items():
            if cs == a["callsign"] or waited > PUSH_DELAY_MAX_S:
                continue
            if any(math.hypot(*(g.node_xy[n] - lane)) < PUSHBACK_CLEAR_M for n in nodes[1:]):
                return False
        for o in obs["aircraft"]:
            if o["callsign"] == a["callsign"] or o["phase"] in ("at_gate", "parked", "final", "airborne", "go_around"):
                continue
            x, y = g.frame.to_xy(o["lon"], o["lat"])
            if math.hypot(x - lane[0], y - lane[1]) < PUSHBACK_CLEAR_M:
                return False
        return True

    def _line_up(self, obs, a, st):
        if "luaw" in st:
            return
        t = obs["t"]
        pav = self.g.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t, t + 40.0)
        due = self._landing_due(obs, pav, LINE_UP_GUARD_S, a["callsign"])
        if blockers or due:
            return
        queue = [o for o in self.ac.values() if o["kind"] == "departure" and o["phase"] == "hold_short" and o.get("hold")
                 and o["hold"]["runway"] == pav and "taxi" in self.assigned.get(o["callsign"], {})
                 and o["callsign"] not in self.ground_hold and self.assigned[o["callsign"]].get("end") == st["end"]
                 and self._at_entry(o) and "luaw" not in self.assigned[o["callsign"]]]
        decision = None
        if len(queue) > 1:
            queue.sort(key=lambda o: (-float(o.get("stopped_s") or 0.0), o["callsign"]))
            decision = {"kind": "departure_gap", "runway": st["end"], "callsign": a["callsign"],
                        "options": [{"id": o["callsign"], "label": "%s (%s, wake %s) waiting %.0f s" % (
                            o["callsign"], o["type"], o.get("wake"), o.get("stopped_s") or 0.0)} for o in queue],
                        "default": queue[0]["callsign"]}
            decision["choice"] = self.choose(decision)
            if decision["choice"] != a["callsign"]:
                return
        res = self._propose(a, "line_up_and_wait", {"runway": st["end"]},
                            "%s line up and wait runway %s" % (a["callsign"], st["end"]),
                            ["Runway %s clear of traffic and reservations" % pav, "No landing due within %.0f s" % LINE_UP_GUARD_S],
                            [RULES["luaw"]], [{"option": "hold short", "reason": "runway is available now"}], decision=decision)
        if res:
            st["luaw"] = True
            self.watch[res["clearance_id"]] = (a["callsign"], "luaw")

    def _spacing_ok(self, st, a, t):
        last = self.last_airborne.get(self.g.end_runway[st["end"]])
        if last is None:
            return True
        return t - last[0] >= departure_gap_s(last[1], self.wtc.get(a["callsign"], "M"))

    def _takeoff(self, obs, a, st):
        t = obs["t"]
        pav = self.g.end_runway[st["end"]]
        blockers = self._blockers(obs, pav, a["callsign"], t, t + 30.0)
        due = self._landing_due(obs, pav, TAKEOFF_GUARD_S, a["callsign"])
        if blockers or due or not self._spacing_ok(st, a, t):
            return
        res = self._propose(a, "cleared_for_takeoff", {"runway": st["end"]},
                            "Cleared %s for takeoff runway %s" % (a["callsign"], st["end"]),
                            ["%s lined up on runway %s" % (a["callsign"], pav),
                             "No traffic on or reserved on the runway; no landing within %.0f s" % TAKEOFF_GUARD_S,
                             "Wake/time spacing from the previous departure satisfied"],
                            [RULES["takeoff"], RULES["same_rwy_dep"]], [{"option": "keep waiting", "reason": "all conditions met"}])
        if res:
            st["to"] = True
            st["airborne"] = False
            self.watch[res["clearance_id"]] = (a["callsign"], "to")

    # -- sequence events --------------------------------------------------------------------------------------------
    def _sequence(self):
        seqs = {}
        for a in self.ac.values():
            if a["kind"] == "arrival" and a["phase"] == "final" and a.get("arrival_end"):
                seqs.setdefault(a["arrival_end"], []).append((a.get("eta_s") or 0.0, a["callsign"]))
        for end, lst in seqs.items():
            order = [cs for _, cs in sorted(lst)]
            if self.sequences.get(end) != order:
                self.sequences[end] = order
                self.emit(self.t, "sequence", None, {"runway": end, "order": order},
                          f"Arrival sequence runway {end}: {', '.join(order)} (first come, first served by time to the "
                          f"threshold).")
