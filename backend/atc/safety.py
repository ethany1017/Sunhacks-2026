"""Agent safety engine: deterministic rules that approve or reject every proposed instruction before it is sent.

Each rule is `rule(pic, prop) -> (ok, rule_name, reason)`. `pic` is the controller's picture of the airport this tick
(built by the planner from simif observations and its own record of what it cleared); `prop` is the proposal
`{callsign, action, end, pavement, route, ...}`. `check()` runs the rules for the action; the first rejection wins.
The engine only stops the agent from SENDING unsafe clearances. The Referee (in the simulator) judges independently;
these margins sit above its limits (sim/referee/limits.py) so a clearance that passes here keeps the day legal.
"7110.65" is FAA Order JO 7110.65 (Air Traffic Control).
"""
import math


# -- runway occupancy (7110.65 3-9-6 same-runway separation, 3-10-3 / 3-10-5 landing clearance) --------------------
LANDING_BUFFER_S = 8.0       # a landing needs every other aircraft off the runway this long before touchdown
# -- arrival / departure spacing on one runway (7110.65 3-9-6 / 3-10-3 same-runway separation, 3-7-2 crossings; the
#    guard times below are this agent's assumptions, not figures from the Order) -------------------------------------
TAKEOFF_ARRIVAL_GUARD_S = 60.0   # no takeoff roll with an arrival inside about 2 NM (2 NM at 65 m/s = 57 s)
LINEUP_ARRIVAL_GUARD_S = 90.0    # 3-9-4 line up and wait: not with a landing due before the departure can roll
CROSS_ARRIVAL_GUARD_S = 55.0     # a crossing takes 30-40 s from the clearance; never with a landing inside that + margin
# -- departure spacing (7110.65 3-9-6: preceding departure airborne and 6000 ft away; the Referee's AIRBORNE_GAP_M) -----
AIRBORNE_GAP_M = 6000 * 0.3048 * 1.25   # 25 % above the Referee's 6000 ft, measured from the departure's position
MIN_DEPARTURE_INTERVAL_S = 45.0          # never two takeoff clearances on one runway closer than this
# -- wake turbulence, departures on one runway (7110.65 3-9-6 / 5-8-3: 2 min behind a heavy, 3 min behind an A380) ---
WAKE_DEPARTURE_S = {"A": 180.0, "B": 120.0, "C": 120.0, "D": 120.0}  # leader CWT -> seconds, follower CWT E-I
WAKE_HEAVY_BEHIND_SUPER_S = 120.0        # leader A, follower B/C/D: 2 min (assumption: 5-8-3's 2 min for a heavy
                                         # behind a heavy, applied to a heavy behind a super; not checked against the Order)
WAKE_LIGHT_BEHIND_LARGE_S = 90.0         # assumption (the built-in tower's value): a light behind any jet
# -- taxi -------------------------------------------------------------------------------------------------------------
PUSHBACK_LANE_M = 70.0       # two pushbacks into lanes this close are serialised...
PUSHBACK_STAGGER_S = 90.0    # ...this far apart, so their push movements (at the end of ~280 s) do not overlap
PUSH_LOOK_M = 150.0          # route stretch checked against a pushback that may be moving
PUSH_CLEAR_M = 5.0           # margin between a pushback's swept area and any other aircraft
CONVERGE_WINDOW_S = 20.0     # two aircraft predicted at one junction closer in time than this: the later one waits
CONFLICT_MARGIN_M = 20.0     # route points closer than the two half wingspans + this are one junction
SHARP_TURN_DEG = 90.0        # a turn sharper than this near the start of a route sweeps its corner (planner geometry)
SHARP_TURN_LOOK_M = 100.0    # ... checked over the first stretch of the route only
SHARP_TURN_CLEAR_M = 40.0    # room beyond the half sizes at that corner (a 53 m sweep collided; 3 m observation noise)
HEAD_ON_FAST_MPS = 10.0      # opposite use of one taxiway edge is allowed only if the two are apart in time: earliest
HEAD_ON_SLOW_MPS = 3.0       # arrival at this speed, latest exit at this (holds and queues on the way),
HEAD_ON_START_S = 10.0       # after this start delay,
HEAD_ON_MARGIN_S = 60.0      # with this margin in between
HEAD_ON_NEAR_S = 60.0        # (the planner's second-choice routes avoid only this much of the other routes ahead)
SEE_AVOID_M = 15.0           # sim pilots stop for an aircraft this far (beyond half lengths) right ahead (sim README)
FACING_MARGIN_M = 12.0       # nose-to-nose meeting range margin (corner cutting ~10 m, 3 m observation noise)
FACING_DEG = 120.0           # headings this far apart: the two meet nose to nose
TURN_CLEAR_M = 30.0         # turning round in place needs this much room beyond the two aircraft's half sizes
GROUND_PHASES = ("taxi_in", "taxi_out", "pushback", "hold_short", "line_up", "runway_exit", "landing_roll")
# -- tailwind (pilot limit, typical transport AFM; the level exposes it as tailwind_limit_kt) --------------------------
TAILWIND_LIMIT_KT = 10.0
# -- rain / low visibility (the level's effects: wet runways occupy 15 % longer, approach control adds 1 NM) ----------
RAIN_VIS_SM = 5.0            # visibility below this, or the level's occupancy_mult above 1, counts as rain
RAIN_GUARD_MULT = 1.3        # every arrival guard above grows by 30 % in rain

