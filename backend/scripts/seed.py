"""Seed/replay a scripted demo run into the event DB.

Everything airport-specific (runways, nodes, coordinates, ICAO) is derived from backend/airport/.
Usage (from backend/): .venv/bin/python scripts/seed.py [--speed 4] [--instant]
"""
import argparse
import heapq
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import airport, db  # noqa: E402

KT = 0.514444  # m/s per knot
NM = 1852.0
DT = 2.0  # sim seconds between position reports
SPOKEN = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
          "7": "seven", "8": "eight", "9": "niner", "L": "left", "R": "right", "C": "center"}
ARR, DEP = "N123AB", "N456CD"


def spoken(designator):
    return " ".join(SPOKEN.get(ch, ch) for ch in designator)


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


def shortest(graph, start, goal, allow):
    """Dijkstra over bidirectional edges whose kind is in `allow`; returns node ids."""
    adj = {}
    for e in graph["edges"]:
        if e["kind"] in allow:
            adj.setdefault(e["from"], []).append((e["to"], e["length_m"]))
            adj.setdefault(e["to"], []).append((e["from"], e["length_m"]))
    best, todo = {start: (0, None)}, [(0, start)]
    while todo:
        d, n = heapq.heappop(todo)
        if n == goal:
            route = [n]
            while best[route[-1]][1] is not None:
                route.append(best[route[-1]][1])
            return route[::-1]
        for m, w in adj.get(n, []):
            if m not in best or d + w < best[m][0]:
                best[m] = (d + w, n)
                heapq.heappush(todo, (d + w, m))
    raise ValueError(f"no route {start} -> {goal}")


class Story:
    def __init__(self, run_id):
        self.run_id, self.events = run_id, []

    def add(self, t, type, callsign=None, data=None, reason=""):
        self.events.append({"t": round(t, 1), "type": type, "callsign": callsign, "data": data or {}, "reason": reason})

    def move(self, cs, t, path, phase, v0, v1):
        """Position reports every DT along path, speed easing v0 -> v1 knots. Returns arrival time."""
        total, s = path_len(path), 0.0
        while True:
            v = v0 + (v1 - v0) * min(s / total, 1.0) if total else v1
            p, hdg = point_at(path, min(s, total))
            self.add(t, "position", cs, {"lat": round(p[1], 7), "lon": round(p[0], 7), "heading": hdg,
                                         "phase": phase, "speed_kt": round(v)})
            if s >= total:
                return t
            step = max(v, 5) * KT * DT
            if s + step >= total:
                t += (total - s) / (max(v, 5) * KT)
                s = total
            else:
                t, s = t + DT, s + step

    def hold(self, cs, t0, t1, p, hdg, phase):
        t = t0
        while t < t1:
            self.add(t, "position", cs, {"lat": round(p[1], 7), "lon": round(p[0], 7), "heading": hdg,
                                         "phase": phase, "speed_kt": 0})
            t += DT

    def say(self, t, cs, action, runway, text, reason, readback=None):
        self.add(t, "clearance", cs, {"text": text, "action": action, "runway": runway}, reason)
        self.add(t + 0.2, "audio", cs, {"url": "", "speaker": "tower", "text": text}, "")
        if readback is not None:
            self.add(t + 2, "readback", cs, {"transcript": readback, "matched": True}, "Readback matches the clearance.")


