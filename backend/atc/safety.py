"""Agent safety engine: deterministic rules that approve or reject every proposed instruction before it is sent.

Each rule is `rule(pic, prop) -> (ok, rule_name, reason)`. `pic` is the controller's picture of the airport this tick
(built by the planner from simif observations and its own record of what it cleared); `prop` is the proposal
`{callsign, action, end, pavement, route, ...}`. `check()` runs the rules for the action; the first rejection wins.
The engine only stops the agent from SENDING unsafe clearances. The Referee (in the simulator) judges independently;
these margins sit above its limits (sim/referee/limits.py) so a clearance that passes here keeps the day legal.
"7110.65" is FAA Order JO 7110.65 (Air Traffic Control).
"""

# -- runway occupancy (7110.65 3-9-6 same-runway separation, 3-10-3 / 3-10-5 landing clearance) --------------------
LANDING_BUFFER_S = 8.0       # a landing needs every other aircraft off the runway this long before touchdown
# -- arrival / departure spacing on one runway (7110.65 3-9-? takeoff with an arrival on final; 3-7-2 crossings) ------
TAKEOFF_ARRIVAL_GUARD_S = 60.0   # no takeoff roll with an arrival inside about 2 NM (2 NM at 65 m/s = 57 s)
LINEUP_ARRIVAL_GUARD_S = 90.0    # 3-9-4 line up and wait: not with a landing due before the departure can roll
CROSS_ARRIVAL_GUARD_S = 55.0     # a crossing takes 30-40 s from the clearance; never with a landing inside that + margin
# -- departure spacing (7110.65 3-9-6: preceding departure airborne and 6000 ft away; the Referee's AIRBORNE_GAP_M) -----
AIRBORNE_GAP_M = 6000 * 0.3048 * 1.25   # 25 % above the Referee's 6000 ft, measured from the departure's position
MIN_DEPARTURE_INTERVAL_S = 45.0          # never two takeoff clearances on one runway closer than this
# -- wake turbulence, departures on one runway (7110.65 3-9-6 / 5-8-3: 2 min behind a heavy, 3 min behind an A380) ---
WAKE_DEPARTURE_S = {"A": 180.0, "B": 120.0, "C": 120.0, "D": 120.0}  # leader CWT -> seconds (follower not CWT A-D)
WAKE_LIGHT_BEHIND_LARGE_S = 90.0         # assumption (the built-in tower's value): a light behind any jet
# -- taxi -------------------------------------------------------------------------------------------------------------
PUSHBACK_LANE_M = 70.0       # two pushbacks into lanes this close are serialised...
PUSHBACK_STAGGER_S = 90.0    # ...this far apart, so their push movements (at the end of ~280 s) do not overlap
# -- tailwind (pilot limit, typical transport AFM; the level exposes it as tailwind_limit_kt) --------------------------
TAILWIND_LIMIT_KT = 10.0
# -- rain / low visibility (the level's effects: wet runways occupy 15 % longer, approach control adds 1 NM) ----------
RAIN_VIS_SM = 5.0            # visibility below this, or the level's occupancy_mult above 1, counts as rain
RAIN_GUARD_MULT = 1.3        # every arrival guard above grows by 30 % in rain

RUNWAY_ACTIONS = ("cleared_to_land", "line_up_and_wait", "cleared_for_takeoff", "cross_runway")
RUNWAY_PHASES_ROLLING = ("takeoff_roll",)
AMENDS_CROSSING = ("taxi_via", "hold_position", "resume_taxi")   # the sim replaces a live crossing clearance with these


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
    need = WAKE_DEPARTURE_S.get(last.get("cwt"), 0.0) if me.get("wake") not in WAKE_DEPARTURE_S else 0.0
    if str(me.get("wtc", "")).startswith("L") and not str(last.get("wtc", "")).startswith("L"):
        need = max(need, WAKE_LIGHT_BEHIND_LARGE_S)
    if t - last["t"] < need:
        return _no("wake_turbulence", f"{cs} (wake {me.get('wake')}) must wait {need:.0f} s behind {last['callsign']} "
                                      f"(wake {last.get('cwt')}); {t - last['t']:.0f} s so far.")
    return _ok("wake_turbulence")


def hold_short(pic, prop):
    """A crossing needs its own clearance (7110.65 3-7-2): only with the runway clear, nothing cleared onto it, and
    no landing or takeoff due on it. Taxi routes never include a crossing: pilots stop at every hold point."""
    if prop["action"] in AMENDS_CROSSING:
        if "cross_runway" in pic.get("live_actions", {}).get(prop["callsign"], ()):
            return _no("hold_short", f"{prop['callsign']} is crossing a runway: a new taxi instruction would cancel its "
                                     f"crossing clearance while it is on the runway.")
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
        return _ok("taxi_conflict")
    route = prop.get("route") or []
    mine = set(zip(route, route[1:]))
    for o, r in pic["routes"].items():
        if o == cs:
            continue
        head_on = [(u, v) for u, v in zip(r, r[1:]) if (v, u) in mine]
        if head_on:
            return _no("taxi_conflict", f"Route meets {o} head-on between nodes {head_on[0][1]} and {head_on[0][0]}.")
    for o, n in pic.get("parked_on", {}).items():  # aircraft stopped with no route yet (e.g. pushed back, waiting)
        if o != cs and n in route[1:]:
            return _no("taxi_conflict", f"{o} is stopped on the route at node {n}.")
    return _ok("taxi_conflict", "No head-on or blocked segment on the route.")


RULES = {
    "cleared_to_land": (tailwind, runway_occupancy),
    "line_up_and_wait": (tailwind, runway_occupancy, arrival_departure_spacing),
    "cleared_for_takeoff": (tailwind, runway_occupancy, arrival_departure_spacing, departure_spacing, wake_turbulence),
    "cross_runway": (hold_short, runway_occupancy, arrival_departure_spacing),
    "taxi_via": (taxi_conflict, hold_short),
    "pushback_approved": (taxi_conflict,),
    "hold_position": (hold_short,),
    "resume_taxi": (hold_short,),
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