RUNWAY_ACTIONS = ("cleared_to_land", "line_up_and_wait", "cleared_for_takeoff", "cross_runway")
RUNWAY_PHASES_ROLLING = ("takeoff_roll",)
STOPS_AIRCRAFT = ("hold_position",)   # replaces a live crossing clearance and stops the aircraft where it is


def _ok(rule, reason=""):
    return True, rule, reason


def _no(rule, reason):
    return False, rule, reason


def guard(pic, base):
    return base * (RAIN_GUARD_MULT if pic.get("rain") else 1.0)


def _arrivals_on(pic, pav, me):
    return [a for cs, a in pic["aircraft"].items() if cs != me and a.get("kind") == "arrival"
            and a.get("phase") in ("final", "go_around") and a.get("runway") == pav]


# -- rules ------------------------------------------------------------------------------------------------------------
def tailwind(pic, prop):
    """Pilots refuse a landing / line-up / takeoff above the tailwind limit; never send one, never pick such a flow."""
    limit = pic.get("tailwind_limit") or TAILWIND_LIMIT_KT
    ends = prop.get("ends") or [prop.get("end")]
    for end in ends:
        tw = pic["tailwind"].get(end, 0.0)
        if tw > limit:
            return _no("tailwind", f"Runway {end} has a {tw:.0f} kt tailwind, above the {limit:.0f} kt limit.")
    return _ok("tailwind")


def runway_occupancy(pic, prop):
    """One aircraft on a runway at a time. Landing: everyone else must be predicted off it before touchdown.
    Line-up / takeoff / crossing: nobody else on it, and nobody else cleared onto it."""
    cs, pav, t = prop["callsign"], prop["pavement"], pic["t"]
    others = [o for o in pic["occupants"].get(pav, []) if o != cs]
    cleared = [c for c in pic["runway_clearances"] if c["pavement"] == pav and c["callsign"] != cs]
    if prop["action"] == "cleared_to_land":
        touchdown = t + prop["eta"]
        for o in sorted(set(others) | {c["callsign"] for c in cleared if c["action"] != "cleared_to_land"}):
            clear = pic["clear_at"].get(o, float("inf"))
            if clear > touchdown - LANDING_BUFFER_S:
                when = "not predicted to clear" if clear == float("inf") else f"predicted clear in {clear - t:.0f} s"
                return _no("runway_occupancy", f"{o} is on or cleared onto runway {pav} ({when}); {cs} touches down in "
                                               f"{prop['eta']:.0f} s.")
        for c in cleared:  # an earlier landing on the same runway must roll out and exit first
            if c["action"] == "cleared_to_land" and c["callsign"] in pic["aircraft"]:
                lead = pic["aircraft"][c["callsign"]]
                lead_clear = t + (lead.get("eta_s") or 0.0) + pic["rollout_s"]
                if lead.get("phase") == "final" and (lead.get("eta_s") or 0.0) < prop["eta"] and \
                        lead_clear > touchdown - LANDING_BUFFER_S:
                    return _no("runway_occupancy", f"{c['callsign']} lands on {pav} first and is predicted on the runway "
                                                   f"until {lead_clear - t:.0f} s; {cs} touches down in {prop['eta']:.0f} s.")
        return _ok("runway_occupancy", f"Runway {pav} predicted clear at touchdown.")
    if others:
        return _no("runway_occupancy", f"Runway {pav} is occupied by {', '.join(others)}.")
    blocking = [c for c in cleared if c["action"] != "cleared_to_land"]
    if blocking:
        c = blocking[0]
        return _no("runway_occupancy", f"{c['callsign']} already holds a {c['action'].replace('_', ' ')} clearance on "
                                       f"runway {pav}.")
    return _ok("runway_occupancy", f"Runway {pav} is clear.")


