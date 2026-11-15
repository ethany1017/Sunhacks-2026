"""Deterministic planner: every decision pass it looks at the simif observation and proposes instructions.

Flow per instruction: proposal -> safety.check -> simif.issue -> clearance -> readback check. The planner decides WHAT
should happen next (sequence, runway, route, who takes a gap); the safety engine decides WHETHER it may happen now.
Key decisions (runway flow, which departure takes the gap) are built as options + a default; `choose` picks one
(A4 plugs Claude in there; here it always takes the default).
"""
import math

import networkx as nx

from . import safety

M_PER_DEG = 111320.0
LAND_CLEAR_ETA_S = 120.0        # start offering the landing clearance this far out (the runway must be predicted clear)
LAND_DECIDE_ETA_S = 30.0        # last chance before the 1,800 m decision point (~26 s at Vref): clear it or go around
SHORT_FINAL_CHECK_S = 12.0      # cleared arrival this close with the runway still blocked: go around (last resort)
ROLLOUT_S = 72.0                # typical landing occupancy, dry (docs/LEVELS.md measures 69-77 s), times occupancy_mult
FLOW_SWITCH_TAILWIND_KT = 4.0   # turn the airport round once the active direction has more tailwind than this
FLOW_FINISH_ETA_S = 150.0       # on a flow change an arrival this close finishes on the old direction while legal
CROSSING_PENALTY_M = 2500.0     # taxi routing: per runway-pavement edge (never trade a crossing for a short detour)
SHARED_EDGE_PENALTY_M = 60.0    # taxi routing: an edge another aircraft also uses (same direction)
CROSSER_PRIORITY_S = 20.0       # a crossing waiting this long goes before the next takeoff on that runway
PUSH_SPEED_MPS = 1.1            # pushback speed (tug), so the push movement lasts lead length / 1.1 s
PUSHBACK_EARLIEST_S = 282.0 * 0.75 - 15.0   # NASA NTRS 20190027149: pushback 4.72 min mean; 2.5 sigma early, minus a margin
PUSH_SWEEP_M = 45.0             # a pushed-back aircraft (centre on the lane node) reaches about this far
LEAD_IN_M = 90.0                # a re-route for a moving aircraft keeps this much of its current route first
ENTRY_NEAR_M = 120.0            # a departure at its runway's hold line this close (+ half length) to its entry node is at it
CONVERGE_LOOK_M = 250.0         # ground: look this far ahead along each route for a shared junction
CONVERGE_WINDOW_S = 20.0        # two aircraft reaching one junction closer in time than this: one holds
CONVERGE_MIN_STOP_M = 80.0      # ...if it is still this far from the junction (readback + braking distance)
CONVERGE_CLEAR_M = 70.0         # resume once the other aircraft is this far past the junction and from the holder
DEPARTURE_RUNWAYS = {"west": ["25R"], "east": ["07L"]}  # departures on the runway arrivals do not use
FREQ = {"tower": "120.9", "ground": "132.55"}


