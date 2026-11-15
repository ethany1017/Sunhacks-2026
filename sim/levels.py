"""Levels: a seeded KPHX day (Normal Day, Light Rain) built from real PHX traffic rates, with its weather and effects.

A level is `(level_id, seed, duration_s)` -> a Scenario (the traffic) plus a LevelWeather (METAR reports over the day
and the physical effect multipliers). Same seed -> same traffic in both levels; the level only changes the weather.
Every tunable number is a plain named value in this file, each with its source; docs/LEVELS.md explains them.
S5 calibrates these values against the Human controller, then they are frozen.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from sim.core.aircraft_types import AircraftType, get_type
from sim.core.scenario import Flight, Scenario
from sim.referee.limits import NM, RADAR_MIN_M, WAKE_NM

ROOT = Path(__file__).resolve().parents[1]

# -- seeds: one place, never mixed. Tune on TRAINING; grade on HELD_OUT only. ------------------------------------------
TRAINING_SEEDS: Dict[str, List[int]] = {"normal_day": [1, 2, 3, 4, 5], "light_rain": [21, 22, 23, 24, 25]}
HELD_OUT_SEEDS: Dict[str, List[int]] = {"normal_day": [9001, 9002, 9003], "light_rain": [9021, 9022, 9023]}

# -- traffic (same for both levels) ---------------------------------------------------------------------------------------
DEFAULT_DURATION_S = 1500.0     # assumption: the 12-hour day below compressed to 25 min of scheduled traffic
HOURLY_CSV = ROOT / "data/kphx_traffic/bts_otp_2026_8/derived/phx_hourly_summary.csv"   # BTS on-time, PHX, Aug 2026
DAY_HOURS = list(range(6, 18))  # local hours 06-17 (assumption: the daytime part of the BTS profile, banks at 08-10 and 15-16)
DEMAND_SCALE = 1.0              # 1.0 = the real BTS scheduled rate of each hour; S5 tunes this
ARRIVAL_LEAD_S = 150.0          # assumption: an arrival's slot time is when it enters the 11 km final; it lands this much later
# carrier mix: BTS Aug 2026 PHX flights by carrier (WN 9955, AA 8219, OO 3947, MQ 2200, DL 1672, UA 1438, F9 1079, AS 916);
# fleets per carrier are the KPHX aircraft CSV types each airline flies (as in sim/scenarios/generate.py).
AIRLINES: Dict[str, Tuple[int, List[str]]] = {
    "SWA": (9955, ["B737", "B738", "B38M", "B39M"]), "AAL": (8219, ["A319", "A320", "A321", "B738"]),
    "SKW": (3947, ["E75L", "CRJ7", "CRJ9"]), "ENY": (2200, ["E75S", "E170"]), "DAL": (1672, ["B738", "A321", "B752"]),
    "UAL": (1438, ["B738", "B739", "A320"]), "FFT": (1079, ["A320", "A21N"]), "ASA": (916, ["B738", "B39M"]),
}
HEAVY_SHARE = 0.04              # assumption: Hawaiian A332 (BTS HA, ~2 flights/day) and wide-body cargo (FedEx B763)
HEAVIES: List[Tuple[str, str]] = [("HAL", "A332"), ("FDX", "B763")]
LIGHT_SHARE = 0.05              # assumption: GA/air-taxi share of the mix (BTS covers airlines only; Sky Harbor reports
                                # ~1,335 operations/day vs 952 BTS flights/day, the rest is GA, cargo and non-reporting carriers)
LIGHTS = ["C172", "SR22", "PC12", "C208"]
MIN_HEAVY_ARRIVALS = 1          # assumption: so wake separation always matters (a heavy and a light on final every day)
MIN_LIGHT_ARRIVALS = 1
READBACK_ERROR_RATE = 0.01      # Cardosi (FAA/Volpe en route and TRACON studies): fewer than 1% of clearances led to a
                                # communication error. Same in rain (assumption: rain does not change radio quality).
PUSHBACK_MEAN_S = 282.0         # NASA NTRS 20190027149: pushback at Charlotte averaged 4.72 min (also the engine default)
ARRIVAL_STREAMS = ("25L", "26") # the two arrival runways of the default west-flow config; spawns map to the active flow
GATE_SPACING_M = 70.0           # assumption: OSM gate points are 9-40 m apart (closer than an airliner is long), so a day
                                # uses only gates at least this far apart and each flight gets its own gate
SPAWN_MARGIN_NM = 1.0           # assumption: the schedule puts arrivals at the Referee's radar/wake minimum plus this margin
                                # (both levels, so the traffic stays the same); at run time approach control uses the
                                # level's `spacing_margin_nm` instead

# -- weather (both levels): one scheduled wind shift that turns the airport around ------------------------------------------
# PHX normally runs west flow; a shift to an easterly wind forces east flow. Wind is (direction deg, speed kt).
WIND_START = (270, 8)           # assumption: westerly 8 kt, a headwind for west flow (25L/25R/26)
WIND_SHIFT_FRAC = 0.55          # assumption: the shift lands at 55% of the day (~12:36 local, the mid-day lull in the BTS rates)
WIND_STEPS = [(-300.0, 180, 6), (-180.0, 130, 7), (0.0, 90, 14)]   # (s relative to the shift, dir, kt): the wind backs
# through south over 5 minutes; from -180 s west flow has a 5.4 kt tailwind, at 0 a 14 kt tailwind (beyond the limit)
TAILWIND_LIMIT_KT = 10.0        # pilot behaviour: typical transport-category AFM tailwind limit for takeoff and landing.
                                # Above it pilots say "unable" to landing, line-up and takeoff clearances (a pilot limit,
                                # not a Referee rule); arrivals already cleared go around at the 1,800 m decision point.

# -- per level ---------------------------------------------------------------------------------------------------------------
LEVELS: Dict[str, Dict[str, Any]] = {
    "normal_day": {
        "name": "Normal Day", "vis_sm": 10.0, "wx": None, "sky": "FEW200", "temp_c": 29, "dew_c": 2, "altimeter": "A2995",
        "taxi_mult": 1.0,         # no weather effect on taxi speed
        "occupancy_mult": 1.0,    # no effect on landing roll / takeoff roll
        "approach_mult": 1.0,     # full approach speed profile (Vref + 20 m/s easing to Vref)
        "go_around_rate": 0.002,  # Flight Safety Foundation go-around study (2017): about 1-3 go-arounds per 1,000 approaches
        "spacing_margin_nm": 1.0, # approach control's in-trail buffer above the radar/wake minimum (= SPAWN_MARGIN_NM)
    },
    "light_rain": {
        "name": "Light Rain", "vis_sm": 4.0, "wx": "-RA BR", "sky": "BKN015 OVC030", "temp_c": 21, "dew_c": 18,
        "altimeter": "A2983",     # assumption: MVFR light rain and mist
        "taxi_mult": 0.8,         # assumption: pilots taxi about 20% slower on wet pavement in reduced visibility
        "occupancy_mult": 1.15,   # 14 CFR 121.195(d): wet-runway landing distance is 115% of dry; applied to roll time
                                  # (landing roll slower; takeoff acceleration / 1.15^2, see physics)
        "approach_mult": 0.6,     # assumption: stabilised earlier in rain, 60% of the extra speed above Vref on final
        "go_around_rate": 0.05,   # assumption: 25x the dry rate (unstable approaches, runway not in sight); S5 tunes it
        "spacing_margin_nm": 2.0, # assumption: with a BKN015 ceiling approach control runs instrument approaches and adds
                                  # extra miles-in-trail (2 NM instead of 1 above the minimum); the Referee minimum is unchanged
    },
}
LEVEL_IDS = tuple(LEVELS)
DAY_START_ZULU_H = 13           # 06:00 local Phoenix (MST, UTC-7, no DST) = 13:00Z


def tailwind_kt(weather: Dict[str, Any], runway_heading_deg: float) -> float:
    """Tailwind component of the reported (steady) wind on a runway heading; negative = headwind."""
    if not weather.get("wind_kt"):
        return 0.0
    return -float(weather["wind_kt"]) * math.cos(math.radians(float(weather.get("wind_dir") or 0) - runway_heading_deg))


def level_info(level: str, seed: int, duration_s: float) -> Dict[str, Any]:
    """What a controller may know about the level (the same thing a real controller knows from the ATIS and the briefing)."""
    p = LEVELS[level]
    return {"id": level, "name": p["name"], "seed": seed, "duration_s": duration_s, "tailwind_limit_kt": TAILWIND_LIMIT_KT,
            "effects": {k: p[k] for k in ("taxi_mult", "occupancy_mult", "approach_mult", "go_around_rate",
                                             "spacing_margin_nm")}}


# -- weather -----------------------------------------------------------------------------------------------------------------
def _metar(p: Dict[str, Any], t: float, duration_s: float, wind: Tuple[int, int]) -> Dict[str, Any]:
    minutes = int(t / duration_s * len(DAY_HOURS) * 60.0)        # compressed clock: the day spans DAY_HOURS
    hh, mm = DAY_START_ZULU_H + minutes // 60, minutes % 60
    vis = ("%d" % p["vis_sm"]) if float(p["vis_sm"]).is_integer() else str(p["vis_sm"])
    parts = ["KPHX", "15%02d%02dZ" % (hh % 24, mm), "%03d%02dKT" % wind, vis + "SM"] + ([p["wx"]] if p["wx"] else [])
    parts += [p["sky"], "%02d/%02d" % (p["temp_c"], p["dew_c"]), p["altimeter"]]
    return {"metar": " ".join(parts), "wind_dir": wind[0], "wind_kt": wind[1], "gust_kt": None, "vis_sm": float(p["vis_sm"]),
            "wx": p["wx"], "temp_f": round(p["temp_c"] * 9 / 5 + 32, 1)}


class LevelWeather:
    """Scheduled METAR reports plus the level's effect multipliers (read by physics and pilots)."""

    def __init__(self, level: str, duration_s: float) -> None:
        p = LEVELS[level]
        self.taxi_mult, self.occupancy_mult = p["taxi_mult"], p["occupancy_mult"]
        self.approach_mult, self.go_around_rate = p["approach_mult"], p["go_around_rate"]
        self.spacing_margin_nm = p["spacing_margin_nm"]
        shift = WIND_SHIFT_FRAC * duration_s
        self.schedule: List[Tuple[float, Dict[str, Any]]] = [(0.0, _metar(p, 0.0, duration_s, WIND_START))]
        for dt, d, kt in WIND_STEPS:
            t = max(shift + dt, 1.0)
            self.schedule.append((t, _metar(p, t, duration_s, (d, kt))))
        self._i = 0
        self.current: Dict[str, Any] = dict(self.schedule[0][1], t=0.0)

    def tick(self, t: float) -> bool:
        """Advance to the report in force at sim time t; True if a new report was issued this tick."""
        changed = False
        while self._i + 1 < len(self.schedule) and self.schedule[self._i + 1][0] <= t:
            self._i += 1
            self.current = dict(self.schedule[self._i][1], t=round(self.schedule[self._i][0], 1))
            changed = True
        return changed


