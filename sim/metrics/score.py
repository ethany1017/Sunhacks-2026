"""Scoring: pure functions over the event log. These describe how the run went; they never gate anything."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

THROUGHPUT_WINDOW_S = 600.0
EventList = List[Dict[str, Any]]


def _ts(ev: Dict[str, Any]) -> float:
    return ev["sim_time_ms"] / 1000.0


def recovery_time_s(events: EventList) -> Optional[float]:
    """Seconds from visibility returning to 1 SM or better until the first takeoff after the haboob; None if n/a."""
    hit: Optional[float] = None
    clear: Optional[float] = None
    for ev in events:
        if ev["kind"] != "weather.update":
            continue
        if ev["data"].get("low_vis") and hit is None:
            hit = _ts(ev)
        elif hit is not None and clear is None and not ev["data"].get("low_vis"):
            clear = _ts(ev)
    if clear is None:
        return None
    after = [_ts(e) for e in events if e["kind"] == "aircraft.airborne" and _ts(e) >= clear]
    return round(after[0] - clear, 1) if after else None


def compute_score(events: EventList, t: float) -> Dict[str, Any]:
    kinds: Dict[str, EventList] = {}
    for ev in events:
        kinds.setdefault(ev["kind"], []).append(ev)
    done = kinds.get("aircraft.completed", [])
    delays = [e["data"]["delay_s"] for e in done]
    ops = kinds.get("aircraft.touchdown", []) + kinds.get("aircraft.airborne", [])
    amended = kinds.get("clearance.amended", [])
    occupancy = _occupancy_times(events)
    return {
        "separation_losses": len(kinds.get("separation_loss", [])),
        "runway_conflicts": len(kinds.get("conflict", [])),
        "deadlocks": len(kinds.get("deadlock", [])),
        "readback_errors_accepted": len(kinds.get("readback.error_accepted", [])),
        "completed": len(done),
        "avg_delay_s": round(sum(delays) / len(delays), 1) if delays else 0.0,
        "throughput_per_10min": sum(1 for e in ops if t - THROUGHPUT_WINDOW_S < _ts(e) <= t),
        "clearance_reversals": sum(1 for e in amended if e["data"].get("reversal")),
        "clearance_amendments": len(amended),
        "go_arounds": len(kinds.get("go_around", [])),
        "avg_landing_occupancy_s": round(sum(occupancy) / len(occupancy), 1) if occupancy else None,
        "recovery_time_s": recovery_time_s(events),
        "decisions": len(kinds.get("decision", [])),
    }


def _occupancy_times(events: EventList) -> List[float]:
    """Landing runway occupancy: touchdown until the aircraft has left the pavement."""
    touchdown: Dict[Any, float] = {}
    out: List[float] = []
    for ev in events:
        if ev["kind"] == "aircraft.touchdown":
            touchdown[ev["entity_id"]] = _ts(ev)
        elif ev["kind"] == "runway.exit" and ev["entity_id"] in touchdown:
            out.append(_ts(ev) - touchdown.pop(ev["entity_id"]))
    return out


def format_table(score: Dict[str, Any]) -> str:
    rows = [("Separation losses", score["separation_losses"]), ("Runway conflicts", score["runway_conflicts"]),
            ("Taxi deadlocks", score["deadlocks"]), ("Readback errors accepted", score["readback_errors_accepted"]),
            ("Clearance reversals", score["clearance_reversals"]), ("Go-arounds", score["go_arounds"]),
            ("Aircraft completed", score["completed"]), ("Average delay (s)", score["avg_delay_s"]),
            ("Throughput (ops / 10 min)", score["throughput_per_10min"]),
            ("Avg landing occupancy (s)", score["avg_landing_occupancy_s"]),
            ("Recovery after haboob (s)", score["recovery_time_s"]), ("Decisions recorded", score["decisions"])]
    width = max(len(r[0]) for r in rows)
    line = "+-%s-+-%s-+" % ("-" * width, "-" * 10)
    body = "\n".join("| %s | %10s |" % (k.ljust(width), "n/a" if v is None else v) for k, v in rows)
    return "\n".join([line, "| %s | %10s |" % ("Score".ljust(width), "value"), line, body, line])
