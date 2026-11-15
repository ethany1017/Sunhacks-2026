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
SHORT_FINAL_CHECK_S = 20.0      # cleared arrival this close with the runway still blocked: go around (last resort)
ROLLOUT_S = 72.0                # typical landing occupancy, dry (docs/LEVELS.md measures 69-77 s), times occupancy_mult
FLOW_SWITCH_TAILWIND_KT = 4.0   # turn the airport round once the active direction has more tailwind than this
OLD_FLOW_DEPART_S = 60.0        # a departure already at its old runway may still go this soon after the flow turns
FLOW_FINISH_ETA_S = 150.0       # on a flow change an arrival this close finishes on the old direction while legal
CROSSING_PENALTY_M = 2500.0     # taxi routing: per runway-pavement edge (never trade a crossing for a short detour)
SHARED_EDGE_PENALTY_M = 60.0    # taxi routing: an edge another aircraft also uses (same direction)
CROSSER_PRIORITY_S = 20.0       # a crossing waiting this long goes before the next takeoff on that runway
PUSH_SPEED_MPS = 1.1            # pushback speed (tug), so the push movement lasts lead length / 1.1 s
PUSHBACK_EARLIEST_S = 282.0 * 0.75 - 15.0   # NASA NTRS 20190027149: pushback 4.72 min mean; 2.5 sigma early, minus a margin
PUSH_SWEEP_M = 45.0             # a pushed-back aircraft (centre on the lane node) reaches about this far
LEAD_IN_M = 90.0                # a re-route for a moving aircraft keeps this much of its current route first
CONVERGE_LOOK_M = 250.0         # ground: look this far ahead along each route for a shared junction
CONFLICT_MARGIN_M = 20.0        # two route points closer than the half wingspans + this are one conflict point
CONVERGE_WINDOW_S = 20.0        # two aircraft reaching one junction closer in time than this: one holds
CONVERGE_TIGHT_S = 10.0         # ...if a hold would stop in the other's path, hold only when this close in time
CONVERGE_MIN_STOP_M = 80.0      # ...if it is still this far from the junction (readback + braking distance)
BODY_MIN_STOP_M = 40.0          # stop for a stopped fuselage over a junction only if it can still stop short of it
SEE_AVOID_M = 15.0              # the pilots stop for any aircraft this close (beyond half lengths) ahead (sim README)
SIM_CORRIDOR_M = 30.0           # the pilots stop for a stopped aircraft this close to their path (sim README)
HOLD_REACT_S = 4.0              # hold_position: readback delay before the pilots brake
HOLD_DECEL_MPS2 = 0.5           # assumed (gentle) taxi braking
UNJAM_NO_RUNWAY_M = 110.0       # an unjam re-route reaches runway pavement no sooner than half a length + this (hold offset + margin)
BODY_REROUTE_S = 90.0           # held this long for a stopped aircraft: look for a way round it
UNJAM_AFTER_S = 15.0            # mutual block this long: re-route one of the pair
IN_TRAIL_COS = 0.77             # within ~40 deg of the same direction: following, not crossing
BODY_MARGIN_M = 5.0             # extra clearance past the two half spans when passing a stopped aircraft
CONVERGE_CLEAR_M = 70.0
START_DELAY_S = 5.0             # readback plus spool-up before a stopped aircraft moves
TAXI_AVG_MPS = 5.0              # average speed from a standing start over the first few hundred metres
HOLD_MAX_S = 60.0               # a hold for an aircraft that has itself stopped ends after this         # resume once the other aircraft is this far past the junction and from the holder
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
        self.flows = self._derive_flows()
        self.flow = next(iter(self.flows))
        self.flow_t = 0.0
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
            self.flow = min(self.flows, key=lambda f: max(self._tailwind(e) for e in sum(self._config(f), [])))
            arr, dep = self._config(self.flow)
            self._set_flow(self.flow, f"Start of the day, least tailwind: arrivals on {' and '.join(arr)}, departures "
                                      f"on {' and '.join(dep)} (no arrivals on it).")
        self._flow()
        order = sorted(self.ac.values(), key=lambda a: (a.get("eta_s") or 0.0) if a["phase"] == "final" else 1e6)
        for a in order:
            (self._arrival if a["kind"] == "arrival" else self._departure)(a)
        self._ground()
        self._unjam()
        self._sequence()

    # -- ground conflicts: two aircraft converging on one node -------------------------------------------------------
    def _body_hit(self, cs, a, b, min_d=BODY_MIN_STOP_M):
        """Next route node of the first point on a's route ahead (beyond min_d) whose wings would reach the stopped b's
        fuselage line: the half spans of both plus a margin."""
        h = math.radians(b["heading_deg"])
        ux, uy = math.sin(h) * b["length_m"] / 2, math.cos(h) * b["length_m"] / 2
        lim = a["wingspan_m"] / 2 + b["wingspan_m"] / 2 + BODY_MARGIN_M
        bx, by = math.sin(h), math.cos(h)
        for x, y, d, rx, ry, n in self._route_pts(cs, a, CONVERGE_LOOK_M):
            if d < min_d or rx * bx + ry * by > IN_TRAIL_COS:
                continue   # (in trail with b there: the pilots queue behind it)
            px, py = x - b["x"], y - b["y"]
            k = max(-1.0, min(1.0, (px * ux + py * uy) / max(ux * ux + uy * uy, 1e-6)))
            if math.hypot(px - k * ux, py - k * uy) < lim:
                return n
        return None

    def _route_pts(self, cs, a, maxd):
        """Points every 8 m along a's remaining route from where it is, up to maxd: [(x, y, d, ux, uy, next node)]."""
        r = self._remaining(cs, a)
        nn = a.get("next_node")
        if nn not in r:
            return []
        out, prev, d = [], (a["x"], a["y"]), 0.0
        for n in r[r.index(nn):]:
            q = self.xy[n]
            seg = self._dist(prev, q)
            if seg > 1e-6:
                ux, uy = (q[0] - prev[0]) / seg, (q[1] - prev[1]) / seg
                k = 0.0
                while k < seg:
                    out.append((prev[0] + ux * k, prev[1] + uy * k, d + k, ux, uy, n))
                    k += 8.0
                d += seg
            if d > maxd:
                break
            prev = q
        return out

    def _stops_clear(self, cs, other):
        """Would cs, told to hold now, stop more than the pilots' path corridor away from other's route ahead?
        (The simulator's pilots stop for any stopped aircraft within 30 m of their path: holding cs there would block
        the very aircraft it waits for.)"""
        a, o = self.ac[cs], self.ac[other]
        v = a["speed_mps"]
        stop = v * HOLD_REACT_S + v * v / (2 * HOLD_DECEL_MPS2)
        mine = self._route_pts(cs, a, stop + 8.0)
        x, y = (mine[-1][0], mine[-1][1]) if mine else (a["x"], a["y"])
        theirs = self._route_pts(other, o, CONVERGE_LOOK_M + 100.0)
        lim = SIM_CORRIDOR_M + a["length_m"] / 2 + BODY_MARGIN_M
        return all(math.hypot(px - x, py - y) > lim for px, py, *_ in theirs)

    def _unjam(self):
        """Two taxiing aircraft stopped, each blocked by the other (the pilots wait for each other for ever): re-route one
        of them on a route that keeps clear of the other."""
        for cs, a in sorted(self.ac.items()):
            o = a.get("blocked_by")
            b = self.ac.get(o) if o else None
            m = self._m(cs)
            if b is None or a["phase"] not in ("taxi_in", "taxi_out") or a["speed_mps"] > 0.3 or \
                    (a.get("stopped_s") or 0) < UNJAM_AFTER_S or b.get("blocked_by") != cs or b["speed_mps"] > 0.3 or \
                    m.get("unjam") == (a.get("next_node"), o) or not m.get("route") or self._pending(cs, "taxi_via") or \
                    self._live_action(cs, "cross_runway"):
                continue
            m["unjam"] = (a.get("next_node"), o)
            if self._route_round(cs, a, o, f"{cs} and {o} are stopped nose to nose, each waiting for the other: "
                                           f"{cs} takes a route round {o}."):
                return

    def _route_round(self, cs, a, o, reason):
        """Re-route a stopped taxiing aircraft to its destination on a route that keeps clear of aircraft o."""
        m, b = self._m(cs), self.ac[o]
        if not m.get("route") or self._pending(cs, "taxi_via") or self._live_action(cs, "cross_runway"):
            return False
        near = {n for n, (x, y) in self.xy.items()
                if math.hypot(x - b["x"], y - b["y"]) < (a["length_m"] + b["length_m"]) / 2 + SEE_AVOID_M + SIM_CORRIDOR_M}
        dst, end = m["route"][-1], m.get("end")
        for start, turn in ((a.get("next_node"), False), (a.get("prev_node"), True)):
            if not start or start in near:
                continue
            route = self._route(cs, start, dst, avoid_pav=self.ends[end]["runway"] if end else None, avoid_nodes=near)
            if not route or self._pavement_within(route, (a["x"], a["y"]), a["length_m"] / 2 + UNJAM_NO_RUNWAY_M):
                continue   # never start a re-route at a runway: the pilots would be inside its hold point already
            to = end if a["phase"] == "taxi_out" and end else a.get("gate")
            res = self._propose(a, "taxi_via", {"route": route, "to": to}, reason,
                                end=end if a["phase"] == "taxi_out" else None, route=route,
                                extra={"turn_round": turn, "path": self._path(route, (a["x"], a["y"]), 0.0)})
            if res:
                m.pop("held", None)
                m["pend"] = {"cid": res["clearance_id"], "set": {"route": route, "ri": 0}}
                return True
        return False

    def _pavement_within(self, route, xy, dmax):
        """Does the route reach runway pavement within dmax of xy (measured along it)?"""
        d = self._dist(xy, self.xy[route[0]])
        for u, v in zip(route, route[1:]):
            if d > dmax:
                return False
            if self.g[u][v]["pavement"]:
                return True
            d += self.g[u][v]["length"]
        return False

    def _hdg_into(self, cs, a, n):
        """Bearing of a's route leg into node n (from where it is, for its next node)."""
        r = self._remaining(cs, a)
        i = r.index(n) if n in r else 0
        u = (a["x"], a["y"]) if n == a.get("next_node") or i == 0 else self.xy[r[i - 1]]
        v = self.xy[n]
        return math.degrees(math.atan2(v[0] - u[0], v[1] - u[1])) % 360 if self._dist(u, v) > 0.5 else a["heading_deg"]

    def _waits_for(self, a, b):
        """Is a (transitively) held for b?"""
        seen = set()
        while a and a not in seen:
            seen.add(a)
            a = (self._m(a).get("held") or {}).get("by")
            if a == b:
                return True
        return False

    def _freeze(self, cs, a, b, min_d=BODY_MIN_STOP_M):
        """First node ahead of a beyond min_d from which a and the stopped b would be in each other's see-and-avoid
        cone (within half lengths + 15 m, inside +-60 deg ahead): pilots then wait for each other for ever."""
        lim = (a["length_m"] + b["length_m"]) / 2 + SEE_AVOID_M + BODY_MARGIN_M
        bh = math.radians(b["heading_deg"])
        bf = (math.sin(bh), math.cos(bh))
        r = self._remaining(cs, a)
        nn = a.get("next_node")
        if nn not in r:
            return None
        prev, d = (a["x"], a["y"]), 0.0
        for n in r[r.index(nn):]:
            q = self.xy[n]
            seg = self._dist(prev, q)
            k = 0.0
            while k <= seg:
                x, y = prev[0] + (q[0] - prev[0]) * k / max(seg, 1e-6), prev[1] + (q[1] - prev[1]) * k / max(seg, 1e-6)
                rx, ry = b["x"] - x, b["y"] - y
                dist = math.hypot(rx, ry)
                if d + k >= min_d and dist < lim and seg > 0 and \
                        (rx * (q[0] - prev[0]) + ry * (q[1] - prev[1])) / seg > 0.5 * dist and \
                        -(rx * bf[0] + ry * bf[1]) > 0.5 * dist:
                    return n
                k += 8.0
            d += seg
            if d > CONVERGE_LOOK_M:
                return None
            prev = q
        return None

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
            elif h.get("body"):   # no timeout (waiting beats taxiing into a parked fuselage), but try a way round
                past = o is None or o["phase"] not in safety.GROUND_PHASES or not (
                    self._body_hit(cs, a, o, 0.0) or self._freeze(cs, a, o, 0.0))
                if not past and self.t - h["t"] > BODY_REROUTE_S and not h.get("tried") and a["speed_mps"] < 0.3:
                    h["tried"] = True
                    self._route_round(cs, a, h["by"], f"{h['by']} has been stopped in {cs}'s way for "
                                                      f"{self.t - h['t']:.0f} s: {cs} takes a route round it.")
                    continue
            else:
                past = o is None or (h["node"] not in self._remaining(h["by"], o)
                                 and self._dist((o["x"], o["y"]), node) > CONVERGE_CLEAR_M) or \
                (o["speed_mps"] < 0.3 and self._dist((o["x"], o["y"]), node) > o["length_m"] / 2 + CONVERGE_CLEAR_M)
            if not past and o is not None and o["speed_mps"] < 0.3 and self.t - h["t"] > HOLD_MAX_S:
                past = True   # the other aircraft is stopped too (waiting itself): do not wait for it forever
            if past and not self._pending(cs, "hold_position"):
                r = self._remaining(cs, a)
                path = self._path(r[r.index(a["next_node"]):], (a["x"], a["y"]), 0.0) if a.get("next_node") in r else []
                if self._propose(a, "resume_taxi", {}, f"{h['by']} is past the junction: {cs} resumes taxi.",
                                 route=r, extra={"path": path}):
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
        still = {cs: a for cs, a in self.ac.items() if a["phase"] in safety.GROUND_PHASES and a["speed_mps"] < 0.3}
        for cs, lst in ahead.items():   # a stopped aircraft whose fuselage sits over a junction on this route
            a = movers[cs]
            if self._m(cs).get("held") or self._live_action(cs, "cross_runway"):
                continue
            for o, b in still.items():
                if o == cs or self._waits_for(o, cs) or abs((a["heading_deg"] - b["heading_deg"] + 180) % 360 - 180) < 40:
                    continue   # stopped ahead in trail: the pilots' car-following stops behind it
                n = self._body_hit(cs, a, b) or self._freeze(cs, a, b)
                if n and self._propose(a, "hold_position", {}, f"{o} is stopped across the junction ahead of {cs}: "
                                                                f"{cs} holds position until it moves clear."):
                    self._m(cs)["held"] = {"by": o, "node": n, "t": self.t, "body": True}
                    break
        names = sorted(c for c in movers if not self._m(c).get("held"))
        for i, p in enumerate(names):
            for q in names[i + 1:]:
                if self._m(p).get("held") or self._m(q).get("held"):
                    continue
                ap_, aq_ = movers[p], movers[q]
                radius = (ap_["wingspan_m"] + aq_["wingspan_m"]) / 2 + CONFLICT_MARGIN_M
                hit = None
                for np_, dp in ahead[p]:
                    for nq_, dq in ahead[q]:
                        if self._dist(self.xy[np_], self.xy[nq_]) < radius:
                            hd = abs((self._hdg_into(p, ap_, np_) - self._hdg_into(q, aq_, nq_) + 180) % 360 - 180)
                            if hd < 40:
                                continue   # in trail there (the same way): the pilots keep their distance
                            hit = (np_, dp, nq_, dq)
                            break
                    if hit:
                        break
                if hit is None:
                    continue
                n_p, dp, n_q, dq = hit
                tp, tq = dp / max(movers[p]["speed_mps"], 2.0), dq / max(movers[q]["speed_mps"], 2.0)
                if abs(tp - tq) > CONVERGE_WINDOW_S:
                    continue
                cands = [(t_, x) for t_, x, d_ in ((tp, p, dp), (tq, q, dq))
                         if d_ >= CONVERGE_MIN_STOP_M and not self._live_action(x, "cross_runway")]
                if not cands:
                    continue   # both too close to stop short of the junction: the pilots' see-and-avoid takes it
                cands.sort(reverse=True)   # the later one holds, unless only the other would stop clear of its path
                later = next((x for _, x in cands if self._stops_clear(x, q if x == p else p)), None)
                if later is None:
                    if abs(tp - tq) > CONVERGE_TIGHT_S:
                        continue   # holding would block the first one's path, and it is through well before
                    later = cands[0][1]
                first = q if later == p else p
                if self._waits_for(first, later):
                    continue   # holding it would close a wait cycle
                a = self.ac[later]
                if self._propose(a, "hold_position", {}, f"{later} and {first} are converging on the same junction "
                                                         f"({dp:.0f} m / {dq:.0f} m away): {later} holds position until "
                                                         f"{first} is through."):
                    self._m(later)["held"] = {"by": first, "node": n_q if later == p else n_p, "t": self.t}

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
        c = a.get("clearance") or {}
        if a["speed_mps"] < 0.5 and str(a.get("blocked_by") or "").startswith("runway:") and \
                c.get("action") != "cross_runway":
            return float("inf")   # stopped at a hold point while still on the runway
        if ph in ("landing_roll", "runway_exit") and m.get("exit"):
            ex = self.xy[m["exit"]]
            d = self._dist((a["x"], a["y"]), ex)
            v = max(a["speed_mps"], 10.0)
            if ph == "landing_roll":
                return self.t + d / ((v + 10.0) / 2.0) * occ + (a["length_m"] / 2 + 70.0) / 5.0
            if a["speed_mps"] < 0.5 and a.get("blocked_by"):
                return float("inf")   # held up by traffic before it is clear of the runway
            rem = a["length_m"] / 2 + 70.0 - d
            if rem <= 0:
                return self.t + 8.0   # past where it should be clear yet still reported on the runway: long exit, don't trust
            return self.t + rem / min(max(a["speed_mps"], 1.0), 4.0) + 2.0
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
        paths = {}
        for cs, a in self.ac.items():   # where every moving (or just-cleared) ground aircraft will be, and when
            if a["phase"] not in ("taxi_in", "taxi_out", "hold_short", "runway_exit"):
                continue
            just = any(c["callsign"] == cs and c["action"] in ("taxi_via", "resume_taxi") and self.t - c["issued_t"] < 10
                       for c in self.live.values())
            if a["speed_mps"] < 0.3 and not just:
                continue
            r = self._remaining(cs, a)
            if a.get("next_node") in r:
                paths[cs] = self._path(r[r.index(a["next_node"]):], (a["x"], a["y"]), a["speed_mps"])
        wx = self.obs["weather"]
        lvl = self.obs.get("level") or {}
        eff = lvl.get("effects") or {}
        rain = (wx.get("vis_sm") or 10.0) < safety.RAIN_VIS_SM or eff.get("occupancy_mult", 1.0) > 1.0
        live_actions = {}
        for c in self.live.values():
            live_actions.setdefault(c["callsign"], set()).add(c["action"])
        return {"t": self.t, "aircraft": self.ac, "live_actions": live_actions, "occupants": occupants, "clear_at": clear_at, "runway_clearances": rc,
                "last_roll": self.last_roll, "routes": routes, "parked_on": parked, "paths": paths,
                "pushbacks": self.pushbacks, "hot_nodes": self._hot_nodes(), "tailwind": {e: self._tailwind(e) for e in self.ends},
                "tailwind_limit": lvl.get("tailwind_limit_kt") or safety.TAILWIND_LIMIT_KT, "rain": rain,
                "rollout_s": ROLLOUT_S * eff.get("occupancy_mult", 1.0)}

    def _path(self, nodes, xy, speed):
        """[{n, xy, eta, hdg}] along a node route ahead (CONVERGE_LOOK_M): eta at the speed held, or from rest."""
        out, d, prev = [], 0.0, xy
        for n in nodes:
            p = self.xy[n]
            seg = self._dist(prev, p)
            d += seg
            if d > CONVERGE_LOOK_M:
                break
            hdg = math.degrees(math.atan2(p[0] - prev[0], p[1] - prev[1])) % 360 if seg > 0.5 else (out[-1]["hdg"] if out else 0.0)
            eta = d / speed if speed > 3.0 else START_DELAY_S + d / TAXI_AVG_MPS
            out.append({"n": n, "xy": p, "eta": eta, "hdg": hdg})
            prev = p
        return out

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
        if action in ("taxi_via", "cross_runway"):
            self._m(cs).pop("held", None)   # a new movement clearance supersedes a hold_position
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
    def _derive_flows(self):
        """Runway flows from the airport layout: the runway ends pointing one way, and the other. In each flow the
        middle runway (by lateral position) takes departures and the others arrivals; one runway does both."""
        names = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
        h0 = next(iter(self.ends.values()))["heading"]
        flows = {}
        for same in (True, False):
            ends = [e for e, v in self.ends.items() if (abs((v["heading"] - h0 + 180) % 360 - 180) < 90) == same]
            if not ends:
                continue
            h = math.radians(self.ends[ends[0]]["heading"])
            lat = lambda e: (self.xy[self.ends[e]["threshold_node"]][0] * math.cos(h)
                             - self.xy[self.ends[e]["threshold_node"]][1] * math.sin(h))
            ends.sort(key=lat)
            dep = [ends[len(ends) // 2]] if len(ends) != 2 else [ends[1]]
            arr = [e for e in ends if e not in dep] or dep
            flows[names[int((self.ends[ends[0]]["heading"] + 22.5) % 360 // 45)]] = (arr, dep)
        return flows

    def _config(self, flow):
        arr, dep = self.flows[flow]
        return list(arr), list(dep)

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
            self.flow, self.flow_t = flow, self.t
        return ok

    def _flow(self):
        cur = self._config(self.flow)
        worst = max(self._tailwind(e) for e in cur[0] + cur[1])
        if worst <= FLOW_SWITCH_TAILWIND_KT:
            return
        other = next((f for f in self.flows if f != self.flow), None)
        if other is None:
            return
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
            if a["hold"]["runway"] != a.get("runway") or not (self._pending(cs, "taxi_via") or m.get("pend")
                                                               or self._reroute_off(a, m)):
                self._cross(a)
        elif ph in ("landing_roll", "runway_exit") and a["speed_mps"] < 0.3 and \
                str(a.get("blocked_by") or "").startswith("runway:"):
            # its exit route runs onto the same runway's pavement again (the sim would end a crossing clearance at once):
            # re-route it off that pavement if possible, else clear it across
            if not self._pending(cs, "taxi_via") and not m.get("pend") and not self._reroute_off(a, m):
                self._cross(a, a["runway"])
        elif ph in ("taxi_in", "landing_roll", "runway_exit") and not m.get("taxied") and a["speed_mps"] < 0.3 \
                and not a.get("blocked_by") and ((a.get("stopped_s") or 0) > 1.0 or ph != "taxi_in") and a.get("next_node") \
                and a.get("gate"):
            self._taxi_in(a, m)

    def _taxi_in(self, a, m):
        cs = a["callsign"]
        if self._pending(cs, "taxi_via"):
            return
        stand = self.si.stand_nodes(a["gate"])["node"]
        route = self._route(cs, a["next_node"], stand, avoid_pav=a.get("runway")) or \
            self._route(cs, a["next_node"], stand)
        if not route:
            self._no_route(cs)
            return
        length = sum(self.g[u][v]["length"] for u, v in zip(route, route[1:]))
        res = self._propose(a, "taxi_via", {"route": route, "to": a["gate"]},
                            f"{cs} vacated the runway: taxi to gate {a['gate']} ({length:.0f} m), clear of "
                            f"opposite-direction traffic.", route=route,
                            extra={"path": self._path(route, (a["x"], a["y"]), a["speed_mps"])})
        if res:
            m["pend"] = {"cid": res["clearance_id"], "set": {"route": route, "ri": 0, "taxied": True}}

    def _reroute_off(self, a, m):
        """Arrival stopped at a hold point of its own landing runway: a taxi route to the gate that stays off it."""
        cs, pav = a["callsign"], a["runway"]
        if m.get("reroute_tried") == a.get("next_node") or not a.get("gate"):
            return False
        m["reroute_tried"] = a.get("next_node")
        stand = self.si.stand_nodes(a["gate"])["node"]
        for start, turn in ((a.get("next_node"), False), (a.get("prev_node"), True)):
            if not start:
                continue
            route = self._route(cs, start, stand, avoid_pav=pav)
            if route and pav not in self._runs(route):
                res = self._propose(a, "taxi_via", {"route": route, "to": a["gate"]},
                                    f"{cs}'s exit route runs back onto runway {pav}: taxi to gate {a['gate']} on a route "
                                    f"that stays off it.", route=route,
                                    extra={"turn_round": turn, "path": self._path(route, (a["x"], a["y"]), 0.0)})
                if res:
                    m["pend"] = {"cid": res["clearance_id"], "set": {"route": route, "ri": 0, "taxied": True}}
                    return True
        return False

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
                        if o != cs and self.pic["clear_at"].get(o, float("inf")) > self.t + eta - safety.LANDING_BUFFER_S]
                if late:
                    m["ga"] = True
                    self._propose(a, "go_around", {}, f"{cs} is {eta:.0f} s from touchdown and {', '.join(late)} is "
                                                      f"still on runway {a['runway']}: go around.")
            return
        if eta > LAND_CLEAR_ETA_S:
            return
        if not m.get("exit") or m.get("exit_end") != end:
            m["exit"], m["exit_end"] = self._exit(end, a), end
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

    def _exit(self, end, a):
        """The pilots' preferred exit unless its taxi route to the gate runs back onto the landing runway's pavement
        (the simulator then ends a crossing clearance at once, see README): the best exit that stays off it."""
        if not a.get("gate"):
            return None
        pav, stand = self.ends[end]["runway"], self.si.stand_nodes(a["gate"])["node"]
        opts = self.si.exit_options(end, a["type"], a["gate"])
        for n in opts:
            runs = self._runs(self.si.taxi_route(n, stand) or [])
            if pav not in runs[1 if runs[:1] == [pav] else 0:]:   # the exit connector itself is on that pavement
                return n
        return opts[0] if opts else self.si.choose_exit(end, a["type"], a["gate"])

    def _cross(self, a, pav=None):
        cs = a["callsign"]
        pav = pav or a["hold"]["runway"]
        if self._pending(cs, "cross_runway") or self._m(cs).get("held"):
            return   # a held aircraft resumes first: a later resume would replace the crossing clearance
        self._propose(a, "cross_runway", {"runway": self.si.runway_label(pav)},
                      f"{cs} is holding short of {pav}; crossing when the runway is clear and nothing is due on it.",
                      end=None, pavement=pav)

    # -- departures --------------------------------------------------------------------------------------------------
    def _departure(self, a):
        cs, m, ph = a["callsign"], self._m(a["callsign"]), a["phase"]
        if ph == "at_gate" and a.get("ready") and not m.get("push"):
            sn = self.si.stand_nodes(a["gate"])
            lane = sn["lane_node"]
            res = self._propose(a, "pushback_approved", {}, f"{cs} is ready at gate {a['gate']}: pushback approved.",
                                extra={"lane_xy": self.xy[lane], "push_from": self.xy[sn["node"]],
                                       "push_to": self.xy[lane]})
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
                         and self._tailwind(m["end"]) <= self.pic["tailwind_limit"] - 2
                         and self.t - self.flow_t < OLD_FLOW_DEPART_S):   # finish the old flow briefly: the wind is still turning
            if a["speed_mps"] < 0.3 and a.get("prev_node"):
                (not a.get("hold") and a["next_node"] not in self.runway_nodes and self._taxi_out(a, m, a["next_node"], why="Runway flow changed: ", first=True)) or \
                    self._taxi_out(a, m, a["prev_node"], why="Runway flow changed (turning round): ", turn=True)
            elif a["speed_mps"] >= 0.3 and a.get("next_node") and a["next_node"] not in self.runway_nodes:
                lead = self._lead_in(cs, a)
                if lead:
                    self._taxi_out(a, m, lead[-1], why="Runway flow changed: ", lead=lead[:-1])
            return
        if at_line:
            if a["hold"]["runway"] == dep_pav:   # routes never cross their own departure runway: this is the entry
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
                            end=end, route=route,
                            extra={"turn_round": turn, "path": self._path(route, (a["x"], a["y"]), a["speed_mps"])})
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