def build_events(apt, run_id):
    cfg, geo, graph = apt["config"], apt["geojson"], apt["graph"]
    nodes = graph["nodes"]
    pt = lambda nid: [nodes[nid]["lon"], nodes[nid]["lat"]]
    pts = lambda ids: [pt(n) for n in ids]
    rwy = next(f["properties"] for f in geo["features"] if f["properties"]["kind"] == "runway")
    holds = [f["properties"] for f in geo["features"] if f["properties"]["kind"] == "hold_short"]
    parking = [f["properties"]["id"] for f in geo["features"] if f["properties"]["kind"] == "parking"]
    active, other = rwy["ends"][-1], rwy["ends"][0]
    des, rwy_id = active["designator"], rwy["id"]
    say_rwy = f"runway {spoken(des)}"
    thr = graph["approaches"][des]["threshold_node"]
    far_thr = graph["approaches"][other["designator"]]["threshold_node"]
    wind_dir = int(round(active["heading"] / 10.0)) * 10 % 360 or 360
    S = Story(run_id)

    def runway_node(hs):  # runway node a hold-short point protects
        return next(e["to"] if e["from"] == hs else e["from"] for e in graph["edges"]
                    if hs in (e["from"], e["to"]) and nodes[e["to"] if e["from"] == hs else e["from"]]["kind"] == "runway")

    along = lambda nid: dist(pt(thr), pt(nid))
    exit_hs = min((h for h in holds if along(runway_node(h["id"])) >= 900), key=lambda h: along(runway_node(h["id"])),
                  default=max(holds, key=lambda h: along(runway_node(h["id"]))))
    dep_hs = min(holds, key=lambda h: along(runway_node(h["id"])))
    arr_gate, dep_gate = parking[0], parking[-1]

    # --- setup ---
    S.add(0, "sim_start", None, {"run_id": run_id, "airport": cfg["name"], "icao": cfg["icao"], "speed": 1},
          "Scripted demo run started.")
    raw = f"{cfg['icao']} 141851Z {wind_dir:03d}12G18KT 10SM FEW080 24/M02 A2998"
    S.add(1, "weather", None, {"raw": raw, "wind_dir": wind_dir, "wind_speed": 12, "wind_gust": 18,
                               "visibility_sm": 10, "source": "scenario"}, "Scenario weather loaded.")
    S.add(2, "ai_decision", None, {
        "question": "Which runway direction should be active?",
        "options": [{"id": e["designator"], "label": f"Runway {e['designator']}"} for e in rwy["ends"]],
        "choice": des, "source": "claude",
        "explanation": f"Wind {wind_dir:03d} at 12 gusting 18 is a direct headwind for runway {des}."},
        f"Runway {des} is into the wind.")
    S.add(2.5, "runway_change", None, {"runway": des, "previous": None},
          f"Wind {wind_dir:03d}° favours runway {des}; landing and departing into the wind.")

    # --- departure: parked, taxi to hold short ---
    dep_route = shortest(graph, dep_gate, dep_hs["id"], {"taxiway"})
    dep_path = pts(dep_route)
    _, dep_hdg0 = point_at(dep_path, 0)
    S.add(3, "aircraft_spawn", DEP, {"kind": "departure", "aircraft_type": "C172", "lat": dep_path[0][1],
                                     "lon": dep_path[0][0], "heading": dep_hdg0, "phase": "parked"},
          f"{DEP} is parked and ready to taxi.")
    S.hold(DEP, 3, 6, dep_path[0], dep_hdg0, "parked")
    S.add(4, "proposal", DEP, {"action": "taxi", "runway": des, "route": dep_route},
          f"Taxi {DEP} to {dep_hs['taxiway']} hold short for runway {des}.")
    S.add(4.5, "safety_check", DEP, {"approved": True, "rule": "taxi_route_clear", "action": "taxi"},
          "Taxi route is clear of other traffic and does not cross the runway.")
    S.say(5, DEP, "taxi", des, f"{DEP}, {say_rwy}, taxi via {dep_hs['taxiway']}, hold short {say_rwy}.",
          "Taxi route approved by safety check.", f"Taxi via {dep_hs['taxiway']}, hold short {say_rwy}, {DEP}.")
    t_dep_hs = S.move(DEP, 6, dep_path, "taxi", 20, 20)

    # --- arrival: spawn on final ~2 nm out ---
    app_path = graph["approaches"][des]["path"]
    app_len = path_len(app_path)
    s0 = max(app_len - 2 * NM, 0)
    spawn, app_hdg = point_at(app_path, s0)
    short = point_at(app_path, app_len - 0.5 * NM)[0]
    t_arr = max(10, round(t_dep_hs) - 60)  # ~60 s of final: departure reaches hold short before touchdown
    S.add(t_arr, "aircraft_spawn", ARR, {"kind": "arrival", "aircraft_type": "C208", "lat": spawn[1], "lon": spawn[0],
                                         "heading": app_hdg, "phase": "approach"},
          f"{ARR} checked in on a 2 mile final for runway {des}.")
    S.add(t_arr + 1, "ai_decision", None, {
        "question": f"Sequence {ARR} (arrival) and {DEP} (departure): who uses runway {des} first?",
        "options": [{"id": "arrival_first", "label": f"{ARR} lands first"},
                    {"id": "departure_first", "label": f"{DEP} departs first"}],
        "choice": "arrival_first", "source": "fallback",
        "explanation": "AI model unavailable; fallback rule: an arrival on final has priority over a departure."},
        "Fallback rule: arrivals on final have priority.")
    S.add(t_arr + 1.5, "sequence", None, {"arrivals": [ARR], "departures": [DEP]},
          f"{ARR} lands first, then {DEP} departs.")
    S.add(t_arr + 2, "proposal", ARR, {"action": "land", "runway": des, "route": None}, f"Clear {ARR} to land runway {des}.")
    S.add(t_arr + 2.5, "safety_check", ARR, {"approved": True, "rule": "runway_clear", "action": "land"},
          f"Runway {des} is clear and no departure is rolling.")
    land_text = f"{ARR}, wind {spoken(f'{wind_dir:03d}')} at one two gusts one eight, {say_rwy}, cleared to land."
    S.add(t_arr + 3, "clearance", ARR, {"text": land_text, "action": "land", "runway": des}, "Landing clearance issued.")
    S.add(t_arr + 3.2, "audio", ARR, {"url": "", "speaker": "tower", "text": land_text}, "")
    S.add(t_arr + 6, "readback", ARR, {"transcript": f"Cleared to land runway {spoken(other['designator'])}, {ARR}.",
                                       "matched": False},
          f"Pilot read back runway {other['designator']}; cleared runway is {des}.")
    S.add(t_arr + 7, "readback_correction", ARR, {"text": f"{ARR}, negative, {say_rwy}, cleared to land {say_rwy}."},
          "Wrong runway in readback; correcting.")
    S.add(t_arr + 10, "readback", ARR, {"transcript": f"Cleared to land {say_rwy}, {ARR}.", "matched": True},
          "Corrected readback matches.")
    t = S.move(ARR, t_arr, [spawn] + [p for p in app_path if dist(p, app_path[-1]) < 2 * NM - 1 and
                                      dist(p, app_path[-1]) > 0.5 * NM] + [short], "approach", 110, 100)
    t_land = S.move(ARR, t + DT, [short, app_path[-1]], "landing", 100, 90)
    rollout = pts(shortest(graph, thr, runway_node(exit_hs["id"]), {"runway"}))
    t = S.move(ARR, t_land + DT, rollout, "rollout", 90, 20)
    S.add(t, "proposal", ARR, {"action": "taxi", "runway": None,
                               "route": shortest(graph, runway_node(exit_hs["id"]), arr_gate, {"taxiway"})},
          f"Exit at {exit_hs['taxiway']} and taxi {ARR} to parking {arr_gate}.")
    S.add(t + 0.5, "safety_check", ARR, {"approved": True, "rule": "taxi_route_clear", "action": "taxi"},
          "Taxi route to parking is clear.")
    S.say(t + 1, ARR, "taxi", None, f"{ARR}, exit {exit_hs['taxiway']}, taxi to parking {arr_gate}.",
          "Arrival is slowing; route to parking is clear.", f"Exit {exit_hs['taxiway']}, to parking {arr_gate}, {ARR}.")
    t_clear = S.move(ARR, t + DT, pts([runway_node(exit_hs["id"]), exit_hs["id"]]), "taxi", 15, 12)
    t = S.move(ARR, t_clear + DT, pts(shortest(graph, exit_hs["id"], arr_gate, {"taxiway"})), "taxi", 20, 20)
    t_parked = t + DT
    S.hold(ARR, t_parked, t_parked + 1, pt(arr_gate), S.events[-1]["data"]["heading"], "parked")
    S.add(t_parked + 0.5, "alert", ARR, {"level": "info", "message": f"{ARR} parked at {arr_gate}."}, "Arrival complete.")

    # --- departure holds short, is rejected, then released ---
    hs_p = pt(dep_hs["id"])
    t_req = t_dep_hs + DT
    arr_state = "on short final" if t_req < t_land else "on the runway"
    S.hold(DEP, t_dep_hs + DT, t_clear + 4, hs_p, bearing(hs_p, pt(runway_node(dep_hs["id"]))), "holding")
    S.add(t_req, "proposal", DEP, {"action": "takeoff", "runway": des, "route": None}, f"Clear {DEP} for takeoff runway {des}.")
    S.add(t_req + 0.5, "safety_check", DEP, {"approved": False, "rule": "runway_occupied", "action": "takeoff"},
          f"Rejected: {ARR} is {arr_state} for runway {des}; {DEP} must hold short.")
    S.add(t_req + 1, "alert", DEP, {"level": "warning", "message": f"Takeoff for {DEP} blocked: {ARR} {arr_state}."},
          "Safety check rejected a takeoff proposal.")
    S.say(t_req + 2, DEP, "hold_short", des, f"{DEP}, hold short {say_rwy}, landing traffic.",
          f"{ARR} is {arr_state}.", f"Holding short {say_rwy}, {DEP}.")

    t = t_clear + 1
    S.add(t, "ai_decision", None, {
        "question": f"{ARR} has cleared runway {des}. Release {DEP} now?",
        "options": [{"id": "release", "label": f"Line up {DEP}"}, {"id": "wait", "label": "Keep holding"}],
        "choice": "release", "source": "claude",
        "explanation": f"{ARR} is past the hold-short line and no other arrival is inside 5 miles."},
        "Runway is clear and there is no other traffic.")
    S.add(t + 0.5, "sequence", None, {"arrivals": [], "departures": [DEP]}, f"{ARR} landed; {DEP} is next.")
    S.add(t + 1, "proposal", DEP, {"action": "line_up", "runway": des, "route": [dep_hs["id"], runway_node(dep_hs["id"])]},
          f"Line {DEP} up on runway {des}.")
    S.add(t + 1.5, "safety_check", DEP, {"approved": True, "rule": "runway_clear", "action": "line_up"},
          f"Runway {des} is clear of all traffic.")
    S.say(t + 2, DEP, "line_up", des, f"{DEP}, {say_rwy}, line up and wait.", "Runway is clear.",
          f"Line up and wait {say_rwy}, {DEP}.")
    lineup = pts([dep_hs["id"], runway_node(dep_hs["id"])])
    t = S.move(DEP, t_clear + 4, lineup, "lineup", 12, 8)
    t_roll = t + 4
    S.hold(DEP, t + DT, t_roll, lineup[-1], active["heading"], "lineup")
    S.add(t_roll - 3, "proposal", DEP, {"action": "takeoff", "runway": des, "route": None}, f"Clear {DEP} for takeoff runway {des}.")
    S.add(t_roll - 2.5, "safety_check", DEP, {"approved": True, "rule": "runway_clear", "action": "takeoff"},
          f"Runway {des} is clear and no arrival is within 5 miles.")
    S.say(t_roll - 2, DEP, "takeoff", des, f"{DEP}, wind {spoken(f'{wind_dir:03d}')} at one two, {say_rwy}, cleared for takeoff.",
          "Takeoff approved by safety check.", f"Cleared for takeoff {say_rwy}, {DEP}.")
    roll = pts(shortest(graph, runway_node(dep_hs["id"]), far_thr, {"runway"}))
    rotate = point_at(roll, min(400, path_len(roll)))[0]
    t = S.move(DEP, t_roll, [roll[0], rotate], "takeoff", 20, 60)
    t = S.move(DEP, t + DT, [rotate, offset(rotate, active["heading"], 1500)], "departed", 65, 100)
    S.add(t + 0.5, "sequence", None, {"arrivals": [], "departures": []}, "No traffic in sequence.")
    S.add(t + 1, "alert", DEP, {"level": "info", "message": f"{DEP} departed runway {des}."}, "Departure complete.")

    S.add(max(t, t_parked) + 3, "sim_end", None, {}, "Scripted demo run complete.")
    S.events.sort(key=lambda e: e["t"])  # stable: same-time events keep script order
    return S.events


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--speed", type=float, default=4, help="sim seconds per wall second")
    ap.add_argument("--instant", action="store_true", help="write everything with no sleeping")
    args = ap.parse_args()
    run_id = db.start_run()
    events = build_events(airport.load(), run_id)
    events[0]["data"]["speed"] = args.speed
    print(f"run {run_id}: {len(events)} events over {events[-1]['t']:.0f} sim s -> {db.db_path()}")
    prev = 0.0
    for e in events:
        if not args.instant:
            time.sleep(max(e["t"] - prev, 0) / args.speed)
        prev = e["t"]
        db.emit(run_id, e["type"], e["t"], e["callsign"], e["data"], e["reason"])
    print("done")


if __name__ == "__main__":
    main()
