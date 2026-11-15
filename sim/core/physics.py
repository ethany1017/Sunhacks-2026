"""Movement physics: smooth kinematics, car-following, final approach, landing and takeoff rolls.

The simulator never refuses a clearance for being unsafe. Ground aircraft hold behind an occupied runway or a
leader (60 m gap) because that is what moving bodies do; everything else is allowed to go wrong and is logged.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np

from sim.core.clock import TICK_S
from sim.core.entities import Aircraft
from sim.graph.geo import angle_diff

if TYPE_CHECKING:
    from sim.core.engine import Simulation

A_ACC = 0.9            # m/s^2 taxi acceleration
A_BRAKE = 1.3          # m/s^2 planned braking
A_HARD = 3.0           # m/s^2 emergency braking limit
STANDOFF_M = 60.0      # car-following gap between aircraft
CORRIDOR_M = 30.0      # lateral distance for "on my path"
HOLD_OFFSET_M = 8.0    # nose clearance short of the runway pavement at a hold
CROSS_EST_S = 25.0     # time a crossing is assumed to need when checking reservations
PUSH_V = 1.1           # m/s pushback speed
GROUND_TURN_RATE = 25.0  # deg/s
AIR_TURN_RATE = 3.0
GLIDE = math.tan(math.radians(3.0))
FINAL_START_M = 11000.0
GO_AROUND_A_M = 1800.0
EXIT_SPEED = 10.0      # m/s at the chosen runway exit
LIFTOFF_ALT_RATE = 8.0
GO_AROUND_CLIMB_S = 35.0
REENTRY_M = 8000.0


def approach(v: float, target: float, up: float, down: float) -> float:
    if target > v:
        return min(target, v + up * TICK_S)
    return max(target, v - down * TICK_S)


def turn_toward(h: float, target: float, rate: float) -> float:
    d = angle_diff(h, target)
    step = rate * TICK_S
    return (h + max(-step, min(step, d))) % 360.0


def unit(h: float) -> np.ndarray:
    r = math.radians(h)
    return np.array([math.sin(r), math.cos(r)])


class Physics:
    def __init__(self, sim: "Simulation") -> None:
        self.sim = sim

    # -- ground movement -----------------------------------------------------------------
    def stop_point(self, ac: Aircraft) -> Tuple[float, str]:
        """Furthest path distance the aircraft may travel to now, with the reason it must stop there."""
        p = ac.path
        assert p is not None
        half = ac.length_m / 2
        stop, why = p.length, "end"
        if ac.hold_s is not None and ac.hold_s < stop:
            stop, why = max(ac.hold_s, ac.s), "hold"
        if ac.kind == "vehicle":
            return stop, why
        for i, run in enumerate(p.runs):
            if run.s1 <= ac.s - half:
                continue
            entry = run.s0 - (half + HOLD_OFFSET_M)
            if ac.s > entry + 0.5:
                continue  # already committed to this pavement
            if i not in ac.cleared_runs and not ac.ignore_hold:
                cand, reason = max(entry, ac.s), "run"
            elif self.sim.runway_busy(run.runway, ac):
                cand, reason = max(entry, ac.s), "busy"
            else:
                continue
            if cand < stop:
                stop, why = cand, reason
            break
        return stop, why

    def leader_limit(self, ac: Aircraft, movers: List[Aircraft]) -> Tuple[float, Optional[str]]:
        """Speed allowed by the nearest blocking mover on this aircraft's path ahead."""
        p = ac.path
        assert p is not None
        pos = np.array([ac.x, ac.y])
        look = STANDOFF_M + ac.length_m + 60.0 + ac.speed ** 2 / (2 * A_BRAKE)
        cands = [o for o in movers if o is not ac and math.hypot(o.x - ac.x, o.y - ac.y) < look + o.length_m]
        if not cands:
            return 99.0, None
        ss = ac.s + np.arange(2.0, look, 8.0)
        samples = p.xy_at(ss)
        fwd = unit(p.heading_at(ac.s))
        best, who = 99.0, None
        for o in cands:
            op = np.array([o.x, o.y])
            if o.path is None or float(np.dot(op - pos, fwd)) <= 0.0:
                continue
            d = np.hypot(*(samples - op).T)
            k = int(np.argmin(d))
            if d[k] > CORRIDOR_M:
                continue
            odir = unit(o.path.heading_at(o.s))
            mine = unit(p.heading_at(float(ss[k])))
            cosang = float(np.dot(mine, odir))
            follower = cosang > 0.57
            if not follower and cosang > -0.57 and o.speed >= 0.3 and o.seq >= ac.seq:
                continue  # crossing traffic: only the lower-priority aircraft yields
            gap = float(ss[k]) - ac.s - (ac.length_m + o.length_m) / 2 - STANDOFF_M
            allowed = (o.speed * cosang if follower else 0.0) + math.sqrt(2 * A_BRAKE * max(gap, 0.0))
            if allowed < best:
                best, who = allowed, o.callsign
        return best, who

    def step_ground(self, ac: Aircraft, movers: List[Aircraft]) -> None:
        sim, p = self.sim, ac.path
        assert p is not None
        stop, why = self.stop_point(ac)
        v_stop = math.sqrt(2 * A_BRAKE * max(stop - ac.s, 0.0))
        v_lim = p.lookahead_limit(ac.s, ac.speed ** 2 / (2 * A_BRAKE) + 10.0)
        v_lim = v_lim * (sim.weather.taxi_mult if ac.kind != "vehicle" else 1.0)
        if ac.kind == "vehicle":
            v_lim = min(v_lim, ac.patrol_speed)  # type: ignore[attr-defined]
        if ac.phase in ("landing_roll", "runway_exit") and ac.touchdown_t is not None and ac.s < ac.roll_s_exit:
            v_lim = self.roll_target(ac)
        v_lead, who = self.leader_limit(ac, movers)
        target = min(v_lim, v_stop, v_lead)
        ac.blocked_by = who if v_lead < min(v_lim, v_stop) * 0.9 else None
        if ac.blocked_by is None and why in ("run", "busy") and v_stop < v_lim * 0.9:
            ac.blocked_by = "runway:" + why
        ac.stop_s, ac.stop_why = stop, why
        rolling = ac.touchdown_t is not None and ac.s < ac.roll_s_exit
        ac.speed = approach(ac.speed, target, 0.0 if rolling else A_ACC, A_HARD)
        ac.s = min(ac.s + ac.speed * TICK_S, max(stop, ac.s))
        self._place(ac)

    def _place(self, ac: Aircraft) -> None:
        p = ac.path
        assert p is not None
        xy = p.xy_at(ac.s)
        ac.x, ac.y = float(xy[0]), float(xy[1])
        if ac.phase == "pushback":
            return  # nose stays pointed at the gate while being pushed back
        ac.heading = turn_toward(ac.heading, p.heading_at(ac.s, 6.0), GROUND_TURN_RATE)

    def roll_target(self, ac: Aircraft) -> float:
        """Landing roll speed profile: constant deceleration from touchdown speed to the exit speed at the exit."""
        v_td, s_exit = ac.roll_vtd, ac.roll_s_exit
        a = (v_td ** 2 - EXIT_SPEED ** 2) / (2 * max(s_exit, 1.0))
        v = math.sqrt(max(EXIT_SPEED ** 2, v_td ** 2 - 2 * a * ac.s))
        return v / self.sim.weather.occupancy_mult

    # -- pushback ---------------------------------------------------------------------------
    def step_pushback(self, ac: Aircraft, t: float) -> bool:
        """Advance pushback; returns True when the aircraft has reached the lane."""
        p = ac.path
        assert p is not None
        if t - (ac.pushback_started_t or t) < ac.pushback_wait_s:
            return False
        remaining = p.length - ac.s
        ac.speed = approach(ac.speed, min(PUSH_V, math.sqrt(2 * 0.4 * max(remaining, 0.0))), 0.5, 0.8)
        ac.s = min(ac.s + ac.speed * TICK_S, p.length)
        self._place(ac)
        if ac.s >= p.length - 0.05:
            ac.speed = 0.0
            return True
        return False

    # -- airborne ------------------------------------------------------------------------------
    def step_final(self, ac: Aircraft) -> bool:
        """Fly the approach toward the runway threshold. Returns True when the threshold is crossed."""
        sim = self.sim
        rw, info, _ = sim.graph.end_info(ac.runway or "")
        thr = sim.graph.node_xy[info["node"]]
        u = unit(info["heading_deg"])
        rel = np.array([ac.x, ac.y]) - thr
        a = -float(np.dot(rel, u))                      # distance before the threshold along the approach
        lat = float(rel[0] * u[1] - rel[1] * u[0])
        if ac.wp is not None:
            target = np.array(ac.wp)
            if float(np.hypot(*(target - np.array([ac.x, ac.y])))) < 900.0:
                ac.wp = None
        else:
            lead = max(1500.0, 8 * abs(lat))
            target = thr - u * max(0.0, a - lead)
        want = math.degrees(math.atan2(target[0] - ac.x, target[1] - ac.y)) % 360.0
        ac.heading = turn_toward(ac.heading, want, AIR_TURN_RATE)
        v_t = ac.type.vref_mps + min(max((a - 1500.0) / 9000.0, 0.0), 1.0) * 20.0
        ac.speed = approach(ac.speed, v_t, 1.0, 1.0)
        step = unit(ac.heading) * ac.speed * TICK_S
        ac.x += float(step[0])
        ac.y += float(step[1])
        ac.alt_m = approach(ac.alt_m, max(a, 0.0) * GLIDE, 6.0, 6.0)
        return a <= 0.5 and ac.wp is None

    def approach_distance(self, ac: Aircraft) -> float:
        rw, info, _ = self.sim.graph.end_info(ac.runway or "")
        thr = self.sim.graph.node_xy[info["node"]]
        return -float(np.dot(np.array([ac.x, ac.y]) - thr, unit(info["heading_deg"])))

    def step_go_around(self, ac: Aircraft, t: float) -> bool:
        """Straight-ahead climb; returns True when the aircraft turns back to final."""
        ac.speed = approach(ac.speed, ac.type.vref_mps * 1.25, 1.5, 1.5)
        step = unit(ac.heading) * ac.speed * TICK_S
        ac.x += float(step[0])
        ac.y += float(step[1])
        ac.alt_m = approach(ac.alt_m, 450.0, 8.0, 8.0)
        return t >= ac.climb_until

    # -- takeoff -------------------------------------------------------------------------------
    def step_takeoff(self, ac: Aircraft) -> None:
        p = ac.path
        assert p is not None
        occ = self.sim.weather.occupancy_mult
        if ac.phase == "takeoff_roll":
            ac.speed += ac.type.takeoff_accel / (occ * occ) * TICK_S
            if ac.speed >= ac.type.vlof_mps:
                self.sim.liftoff(ac)
        else:
            ac.speed = approach(ac.speed, ac.type.vlof_mps + 25.0, 1.5, 1.5)
            ac.alt_m += LIFTOFF_ALT_RATE * TICK_S
        ac.s = min(ac.s + ac.speed * TICK_S, p.length)
        self._place(ac)