# -- traffic ------------------------------------------------------------------------------------------------------------------
def hourly_rates() -> Dict[int, Tuple[float, float]]:
    """local hour -> (scheduled arrivals per hour, scheduled departures per hour), BTS average over the month."""
    with open(HOURLY_CSV, newline="") as f:
        return {int(r["local_hour"]): (float(r["sched_arr_per_day"]), float(r["sched_dep_per_day"])) for r in csv.DictReader(f)}


def arrival_gap_s(leader: AircraftType, follower: AircraftType, margin_nm: float = SPAWN_MARGIN_NM) -> float:
    """Time between two arrivals on one final so they are at least the radar / CWT wake minimum (+ margin_nm) apart.

    The gap closes as the leader slows to Vref; flying at Vref or faster, the follower is at least gap x min(Vref) behind."""
    sep = max(RADAR_MIN_M, WAKE_NM.get(leader.cwt, {}).get(follower.cwt, 0.0) * NM) + margin_nm * NM
    return sep / min(leader.vref_mps, follower.vref_mps)


_GATES: Dict[int, Tuple[List[str], List[str]]] = {}


def _gates(graph: Any) -> Tuple[List[str], List[str]]:
    """(airline gates, GA stands), each thinned so no two are closer than GATE_SPACING_M."""
    from sim.scenarios.generate import ga_stands, usable_gates
    if id(graph) not in _GATES:
        def spaced(names: List[str]) -> List[str]:
            out: List[str] = []
            for n in names:
                xy = graph.stands[n].xy
                if all(math.hypot(xy[0] - graph.stands[o].xy[0], xy[1] - graph.stands[o].xy[1]) >= GATE_SPACING_M for o in out):
                    out.append(n)
            return out
        _GATES[id(graph)] = (spaced(usable_gates(graph)), spaced(ga_stands(graph)))
    return _GATES[id(graph)]


