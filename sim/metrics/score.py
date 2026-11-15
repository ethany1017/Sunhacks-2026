"""Scoring: pure functions over the event log. These describe how the run went; they never gate anything."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

THROUGHPUT_WINDOW_S = 600.0
EventList = List[Dict[str, Any]]


def _ts(ev: Dict[str, Any]) -> float:
    return ev["sim_time_ms"] / 1000.0


def compute_score(events: EventList, t: float, readback_truth: Optional[EventList] = None) -> Dict[str, Any]:
    """readback_truth is the sim's private record of wrong readbacks (never shown to a controller)."""
    truth = readback_truth or []
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
        "readback_errors_accepted": sum(1 for r in truth if not r["caught"]),
        "readback_errors_caught": sum(1 for r in truth if r["caught"]),
        "completed": len(done),
        "avg_delay_s": round(sum(delays) / len(delays), 1) if delays else 0.0,
        "throughput_per_10min": sum(1 for e in ops if t - THROUGHPUT_WINDOW_S < _ts(e) <= t),
        "clearance_reversals": sum(1 for e in amended if e["data"].get("reversal")),
        "clearance_amendments": len(amended),
        "go_arounds": len(kinds.get("go_around", [])),
        "diversions": len(kinds.get("aircraft.diverted", [])),
        "total_delay_s": round(sum(delays), 1),
        "runway_operations": len(ops),
        "avg_landing_occupancy_s": round(sum(occupancy) / len(occupancy), 1) if occupancy else None,
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
            ("Readback errors caught", score["readback_errors_caught"]),
            ("Clearance reversals", score["clearance_reversals"]), ("Go-arounds", score["go_arounds"]),
            ("Diversions", score["diversions"]), ("Aircraft completed", score["completed"]),
            ("Total delay (s)", score["total_delay_s"]), ("Average delay (s)", score["avg_delay_s"]),
            ("Runway operations", score["runway_operations"]),
            ("Throughput (ops / 10 min)", score["throughput_per_10min"]),
            ("Avg landing occupancy (s)", score["avg_landing_occupancy_s"]),
            ("Decisions recorded", score["decisions"])]
    width = max(len(r[0]) for r in rows)
    line = "+-%s-+-%s-+" % ("-" * width, "-" * 10)
    body = "\n".join("| %s | %10s |" % (k.ljust(width), "n/a" if v is None else v) for k, v in rows)
    return "\n".join([line, "| %s | %10s |" % ("Score".ljust(width), "value"), line, body, line])