def arrival_departure_spacing(pic, prop):
    """No takeoff with an arrival inside ~2 NM, no line-up with a landing due, no crossing ahead of a landing; and no
    landing clearance while a departure on that runway will still be on it."""
    cs, pav, act, t = prop["callsign"], prop["pavement"], prop["action"], pic["t"]
    if act == "cleared_to_land":
        return _ok("arrival_departure_spacing")
    base = {"cleared_for_takeoff": TAKEOFF_ARRIVAL_GUARD_S, "line_up_and_wait": LINEUP_ARRIVAL_GUARD_S,
            "cross_runway": CROSS_ARRIVAL_GUARD_S}[act]
    g = guard(pic, base)
    for a in _arrivals_on(pic, pav, cs):
        eta = a.get("eta_s")
        if eta is not None and eta < g:
            rule = "rain_spacing" if eta >= base else "arrival_departure_spacing"
            return _no(rule, f"{a['callsign']} lands on {pav} in {eta:.0f} s, inside the {g:.0f} s guard for "
                             f"{act.replace('_', ' ')}" + (" (rain-adjusted)." if rule == "rain_spacing" else "."))
    return _ok("arrival_departure_spacing", f"No arrival on {pav} inside {g:.0f} s.")


def departure_spacing(pic, prop):
    """The preceding departure on this runway must be airborne and 6000 ft (+25 %) away before the next takeoff."""
    cs, pav, t = prop["callsign"], prop["pavement"], pic["t"]
    last = pic["last_roll"].get(pav)
    if not last or last["callsign"] == cs:
        return _ok("departure_spacing")
    if t - last["t"] < MIN_DEPARTURE_INTERVAL_S:
        return _no("departure_spacing", f"{last['callsign']} started its takeoff roll {t - last['t']:.0f} s ago (minimum "
                                        f"{MIN_DEPARTURE_INTERVAL_S:.0f} s).")
    lead = pic["aircraft"].get(last["callsign"])
    if lead is not None and lead.get("phase") in ("takeoff_roll", "airborne", "line_up"):
        if lead["phase"] != "airborne":
            return _no("departure_spacing", f"{last['callsign']} is still on its takeoff roll on {pav}.")
        me = pic["aircraft"][cs]
        d = ((lead["x"] - me["x"]) ** 2 + (lead["y"] - me["y"]) ** 2) ** 0.5
        if d < AIRBORNE_GAP_M:
            return _no("departure_spacing", f"{last['callsign']} is airborne only {d / 0.3048:.0f} ft ahead (needs "
                                            f"{AIRBORNE_GAP_M / 0.3048:.0f} ft).")
    return _ok("departure_spacing", "Preceding departure airborne and clear.")


def wake_turbulence(pic, prop):
    """Time behind a departing heavy (or a jet, for a light) on the same runway, by wake category."""
    cs, pav, t = prop["callsign"], prop["pavement"], pic["t"]
    last = pic["last_roll"].get(pav)
    if not last or last["callsign"] == cs:
        return _ok("wake_turbulence")
    me = pic["aircraft"][cs]
    if me.get("wake") not in WAKE_DEPARTURE_S:
        need = WAKE_DEPARTURE_S.get(last.get("cwt"), 0.0)
    else:   # a heavy or super follower: only behind a super (A), and nothing behind the lighter heavies
        need = WAKE_HEAVY_BEHIND_SUPER_S if last.get("cwt") == "A" and me.get("wake") != "A" else 0.0
    if str(me.get("wtc", "")).startswith("L") and not str(last.get("wtc", "")).startswith("L"):
        need = max(need, WAKE_LIGHT_BEHIND_LARGE_S)
    if t - last["t"] < need:
        return _no("wake_turbulence", f"{cs} (wake {me.get('wake')}) must wait {need:.0f} s behind {last['callsign']} "
                                      f"(wake {last.get('cwt')}); {t - last['t']:.0f} s so far.")
    return _ok("wake_turbulence")


