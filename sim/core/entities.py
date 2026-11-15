"""Simulation entities: aircraft, ground vehicles, clearances and voice lines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sim.core.aircraft_types import AircraftType
from sim.graph.model import Track

GROUND_PHASES = ("taxi_in", "taxi_out", "pushback", "hold_short", "line_up", "runway_exit", "vehicle_moving")
STATIONARY_PHASES = ("at_gate", "parked")


@dataclass
class Clearance:
    id: str
    callsign: str
    action: str
    text: str
    params: Dict[str, Any]
    runway: Optional[str]            # runway end the readback must repeat (for hold/cross/line-up/take-off/land)
    issued_t: float
    state: str = "issued"            # issued | acknowledged | complete | amended | cancelled
    ack_t: Optional[float] = None
    done_t: Optional[float] = None
    voice_id: Optional[str] = None
    version: int = 1

    def view(self) -> Dict[str, Any]:
        return {"id": self.id, "callsign": self.callsign, "action": self.action, "text": self.text, "state": self.state,
                "issued_t": self.issued_t, "ack_t": self.ack_t, "done_t": self.done_t}


@dataclass
class Aircraft:
    callsign: str
    type: AircraftType
    kind: str                         # arrival | departure | vehicle
    airline: str
    seq: int                          # right-of-way priority at crossings (lower wins)
    gate: Optional[str] = None
    runway: Optional[str] = None      # runway end for landing / takeoff
    phase: str = "at_gate"
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    alt_m: float = 0.0
    path: Optional[Track] = None
    s: float = 0.0
    cleared_runs: Set[int] = field(default_factory=set)
    hold_s: Optional[float] = None    # explicit hold_position stop point along the path
    blocked_by: Optional[str] = None
    stopped_since: Optional[float] = None
    scheduled_s: float = 0.0
    spawn_t: float = 0.0
    ready: bool = False
    # arrivals
    landing_cleared: bool = False
    exit_node: Optional[str] = None
    wp: Optional[Any] = None          # re-entry waypoint after a go-around
    climb_until: float = 0.0
    touchdown_t: Optional[float] = None
    # departures
    dep_end: Optional[str] = None
    entry_node: Optional[str] = None
    takeoff_cleared: bool = False
    pushback_wait_s: float = 0.0
    pushback_started_t: Optional[float] = None
    airborne_t: Optional[float] = None
    # bookkeeping
    done: bool = False
    removed: bool = False
    frequency: str = "ground"
    status_text: str = ""
    hold_info: Optional[Dict[str, Any]] = None
    pending_readback: bool = False
    unimpeded_s: float = 0.0
    stop_s: float = 0.0
    stop_why: str = "end"
    roll_vtd: float = 0.0
    roll_s_exit: float = 0.0
    dep_pushback_s: float = 0.0

    @property
    def length_m(self) -> float:
        return self.type.length_m

    @property
    def wingspan_m(self) -> float:
        return self.type.wingspan_m


@dataclass
class Vehicle(Aircraft):
    patrol: List[str] = field(default_factory=list)
    patrol_speed: float = 6.0
    forward: bool = True


@dataclass
class VoiceLine:
    id: str
    t: float
    speaker: str
    callsign: str
    text: str
    status: str = "ok"

    def view(self) -> Dict[str, Any]:
        return {"id": self.id, "t": self.t, "speaker": self.speaker, "callsign": self.callsign, "text": self.text,
                "status": self.status}


@dataclass
class Readback:
    id: str
    callsign: str
    clearance_id: str
    voice_id: str
    parsed: Dict[str, Any]
    error: Optional[str]
    due_t: float
    retry: bool = False
    delivered: bool = False
    delivered_t: float = 0.0
    resolved: bool = False