class Planner:
    def __init__(self, si, emit):
        self.si, self.emit = si, emit
        ap = si.get_airport()["graph"]
        nodes = ap["nodes"]
        lat0 = sum(n["lat"] for n in nodes.values()) / len(nodes)
        lon0 = sum(n["lon"] for n in nodes.values()) / len(nodes)
        self._ll0 = (lat0, lon0, math.cos(math.radians(lat0)))
        self.xy = {nid: self._to_xy(n["lon"], n["lat"]) for nid, n in nodes.items()}
        self.g = nx.Graph()
        for e in ap["edges"]:
            self.g.add_edge(e["from"], e["to"], length=e["length_m"], kind=e["kind"], pavement=e["pavement"])
        self.ends = si.runway_ends()
        self.runway_nodes = {n for e in self.ends.values() for n in e["chain"]}
        self.choose = lambda decision: decision["default"]   # A4: Claude picks among decision["options"]
        self.mem = {}           # callsign -> planner memory
        self.expected = {}      # clearance id -> (callsign, action, runway end the readback must contain or None)
        self.rejected = {}      # (callsign, action) -> rule of the last recorded rejection (one record per episode)
        self.last_roll = {}     # pavement -> {t, callsign, cwt, wtc}: the last takeoff roll seen
        self.pushbacks = {}     # callsign -> {xy: lane node, t: approval time}
        self.sequences = {}     # runway -> last emitted order
        self._near_cache = {}
        self.flow = "west"
        self.started = False

    # -- geometry ----------------------------------------------------------------------------------------------------
    def _to_xy(self, lon, lat):
        lat0, lon0, c = self._ll0
        return ((lon - lon0) * M_PER_DEG * c, (lat - lat0) * M_PER_DEG)

    def _dist(self, a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _runs(self, route):
        """Runway pavements a node route crosses, in order (consecutive pavement edges of one runway count once)."""
        out, prev = [], None
        for u, v in zip(route, route[1:]):
            pav = self.g[u][v]["pavement"]
            if pav and pav != prev:
                out.append(pav)
            prev = pav
        return out

    # -- main pass ---------------------------------------------------------------------------------------------------
    def tick(self, obs):
        self.obs, self.t = obs, obs["t"]
        self.live = {c["id"]: c for c in obs["clearances"]}
        self.ac = {}
        for a in obs["aircraft"]:
            if a["kind"] == "vehicle":
                continue
            a["x"], a["y"] = self._to_xy(a["lon"], a["lat"])
            self.ac[a["callsign"]] = a
        self._track()
        self.pic = self._picture()
        self._readbacks()
        if not self.started:
            self.started = True
            self._set_flow(self.flow, "Start of the day: arrivals on 25L and 26, departures on 25R (no arrivals on it).")
        self._flow()
        order = sorted(self.ac.values(), key=lambda a: (a.get("eta_s") or 0.0) if a["phase"] == "final" else 1e6)
        for a in order:
            (self._arrival if a["kind"] == "arrival" else self._departure)(a)
        self._ground()
        self._sequence()

    # -- ground conflicts: two aircraft converging on one node -------------------------------------------------------
    def _ahead(self, cs, a):
        """[(node, distance along the route)] for the remaining route ahead of a moving ground aircraft."""
        r = self._remaining(cs, a)
        nn = a.get("next_node")
        if not r or nn not in r:
            return []
        i = r.index(nn)
        out, d = [], self._dist((a["x"], a["y"]), self.xy[nn])
        out.append((nn, d))
        for u, v in zip(r[i:], r[i + 1:]):
            d += self.g[u][v]["length"]
            if d > CONVERGE_LOOK_M:
                break
            out.append((v, d))
        return out

    def _ground(self):
        held = {cs: m["held"] for cs, m in self.mem.items() if m.get("held") and cs in self.ac}
        for cs, h in held.items():   # resume once the other aircraft is past the node
            a, o = self.ac[cs], self.ac.get(h["by"])
            node = self.xy[h["node"]]
            if h.get("push"):
                past = o is None or o["phase"] != "pushback"
            else:
                past = o is None or (h["node"] not in self._remaining(h["by"], o)
                                 and self._dist((o["x"], o["y"]), node) > CONVERGE_CLEAR_M) or \
                (o["speed_mps"] < 0.3 and self._dist((o["x"], o["y"]), node) > o["length_m"] / 2 + CONVERGE_CLEAR_M)
            if past and not self._pending(cs, "hold_position"):
                if self._propose(a, "resume_taxi", {}, f"{h['by']} is past the junction: {cs} resumes taxi."):
                    self._m(cs).pop("held")
        movers = {cs: a for cs, a in self.ac.items() if a["phase"] in ("taxi_in", "taxi_out") and a["speed_mps"] > 1.0
                  and not self._m(cs).get("held")}
        ahead = {cs: self._ahead(cs, a) for cs, a in movers.items()}
        hot = self.pic["hot_nodes"]
        for cs, lst in ahead.items():
            n, d = next(((n, d) for n, d in lst if n in hot), (None, 0.0))
            if n and d >= CONVERGE_MIN_STOP_M and not self._live_action(cs, "cross_runway"):
                if self._propose(self.ac[cs], "hold_position", {}, f"{hot[n]} is pushing back into the taxilane "
                                                                    f"{d:.0f} m ahead of {cs}: {cs} holds position."):
                    self._m(cs)["held"] = {"by": hot[n], "node": n, "t": self.t, "push": True}
        names = sorted(c for c in movers if not self._m(c).get("held"))
        for i, p in enumerate(names):
            for q in names[i + 1:]:
                if self._m(p).get("held") or self._m(q).get("held"):
                    continue
                aq = dict(ahead[q])
                common = next(((n, d) for n, d in ahead[p] if n in aq), None)
                if common is None:
                    continue
                n, dp = common
                dq = aq[n]
                rp, rq = self._remaining(p, movers[p]), self._remaining(q, movers[q])
                ip, iq = rp.index(n), rq.index(n)
                if ip > 0 and iq > 0 and rp[ip - 1] == rq[iq - 1]:
                    continue   # same incoming edge: one follows the other (the pilots keep their distance)
                tp, tq = dp / max(movers[p]["speed_mps"], 2.0), dq / max(movers[q]["speed_mps"], 2.0)
                if abs(tp - tq) > CONVERGE_WINDOW_S:
                    continue
                cands = [(t_, x) for t_, x, d_ in ((tp, p, dp), (tq, q, dq))
                         if d_ >= CONVERGE_MIN_STOP_M and not self._live_action(x, "cross_runway")]
                if not cands:
                    continue   # both too close to stop short of the junction: the pilots' see-and-avoid takes it
                later = max(cands)[1]
                first = q if later == p else p
                a = self.ac[later]
                if self._propose(a, "hold_position", {}, f"{later} and {first} are converging on the same junction "
                                                         f"({dp:.0f} m / {dq:.0f} m away): {later} holds position until "
                                                         f"{first} is through."):
                    self._m(later)["held"] = {"by": first, "node": n, "t": self.t}

    def _live_action(self, cs, action):
        return any(c["callsign"] == cs and c["action"] == action for c in self.live.values())

    def _m(self, cs):
        return self.mem.setdefault(cs, {})

    def _track(self):
        """Phase transitions seen in the observation: touchdown and takeoff-roll times, forgotten clearances."""
        for cs, a in self.ac.items():
            m = self._m(cs)
            ph, prev = a["phase"], m.get("phase")
            if ph == "takeoff_roll" and prev != "takeoff_roll":
                self.last_roll[a["runway"]] = {"t": self.t, "callsign": cs, "cwt": a.get("wake"), "wtc": a.get("wtc")}
            if ph == "landing_roll" and prev != "landing_roll":
                m["td_t"] = self.t
                if m.get("exit") and a.get("gate"):
                    m["route"], m["ri"] = self.si.taxi_route(m["exit"], self.si.stand_nodes(a["gate"])["node"]), 0
            if ph == "go_around" and prev != "go_around":
                m.pop("land", None)
                self.emit(self.t, "alert", cs, {"level": "warning", "message": f"{cs} is going around."},
                          f"{cs} is going around; it rejoins the final on its own.")
            m["phase"] = ph
            p = m.get("pend")   # a taxi clearance counts only once the pilot has it (a route the sim could not apply is dropped)
            if p:
                c = self.live.get(p["cid"])
                if c and c["state"] == "acknowledged":
                    m.update(p["set"])
                    m.pop("pend")
                elif not c:
                    m.pop("pend")
                    self.emit(self.t, "alert", cs, {"level": "info", "message": "taxi clearance did not take effect"},
                              f"{cs}'s new taxi route could not be applied where it now is; planning again.")
        for cs in [c for c in self.pushbacks if c not in self.ac or self.ac[c]["phase"] not in ("at_gate", "pushback")]:
            self.pushbacks.pop(cs)

    def _remaining(self, cs, a):
        m = self._m(cs)
        route = m.get("route")
        if not route or a["phase"] in ("takeoff_roll", "airborne", "parked"):
            return []
        nn = a.get("next_node")
        if nn in route[m.get("ri", 0):]:
            m["ri"] = route.index(nn, m.get("ri", 0))
        return route[max(m.get("ri", 0) - 1, 0):]

    def _clear_at(self, cs, a):
        """Predicted time an aircraft on a runway is off it."""
        occ = (self.obs.get("level") or {}).get("effects", {}).get("occupancy_mult", 1.0)
        m = self._m(cs)
        ph = a["phase"]
        if ph in ("landing_roll", "runway_exit") and m.get("exit"):
            ex = self.xy[m["exit"]]
            d = self._dist((a["x"], a["y"]), ex)
            v = max(a["speed_mps"], 10.0)
            if ph == "landing_roll":
                return self.t + d / ((v + 10.0) / 2.0) * occ + (a["length_m"] / 2 + 70.0) / 5.0
            return self.t + max(a["length_m"] / 2 + 70.0 - d, 0.0) / 4.0 + 2.0
        if ph == "takeoff_roll":
            return self.t + 45.0 * occ * occ
        c = a.get("clearance") or {}
        if c.get("action") == "cross_runway":
            return (c.get("issued_t") or self.t) + 45.0
        return float("inf")

    def _picture(self):
        """The safety engine's view: observation + what this controller cleared and predicts."""
        occupants, clear_at = {}, {}
        for r in self.obs["runways"]:
            occ = [w["callsign"] for w in r["occupancy"] if w["kind"] == "occupied" and w["end_s"] >= self.t - 0.15]
            occupants[r["id"]] = sorted(set(occ))
            for cs in occ:
                if cs in self.ac:
                    clear_at[cs] = self._clear_at(cs, self.ac[cs])
        rc = []
        for c in self.live.values():
            if c["action"] in safety.RUNWAY_ACTIONS and c["id"] in self.expected:
                cs = c["callsign"]
                pav = self._m(cs).get("pav_of", {}).get(c["id"])
                if pav and cs in self.ac:
                    rc.append({"callsign": cs, "action": c["action"], "pavement": pav, "t": c["issued_t"]})
                    if cs not in clear_at:
                        a = self.ac[cs]
                        clear_at[cs] = (self.t + (a.get("eta_s") or 0) + ROLLOUT_S if c["action"] == "cleared_to_land"
                                        else self.t + 75.0 if c["action"] != "cross_runway" else c["issued_t"] + 45.0)
        for cs, a in self.ac.items():   # landing clearance already acknowledged (no longer "live" once it took effect)
            if a["phase"] == "final" and a.get("landing_cleared") and not any(x["callsign"] == cs for x in rc):
                rc.append({"callsign": cs, "action": "cleared_to_land", "pavement": a["runway"], "t": self.t})
        routes, parked = {}, {}
        for cs, a in self.ac.items():
            r = self._remaining(cs, a)
            if r:
                routes[cs] = r
            elif a["phase"] in ("taxi_out", "taxi_in", "hold_short") and a["speed_mps"] < 0.3 and a.get("next_node"):
                parked[cs] = a["next_node"]
        wx = self.obs["weather"]
        lvl = self.obs.get("level") or {}
        eff = lvl.get("effects") or {}
        rain = (wx.get("vis_sm") or 10.0) < safety.RAIN_VIS_SM or eff.get("occupancy_mult", 1.0) > 1.0
        live_actions = {}
        for c in self.live.values():
            live_actions.setdefault(c["callsign"], set()).add(c["action"])
        return {"t": self.t, "aircraft": self.ac, "live_actions": live_actions, "occupants": occupants, "clear_at": clear_at, "runway_clearances": rc,
                "last_roll": self.last_roll, "routes": routes, "parked_on": parked,
                "pushbacks": self.pushbacks, "hot_nodes": self._hot_nodes(), "tailwind": {e: self._tailwind(e) for e in self.ends},
                "tailwind_limit": lvl.get("tailwind_limit_kt") or safety.TAILWIND_LIMIT_KT, "rain": rain,
                "rollout_s": ROLLOUT_S * eff.get("occupancy_mult", 1.0)}

    def _hot_nodes(self):
        """Lane nodes a pushback may be moving into now: from its earliest likely push movement until it completes
        (the pushing aircraft does not give way; the sim's pushback averages PUSHBACK_MEAN_S, sigma 10 %)."""
        out = {}
        for cs, p in self.pushbacks.items():
            a = self.ac.get(cs)
            if a and a["phase"] == "pushback" and (a["speed_mps"] > 0.05 or self.t >= p["t"] + PUSHBACK_EARLIEST_S - p["move_s"]):
                for n in self._near(p["lane"]):
                    out[n] = cs
        return out

    def _near(self, node):
        """Graph nodes within PUSH_SWEEP_M of a node (cached): where a pushing aircraft's body can reach."""
        if node not in self._near_cache:
            x = self.xy[node]
            self._near_cache[node] = [n for n, p in self.xy.items() if self._dist(p, x) < PUSH_SWEEP_M]
        return self._near_cache[node]

    def _tailwind(self, end):
        wx = self.obs["weather"]
        if not wx.get("wind_kt"):
            return 0.0
        return -float(wx["wind_kt"]) * math.cos(math.radians(float(wx.get("wind_dir") or 0) - self.ends[end]["heading"]))

    # -- proposals ---------------------------------------------------------------------------------------------------
    def _propose(self, a, action, params, reason, end=None, pavement=None, route=None, extra=None, decision=None):
        """proposal -> safety_check -> issue -> clearance. Returns the issue() result, or None if rejected / failed."""
        cs = a["callsign"]
        prop = dict(extra or {}, callsign=cs, action=action, end=end, pavement=pavement, route=route)
        ok, rule, why = safety.check(self.pic, prop)
        key = (cs, action)
        data = {"action": action, "params": params}
        if decision:
            data["decision"] = decision
        if not ok:
            if self.rejected.get(key) != rule:
                self.rejected[key] = rule
                self.emit(self.t, "proposal", cs, data, reason)
                self.emit(self.t, "safety_check", cs, {"action": action, "approved": False, "rule": rule}, why)
            return None
        self.rejected.pop(key, None)
        self.emit(self.t, "proposal", cs, data, reason)
        self.emit(self.t, "safety_check", cs, {"action": action, "approved": True, "rule": rule}, why)
        res = self.si.issue({"callsign": cs, "action": action, "params": params})
        if not res.get("ok"):
            self.emit(self.t, "alert", cs, {"level": "warning", "message": res.get("error"), "action": action},
                      f"The simulator refused {action} for {cs}: {res.get('error')}")
            return None
        exp_rwy = params.get("runway") if action in safety.RUNWAY_ACTIONS + ("hold_short",) else None
        if action in safety.RUNWAY_ACTIONS and exp_rwy in self.ends:
            pass
        elif action in ("cross_runway", "hold_short"):
            exp_rwy = self.si.runway_label(pavement)
        if action == "taxi_via":
            if params.get("to") in self.ends:
                exp_rwy = params["to"]
            else:
                runs = self._runs(route or [])
                exp_rwy = self.si.runway_label(runs[0]) if runs else None
        self.expected[res["clearance_id"]] = (cs, action, exp_rwy)
        if pavement:
            self._m(cs).setdefault("pav_of", {})[res["clearance_id"]] = pavement
        self.emit(self.t, "clearance", cs, {"clearance_id": res["clearance_id"], "action": action, "params": params,
                                            "text": res["text"]}, res["text"])
        return res

    def _pending(self, cs, action):
        """A clearance of this action sent to this aircraft is still in force or awaiting its readback. Runway clearances
        count while live (re-sending one would amend it); taxi instructions only until read back and visible."""
        for c in self.live.values():
            if c["callsign"] == cs and c["action"] == action and c["id"] in self.expected:
                if action in safety.RUNWAY_ACTIONS or c["state"] == "issued" or self.t - c["issued_t"] < 4.0:
                    return True
        return False

    # -- readbacks ---------------------------------------------------------------------------------------------------
    def _readbacks(self):
        for rb in self.si.get_readbacks():
            exp = self.expected.get(rb["clearance_id"])
            heard = rb.get("parsed") or {}
            if exp is None:
                ok, why = True, "Readback for a clearance this controller did not issue; accepted."
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

    # -- runway flow (wind) ------------------------------------------------------------------------------------------
    def _config(self, flow):
        arr = ["25L", "26"] if flow == "west" else ["07R", "08"]
        return arr, DEPARTURE_RUNWAYS[flow]

    def _set_flow(self, flow, reason, decision=None):
        arr, dep = self._config(flow)
        prop = {"callsign": None, "action": "set_runway_config", "ends": arr + dep}
        ok, rule, why = safety.check(self.pic, prop)
        data = {"action": "set_runway_config", "params": {"arrival_runways": arr, "departure_runways": dep}}
        if decision:
            data["decision"] = decision
        self.emit(self.t, "proposal", None, data, reason)
        self.emit(self.t, "safety_check", None, {"action": "set_runway_config", "approved": ok, "rule": rule}, why)
        if ok:
            self.si.set_runway_config(arr, dep)
            self.flow = flow
        return ok

    def _flow(self):
        cur = self._config(self.flow)
        worst = max(self._tailwind(e) for e in cur[0] + cur[1])
        if worst <= FLOW_SWITCH_TAILWIND_KT:
            return
        other = "east" if self.flow == "west" else "west"
        new = self._config(other)
        after = max(self._tailwind(e) for e in new[0] + new[1])
        if after >= worst:
            return
        wx = self.obs["weather"]
        options = [{"id": "switch", "label": f"turn the airport round to {other} flow", "tailwind_kt": round(after, 1)},
                   {"id": "keep", "label": f"keep {self.flow} flow", "tailwind_kt": round(worst, 1)}]
        decision = {"kind": "runway_flow", "options": options, "default": "switch"}
        decision["choice"] = self.choose(decision)
        if decision["choice"] != "switch":
            return
        self._set_flow(other, f"Wind {wx.get('wind_dir')}/{wx.get('wind_kt')} kt gives {worst:.1f} kt tailwind on the "
                              f"active runways ({after:.1f} kt after the change): turning the airport round before "
                              f"pilots start refusing.", decision)

    # -- arrivals ----------------------------------------------------------------------------------------------------
    def _arrival(self, a):
        cs, m, ph = a["callsign"], self._m(a["callsign"]), a["phase"]
        if ph == "final":
            self._final(a, m)
        elif ph == "taxi_in" and self._at_line(a):
            self._cross(a)
        elif ph == "taxi_in" and not m.get("taxied") and a["speed_mps"] < 0.3 and a.get("next_node") and a.get("gate"):
            self._taxi_in(a, m)

    def _taxi_in(self, a, m):
        cs = a["callsign"]
        if self._pending(cs, "taxi_via"):
            return
        stand = self.si.stand_nodes(a["gate"])["node"]
        route = self._route(cs, a["next_node"], stand)
        if not route:
            self._no_route(cs)
            return
        length = sum(self.g[u][v]["length"] for u, v in zip(route, route[1:]))
        res = self._propose(a, "taxi_via", {"route": route, "to": a["gate"]},
                            f"{cs} vacated the runway: taxi to gate {a['gate']} ({length:.0f} m), clear of "
                            f"opposite-direction traffic.", route=route)
        if res:
            m["pend"] = {"cid": res["clearance_id"], "set": {"route": route, "ri": 0, "taxied": True}}

    def _no_route(self, cs):
        key = (cs, "taxi_via")
        if self.rejected.get(key) != "no_route":
            self.rejected[key] = "no_route"
            self.emit(self.t, "alert", cs, {"level": "info", "message": "no conflict-free taxi route yet"},
                      f"{cs} waits: every taxi route meets other traffic head-on.")

    def _final(self, a, m):
        cs, end = a["callsign"], a.get("arrival_end")
        if not end:
            return
        eta = a.get("eta_s") or 999.0
        arr_ends = self.obs["runway_config"]["arrival_runways"]
        limit = self.pic["tailwind_limit"]
        if end not in arr_ends and not a.get("landing_cleared") and not self._pending(cs, "contact_tower_or_ground") and (
                eta > FLOW_FINISH_ETA_S or self._tailwind(end) > limit - 1.0):
            new = self.ends[end]["runway"]
            new = next((e for e in arr_ends if self.ends[e]["runway"] == new), None)
            if new:
                self._propose(a, "contact_tower_or_ground", {"facility": "tower", "frequency": FREQ["tower"], "runway": new},
                              f"Runway flow changed: {cs} re-sequenced to runway {new} instead of {end}.")
            return
        if a.get("landing_cleared") or self._pending(cs, "cleared_to_land"):
            if a.get("landing_cleared") and eta < SHORT_FINAL_CHECK_S and not m.get("ga"):
                late = [o for o in self.pic["occupants"].get(a["runway"], [])
                        if o != cs and self.pic["clear_at"].get(o, float("inf")) > self.t + eta]
                if late:
                    m["ga"] = True
                    self._propose(a, "go_around", {}, f"{cs} is {eta:.0f} s from touchdown and {', '.join(late)} is "
                                                      f"still on runway {a['runway']}: go around.")
            return
        if eta > LAND_CLEAR_ETA_S:
            return
        if not m.get("exit"):
            m["exit"] = self.si.choose_exit(end, a["type"], a["gate"]) if a.get("gate") else None
        res = self._propose(a, "cleared_to_land", {"runway": end, **({"exit": m["exit"]} if m.get("exit") else {})},
                            f"{cs} is {eta:.0f} s from runway {end}; clearing it to land so it never has to go around.",
                            end=end, pavement=a["runway"], extra={"eta": eta})
        if res:
            m["land"] = res["clearance_id"]
            m.pop("ga", None)
        elif eta <= LAND_DECIDE_ETA_S and not m.get("ga"):
            m["ga"] = True
            self._propose(a, "go_around", {}, f"{cs} cannot be cleared to land on {end} in time: the runway will not be "
                                              f"clear; a go-around is safer than landing on an occupied runway.")

    def _cross(self, a):
        cs = a["callsign"]
        pav = a["hold"]["runway"]
        if self._pending(cs, "cross_runway"):
            return
        self._propose(a, "cross_runway", {"runway": self.si.runway_label(pav)},
                      f"{cs} is holding short of {pav}; crossing when the runway is clear and nothing is due on it.",
                      end=None, pavement=pav)

    # -- departures --------------------------------------------------------------------------------------------------
    def _departure(self, a):
        cs, m, ph = a["callsign"], self._m(a["callsign"]), a["phase"]
        if ph == "at_gate" and a.get("ready") and not m.get("push"):
            lane = self.si.stand_nodes(a["gate"])["lane_node"]
            res = self._propose(a, "pushback_approved", {}, f"{cs} is ready at gate {a['gate']}: pushback approved.",
                                extra={"lane_xy": self.xy[lane]})
            if res:
                m["push"] = True
                stand = self.si.stand_nodes(a["gate"])["node"]
                self.pushbacks[cs] = {"xy": self.xy[lane], "t": self.t, "lane": lane,
                                      "move_s": self._dist(self.xy[stand], self.xy[lane]) / PUSH_SPEED_MPS}
            return
        dep_ends = self.obs["runway_config"]["departure_runways"]
        if ph == "taxi_out" and not m.get("route") and a["speed_mps"] < 0.3 and a.get("next_node"):
            self._taxi_out(a, m, a["next_node"])
            return
        at_line = self._at_line(a)
        dep_pav = self.ends[m["end"]]["runway"] if m.get("end") else None
        if ph in ("taxi_out", "hold_short") and m.get("end") and m["end"] not in dep_ends and m.get("route") \
                and not self._pending(cs, "taxi_via") and not m.get("held") and not m.get("pend") \
                and not (at_line and a["hold"]["runway"] == dep_pav
                         and self._tailwind(m["end"]) <= self.pic["tailwind_limit"] - 2):
            if a["speed_mps"] < 0.3 and a.get("prev_node"):
                (not a.get("hold") and a["next_node"] not in self.runway_nodes and self._taxi_out(a, m, a["next_node"], why="Runway flow changed: ", first=True)) or \
                    self._taxi_out(a, m, a["prev_node"], why="Runway flow changed (turning round): ", turn=True)
            elif a["speed_mps"] >= 0.3 and a.get("next_node") and a["next_node"] not in self.runway_nodes:
                lead = self._lead_in(cs, a)
                if lead:
                    self._taxi_out(a, m, lead[-1], why="Runway flow changed: ", lead=lead[:-1])
            return
        if at_line:
            if a["hold"]["runway"] == dep_pav and m.get("entry") and \
                    self._dist((a["x"], a["y"]), self.xy[m["entry"]]) < a["length_m"] / 2 + ENTRY_NEAR_M:
                self._takeoff(a, m)
            else:
                self._cross(a)

    @staticmethod
    def _at_line(a):
        """Stopped at a runway hold line itself (not queued behind another aircraft)."""
        return bool(a.get("hold")) and a["speed_mps"] < 0.3 and str(a.get("blocked_by") or "").startswith("runway:")

    def _route(self, cs, src, dst, avoid_pav=None, avoid_nodes=()):
        """Dijkstra over the taxi graph: no runway centreline edges, never against another aircraft's route (head-on),
        never through an aircraft stopped without a route, a heavy penalty per runway crossing."""
        against = {(v, u) for o, r in self.pic["routes"].items() if o != cs for u, v in zip(r, r[1:])}
        shared = {(u, v) for o, r in self.pic["routes"].items() if o != cs for u, v in zip(r, r[1:])}
        blocked = {n for o, n in self.pic["parked_on"].items() if o != cs} | set(self.pic["hot_nodes"]) | set(avoid_nodes)

        def w(u, v, d):
            if d["kind"] == "runway" or (u, v) in against or (v in blocked and v not in (dst, src)) or \
                    (avoid_pav and d["pavement"] == avoid_pav and v != dst):
                return None
            return d["length"] + (CROSSING_PENALTY_M if d["pavement"] else 0.0) + (SHARED_EDGE_PENALTY_M if (u, v) in shared else 0.0)
        try:
            return nx.dijkstra_path(self.g, src, dst, weight=w)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def _lead_in(self, cs, a):
        """The next nodes of a moving aircraft's current route, LEAD_IN_M or more: a new route must start with them because
        the aircraft passes its observed next node before the readback is accepted."""
        r = self._remaining(cs, a)
        nn = a.get("next_node")
        if nn not in r:
            return None
        out, d = [], self._dist((a["x"], a["y"]), self.xy[nn])
        for u, v in zip(r[r.index(nn):], r[r.index(nn) + 1:]):
            out.append(u)
            if d >= LEAD_IN_M:
                break
            d += self.g[u][v]["length"]
        else:
            return None
        return out if not any(n in self.runway_nodes for n in out) else None

    def _taxi_out(self, a, m, start, why="", first=False, turn=False, lead=()):
        """Route a departure to the best active departure runway. first: only try (no alert if none); turn: the route
        starts behind the aircraft (it turns round where it stands). Returns True if a clearance went out."""
        cs = a["callsign"]
        if self._pending(cs, "taxi_via"):
            return False
        best = None
        for end in self.obs["runway_config"]["departure_runways"]:
            entry = self.si.choose_entry(end, start, a["type"])
            if not entry:
                continue
            route = self._route(cs, start, entry, avoid_pav=self.ends[end]["runway"], avoid_nodes=set(lead))
            if not route:
                continue
            route = list(lead) + route
            length = sum(self.g[u][v]["length"] for u, v in zip(route, route[1:]))
            score = length + CROSSING_PENALTY_M * len(self._runs(route[:-1]))
            if best is None or score < best[0]:
                best = (score, end, entry, route, length)
        if best is None:
            if not first:
                self._no_route(cs)
            return False
        _, end, entry, route, length = best
        res = self._propose(a, "taxi_via", {"route": route, "to": end},
                            f"{why}Taxi {cs} to runway {end} ({length:.0f} m), clear of opposite-direction traffic.",
                            end=end, route=route, extra={"turn_round": turn})
        if res:
            m["pend"] = {"cid": res["clearance_id"], "set": {"end": end, "entry": entry, "route": route, "ri": 0}}
        return bool(res)

    def _takeoff(self, a, m):
        cs, end = a["callsign"], m["end"]
        pav = self.ends[end]["runway"]
        if self._pending(cs, "cleared_for_takeoff") or a.get("takeoff_cleared"):
            return
        waiting = [o for o in self.ac.values() if o["callsign"] != cs and self._at_line(o) and o["hold"]["runway"] == pav
                   and (o.get("stopped_s") or 0) > CROSSER_PRIORITY_S
                   and not (o["kind"] == "departure" and self._m(o["callsign"]).get("end")
                            and self.ends[self._m(o["callsign"])["end"]]["runway"] == pav)]
        if waiting:
            return   # a crossing has waited long enough: it goes first, then the next departure
        queue = [o for o in self.ac.values() if o["kind"] == "departure" and self._at_line(o) and o["hold"]["runway"] == pav
                 and self._m(o["callsign"]).get("end") and self.ends[self._m(o["callsign"])["end"]]["runway"] == pav]
        queue.sort(key=lambda o: (-(o.get("stopped_s") or 0), o["callsign"]))
        decision = None
        if len(queue) > 1:
            options = [{"id": o["callsign"], "label": f"{o['callsign']} ({o['type']}, wake {o.get('wake')}) waiting "
                                                      f"{o.get('stopped_s', 0):.0f} s"} for o in queue]
            decision = {"kind": "departure_gap", "runway": end, "options": options, "default": queue[0]["callsign"]}
            decision["choice"] = self.choose(decision)
            if decision["choice"] != cs:
                return
        self._propose(a, "cleared_for_takeoff", {"runway": end},
                      f"{cs} is first in line at runway {end}; cleared for takeoff when the runway, the arrivals and "
                      f"the wake spacing allow.", end=end, pavement=pav, decision=decision)

    # -- sequence events -----------------------------------------------------------------------------------------------
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