def hold_short(pic, prop):
    """A crossing needs its own clearance (7110.65 3-7-2): only with the runway clear, nothing cleared onto it, and
    no landing or takeoff due on it. Taxi routes never include a crossing: pilots stop at every hold point."""
    cs = prop["callsign"]
    if prop["action"] in STOPS_AIRCRAFT:
        a = pic["aircraft"].get(cs, {})
        on_rwy = any(cs in occ for occ in pic["occupants"].values())
        if on_rwy or "cross_runway" in pic.get("live_actions", {}).get(cs, ()):
            return _no("hold_short", f"{cs} is on or crossing a runway: it must not be stopped there.")
        return _ok("hold_short")
    crossing = "cross_runway" in pic.get("live_actions", {}).get(cs, ())
    if prop["action"] == "resume_taxi" and crossing:
        return _no("hold_short", f"{cs} holds a crossing clearance: resuming would replace it while the runway stays "
                                 f"open to the pilot (it would cross without a clearance).")
    if prop["action"] == "taxi_via" and crossing:
        a = pic["aircraft"].get(cs, {})
        on_rwy = any(cs in occ for occ in pic["occupants"].values())
        at_line = a.get("speed_mps", 9) < 0.3 and str(a.get("blocked_by") or "").startswith("runway:")
        if not (on_rwy or at_line):
            return _no("hold_short", f"{cs} holds a crossing clearance and may be past the hold line: no new route "
                                     f"until it is across.")
    if prop["action"] in ("taxi_via", "resume_taxi"):
        return _ok("hold_short", "The taxi route stops at every runway hold point until a crossing clearance.")
    pav, cs = prop["pavement"], prop["callsign"]
    rolling = [cs2 for cs2, a in pic["aircraft"].items() if cs2 != cs and a.get("phase") in RUNWAY_PHASES_ROLLING
               and a.get("runway") == pav]
    if rolling:
        return _no("hold_short", f"{rolling[0]} is on its takeoff roll on {pav}.")
    return _ok("hold_short")


