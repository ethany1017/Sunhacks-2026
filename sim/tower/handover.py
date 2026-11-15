"""Hand the current simulation state to a freshly built TowerController so it neither repeats nor forgets clearances.

Used when another controller hands the airport back to the tower mid-run: the tower keeps
per-aircraft progress flags, and without them it would re-issue runway assignments, taxi and pushback clearances.
"""
from __future__ import annotations

from typing import Any, Dict

from sim.tower.controller import TowerController
from sim.core.engine import Simulation

LIVE_STATES = ("issued", "acknowledged")


def prime_tower(bl: TowerController, sim: Simulation) -> TowerController:
    live = sorted((a for a in sim.aircraft.values() if a.kind != "vehicle" and not a.removed), key=lambda a: a.seq)
    for i, ac in enumerate(live):
        bl.order[ac.callsign] = i
    for e in sim.log.events:
        if e["kind"] == "aircraft.airborne":
            cs = e["entity_id"]
            if cs in sim.aircraft:
                bl.last_airborne[e["data"]["runway"]] = (e["sim_time_ms"] / 1000.0, sim.aircraft[cs].type.wtc)
    by_cs: Dict[str, Any] = {}
    for c in sim.clearances.values():
        if c.state != "cancelled":
            by_cs.setdefault(c.callsign, []).append(c)
    for ac in live:
        st = bl.assigned.setdefault(ac.callsign, {})
        hist = by_cs.get(ac.callsign, [])
        acts = {c.action for c in hist}
        if ac.kind == "arrival":
            if ac.phase == "final" or ac.phase == "go_around":
                if ac.runway:
                    st["end"] = ac.runway
                    pav = sim.runway_pavement(ac.runway)
                    if pav and ac.phase == "final":
                        eta = sim.physics.approach_distance(ac) / max(ac.speed, 30.0)
                        bl.last_arrival_slot[pav] = max(bl.last_arrival_slot.get(pav, -1e9), sim.t + eta)
                if ac.landing_cleared or "cleared_to_land" in acts:
                    st["landing"] = True
            else:
                if "contact_tower_or_ground" in acts and ac.phase in ("taxi_in", "runway_exit"):
                    st["ground"] = True
                if "taxi_via" in acts:
                    st["ground"] = st["taxi"] = True
                for c in hist:
                    if c.action == "cross_runway" and c.runway:
                        pav = sim.runway_pavement(c.runway)
                        if pav:
                            st["cross-%s" % pav] = True
        elif ac.kind == "departure":
            if ac.dep_end:
                st.update(end=ac.dep_end, entry=ac.entry_node, via=[], why="handed over mid-flight", alts=[])
            if "pushback_approved" in acts:
                st["push"] = True
            if "taxi_via" in acts:
                st["taxi"] = True
            if "line_up_and_wait" in acts:
                st["luaw"] = True
            if "cleared_for_takeoff" in acts or ac.takeoff_cleared:
                st["to"] = True
            if ac.phase in ("airborne", "takeoff_roll") or ac.done:
                st["airborne"] = True
    for c in sim.clearances.values():
        if c.state in LIVE_STATES:
            bl.expected[c.id] = c.runway
            if c.action in ("cleared_to_land", "cross_runway", "line_up_and_wait", "cleared_for_takeoff") and c.runway:
                pav = sim.runway_pavement(c.runway)
                if pav:
                    bl.claims[(pav, c.callsign)] = c.id
    return bl