def _pick_type(rng: np.random.Generator) -> Tuple[str, str]:
    u = rng.random()
    if u < HEAVY_SHARE:
        return HEAVIES[int(rng.integers(len(HEAVIES)))]
    if u < HEAVY_SHARE + LIGHT_SHARE:
        return "GA", LIGHTS[int(rng.integers(len(LIGHTS)))]
    names = list(AIRLINES)
    w = np.array([AIRLINES[n][0] for n in names], dtype=float)
    airline = names[int(rng.choice(len(names), p=w / w.sum()))]
    fleet = AIRLINES[airline][1]
    return airline, fleet[int(rng.integers(len(fleet)))]


def build_scenario(level: str, seed: int, duration_s: Optional[float] = None, graph: Any = None) -> Scenario:
    """The traffic for a level and seed. The traffic depends on the seed only (same day in both levels)."""
    if level not in LEVELS:
        raise ValueError("unknown level %r; expected one of %s" % (level, ", ".join(LEVEL_IDS)))
    if graph is None:
        from sim.graph.model import AirportGraph
        graph = AirportGraph.load()
    dur = float(duration_s or DEFAULT_DURATION_S)
    rng = np.random.default_rng([seed, 7])
    rates = hourly_rates()
    seg = dur / len(DAY_HOURS)
    arr_t: List[float] = []
    dep_t: List[float] = []
    for i, h in enumerate(DAY_HOURS):           # each real hour becomes `seg` sim seconds at that hour's real rate
        for rate, out in ((rates[h][0], arr_t), (rates[h][1], dep_t)):
            n = int(rng.poisson(rate * DEMAND_SCALE * seg / 3600.0))
            out.extend(float(x) for x in i * seg + rng.uniform(0.0, seg, n))
    arr_t.sort()
    dep_t.sort()
    kinds = [("arrival", t + ARRIVAL_LEAD_S) for t in arr_t] + [("departure", t) for t in dep_t]
    picks = [_pick_type(rng) for _ in kinds]
    arr_idx = [i for i, (k, _) in enumerate(kinds) if k == "arrival"]
    def heavy(ty: str) -> bool:
        return get_type(ty).wtc.startswith("H")

    for need, test, pool in ((MIN_HEAVY_ARRIVALS, heavy, HEAVIES),
                             (MIN_LIGHT_ARRIVALS, lambda ty: get_type(ty).light, [("GA", t) for t in LIGHTS])):
        missing = need - sum(1 for i in arr_idx if test(picks[i][1]))
        plain = [i for i in arr_idx if not heavy(picks[i][1]) and not get_type(picks[i][1]).light]
        for i in rng.permutation(plain)[:max(0, missing)]:
            picks[int(i)] = pool[int(rng.integers(len(pool)))]
    gates, ga = _gates(graph)
    airline_n = sum(1 for a, _ in picks if a != "GA")
    order = [str(g) for g in rng.permutation(gates)]     # ponytail: gates repeat past len(gates) flights (44 today)
    airline_gates = [order[i % len(order)] for i in range(airline_n)]
    ga_gates = [str(g) for g in rng.choice(ga, size=len(picks) - airline_n, replace=False)]
    numbers = rng.choice(np.arange(100, 4900), size=len(picks), replace=False)
    flights: List[Flight] = []
    for (kind, t), (airline, ty), num in zip(kinds, picks, numbers):
        if airline == "GA":
            cs = "N%d%s" % (int(num), "".join(chr(65 + int(c)) for c in rng.integers(0, 26, 2)))
            gate = ga_gates.pop()
        else:
            cs, gate = "%s%d" % (airline, int(num)), airline_gates.pop()
        flights.append(Flight(callsign=cs, airline=airline, type=ty, kind=kind, scheduled_sim_s=round(t, 1), gate=gate))
    # arrivals: earliest legal slot on either final, in-trail at the radar / wake minimum plus margin
    last: Dict[str, Optional[Tuple[float, AircraftType]]] = {s: None for s in ARRIVAL_STREAMS}
    for f in sorted((f for f in flights if f.kind == "arrival"), key=lambda f: f.scheduled_sim_s):
        ty = get_type(f.type)
        options = []
        for s in ARRIVAL_STREAMS:
            prev = last[s]
            slot = f.scheduled_sim_s if prev is None else max(f.scheduled_sim_s, prev[0] + arrival_gap_s(prev[1], ty))
            options.append((slot, prev[0] if prev else -1e9, s))
        slot, _, stream = min(options)
        f.scheduled_sim_s, f.runway_preference = round(slot, 1), stream
        last[stream] = (f.scheduled_sim_s, ty)
    flights.sort(key=lambda f: (f.scheduled_sim_s, f.callsign))
    return Scenario(name=level, seed=seed, duration_s=dur, flights=flights, pushback_mean_s=PUSHBACK_MEAN_S,
                    readback_error_rate=READBACK_ERROR_RATE)