def taxi_conflict(pic, prop):
    """No opposite-direction use of one taxiway edge (head-on: a gridlock) and no two aircraft converging on one node;
    pushbacks into nearby lanes are staggered."""
    cs = prop["callsign"]
    if prop["action"] == "pushback_approved":
        lane = prop["lane_xy"]
        for o, p in pic.get("pushbacks", {}).items():
            if o == cs:
                continue
            d = ((p["xy"][0] - lane[0]) ** 2 + (p["xy"][1] - lane[1]) ** 2) ** 0.5
            if d < PUSHBACK_LANE_M and pic["t"] - p["t"] < PUSHBACK_STAGGER_S:
                return _no("taxi_conflict", f"{o} is pushing back {d:.0f} m away; pushbacks are staggered "
                                            f"{PUSHBACK_STAGGER_S:.0f} s.")
        a0, b0 = prop["push_from"], prop["push_to"]
        me = pic["aircraft"][cs]
        ux, uy = b0[0] - a0[0], b0[1] - a0[1]
        L = max((ux * ux + uy * uy) ** 0.5, 1e-6)
        ux, uy = ux / L, uy / L
        for o, a in pic["aircraft"].items():   # the push sweeps from the stand to the lane, tail first
            if o == cs or a.get("phase") not in GROUND_PHASES + ("at_gate", "parked"):
                continue
            px, py = a["x"] - a0[0], a["y"] - a0[1]
            along = max(0.0, min(L + me["length_m"] / 2, px * ux + py * uy))
            d = ((px - along * ux) ** 2 + (py - along * uy) ** 2) ** 0.5
            if d < me["wingspan_m"] / 2 + a["length_m"] / 2 + PUSH_CLEAR_M:
                return _no("taxi_conflict", f"{o} is in {cs}'s pushback path ({d:.0f} m from it).")
            r = ((a["x"] - b0[0]) ** 2 + (a["y"] - b0[1]) ** 2) ** 0.5   # it swings round to its taxi heading there
            if r < half_diag(me) + half_diag(a) + PUSH_CLEAR_M:
                return _no("taxi_conflict", f"{o} is {r:.0f} m from where {cs} swings round at the end of its pushback.")
        return _ok("taxi_conflict")
    route = prop.get("route") or []
    if prop.get("turn_round"):
        me = pic["aircraft"][cs]
        for o, a in pic["aircraft"].items():
            if o != cs and a.get("phase") in GROUND_PHASES:
                d = ((a["x"] - me["x"]) ** 2 + (a["y"] - me["y"]) ** 2) ** 0.5
                if d < me["length_m"] / 2 + a["wingspan_m"] / 2 + TURN_CLEAR_M:
                    return _no("taxi_conflict", f"{cs} cannot turn round where it stands: {o} is {d:.0f} m away.")
    me = pic["aircraft"].get(cs)
    h, at, d = (me or {}).get("heading_deg"), (me or {}).get("x"), 0.0
    at = (me["x"], me["y"]) if me and at is not None else None
    for p in (prop.get("path") or []) if at else []:   # a sharp turn swings the tail and wings round its corner
        if d > SHARP_TURN_LOOK_M:
            break
        if h is not None and abs((p["hdg"] - h + 180) % 360 - 180) > SHARP_TURN_DEG:
            for o, a in pic["aircraft"].items():
                if o != cs and a.get("phase") in GROUND_PHASES:
                    g = ((a["x"] - at[0]) ** 2 + (a["y"] - at[1]) ** 2) ** 0.5
                    if g < me["length_m"] / 2 + a["wingspan_m"] / 2 + SHARP_TURN_CLEAR_M:
                        return _no("taxi_conflict", f"{cs}'s route turns sharply {d:.0f} m ahead and {o} is {g:.0f} m "
                                                    f"from that corner.")
        d += ((p["xy"][0] - at[0]) ** 2 + (p["xy"][1] - at[1]) ** 2) ** 0.5
        at = p["xy"]   # turns measured against the aircraft's own heading (the first short leg's bearing is noisy)
    mine = set(zip(route, route[1:]))
    for o, r in pic["routes"].items():
        if o == cs:
            continue
        head_on = [(u, v) for u, v in zip(r, r[1:]) if (v, u) in mine and not _apart(pic, prop, o, (u, v))]
        if head_on:
            return _no("taxi_conflict", f"Route meets {o} head-on between nodes {head_on[0][1]} and {head_on[0][0]}.")
    for o, n in pic.get("parked_on", {}).items():  # aircraft stopped with no route yet (e.g. pushed back, waiting)
        if o != cs and n in route[1:]:
            return _no("taxi_conflict", f"{o} is stopped on the route at node {n}.")
    mine = prop.get("path") or []
    me = pic["aircraft"].get(cs, {})
    hit = _into_push(pic, cs, me, mine)
    if hit:
        return _no("taxi_conflict", f"{cs}'s route runs into {hit}'s pushback, which may be moving now.")
    for o, theirs in pic.get("paths", {}).items():
        if o == cs:
            continue
        b = pic["aircraft"][o]
        hit = converging(mine, theirs, (me.get("wingspan_m", 36.0) + b["wingspan_m"]) / 2, facing_range(me, b))
        if hit:
            return _no("taxi_conflict", f"{cs} and {o} would reach the same junction {hit[0]:.0f} s / {hit[1]:.0f} s from "
                                        f"now (closer than {CONVERGE_WINDOW_S:.0f} s apart).")
    return _ok("taxi_conflict", "No head-on, blocked or converging segment on the route.")


def _into_push(pic, cs, me, path):
    """An approved pushback (it may start moving at any time and cannot be stopped; LR21: an aircraft that taxied up
    while it waited was hit when it swung round) whose stand-to-lane sweep (plus its half length
    past the lane) the route ahead reaches within the two half spans + PUSH_CLEAR_M."""
    hot = {o for o, pb in pic.get("pushbacks", {}).items() if o != cs and (pic["aircraft"].get(o) or {}).get("phase") == "pushback"}
    if not hot or not me or "x" not in me or not path:
        return None
    pts, prev, d = [], (me["x"], me["y"]), 0.0
    for p in path:
        q = p["xy"]
        seg = ((q[0] - prev[0]) ** 2 + (q[1] - prev[1]) ** 2) ** 0.5
        k = 0.0
        while k < seg and d + k <= PUSH_LOOK_M:
            pts.append((prev[0] + (q[0] - prev[0]) * k / seg, prev[1] + (q[1] - prev[1]) * k / seg))
            k += 8.0
        d += seg
        if d > PUSH_LOOK_M:
            break
        prev = q
    pts = pts[1:] if len(pts) > 1 else pts   # (where it stands now is no new conflict)
    for o in sorted(hot):
        pb, a = pic["pushbacks"].get(o), pic["aircraft"].get(o)
        if not pb or not a or "stand_xy" not in pb:
            continue
        (ax, ay), (bx, by) = pb["stand_xy"], pb["xy"]
        L = max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1e-6)
        ux, uy = (bx - ax) / L, (by - ay) / L
        lim = me.get("wingspan_m", 36.0) / 2 + a["wingspan_m"] / 2 + PUSH_CLEAR_M
        swing = half_diag(me) + half_diag(a) + PUSH_CLEAR_M
        for x, y in pts:
            k = max(0.0, min(L + a["length_m"] / 2, (x - ax) * ux + (y - ay) * uy))
            if ((x - ax - k * ux) ** 2 + (y - ay - k * uy) ** 2) ** 0.5 < lim or ((x - bx) ** 2 + (y - by) ** 2) ** 0.5 < swing:
                return o
    return None


def _apart(pic, prop, o, edge):
    """Two routes use one edge in opposite directions: are they far enough apart in time that the first is off it
    before the other can reach it? Earliest entry at HEAD_ON_FAST_MPS, latest exit at HEAD_ON_SLOW_MPS after a
    START_S start, plus HEAD_ON_MARGIN_S. Without distances: never apart (the strict rule)."""
    mine, theirs = (prop.get("route_d") or {}).get((edge[1], edge[0])), (pic.get("route_d") or {}).get(o, {}).get(edge)
    if not mine or not theirs:
        return False
    span = lambda d: (d[0] / HEAD_ON_FAST_MPS, HEAD_ON_START_S + d[1] / HEAD_ON_SLOW_MPS)
    (a0, a1), (b0, b1) = span(mine), span(theirs)
    return a1 + HEAD_ON_MARGIN_S < b0 or b1 + HEAD_ON_MARGIN_S < a0


def half_diag(a):
    """Half the diagonal of an aircraft's footprint: how far it reaches while turning on the spot."""
    return ((a.get("length_m", 40.0) / 2) ** 2 + (a.get("wingspan_m", 36.0) / 2) ** 2) ** 0.5


def facing_range(a, b):
    """Two aircraft meeting nose to nose stop for each other inside this range (sim see-and-avoid: half lengths + 15 m),
    plus a margin for corner cutting (LR24: stopped 43 m apart on taxiway legs 57 m apart) and observation noise."""
    return (a.get("length_m", 40.0) + b.get("length_m", 40.0)) / 2 + SEE_AVOID_M + FACING_MARGIN_M


def converging(mine, theirs, half_spans, facing=0.0):
    """(my eta, their eta) at the first pair of route points closer than the half wingspans + margin (or, heading at
    each other, closer than `facing`) that both reach within CONVERGE_WINDOW_S of each other, unless one simply follows
    the other there (same heading in). Path points: {xy, eta, hdg} (hdg = direction of the edge into the point)."""
    r = half_spans + CONFLICT_MARGIN_M
    for p in mine:
        for q in theirs:
            hd = abs((p["hdg"] - q["hdg"] + 180) % 360 - 180)
            rr = max(r, facing) if hd > FACING_DEG else r
            if (p["xy"][0] - q["xy"][0]) ** 2 + (p["xy"][1] - q["xy"][1]) ** 2 > rr * rr:
                continue
            if hd < 40:
                continue   # in trail on the same line: the pilots keep their distance
            if abs(p["eta"] - q["eta"]) < CONVERGE_WINDOW_S:
                return p["eta"], q["eta"]
    return None


RULES = {
    "cleared_to_land": (tailwind, runway_occupancy),
    "line_up_and_wait": (tailwind, runway_occupancy, arrival_departure_spacing),
    "cleared_for_takeoff": (tailwind, runway_occupancy, arrival_departure_spacing, departure_spacing, wake_turbulence),
    "cross_runway": (hold_short, runway_occupancy, arrival_departure_spacing),
    "taxi_via": (taxi_conflict, hold_short),
    "pushback_approved": (taxi_conflict,),
    "hold_position": (hold_short,),
    "resume_taxi": (hold_short, taxi_conflict),
    "set_runway_config": (tailwind,),
}


def check(pic, prop):
    """(ok, rule, reason) for a proposal: the first failing rule, else the last rule's approval (or a generic one)."""
    res = _ok("none", "No runway or taxi hazard applies to this instruction.")
    for rule in RULES.get(prop["action"], ()):
        res = rule(pic, prop)
        if not res[0]:
            return res
    if res[0] and res[1] != "none":
        names = ", ".join(r.__name__ for r in RULES[prop["action"]])
        return True, res[1], f"Passed {names}."
    return res
