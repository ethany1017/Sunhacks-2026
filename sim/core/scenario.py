"""Scenario file loading (versioned JSON, see sim/scenarios/)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

KINDS = ("arrival", "departure")


@dataclass
class Flight:
    callsign: str
    airline: str
    type: str
    kind: str
    scheduled_sim_s: float   # arrival: threshold time (ETA); departure: ready for pushback
    gate: str
    runway_preference: Optional[str] = None


@dataclass
class VehicleSpec:
    id: str
    route: List[str]
    speed_mps: float = 6.0


@dataclass
class Scenario:
    name: str
    seed: int
    duration_s: float
    flights: List[Flight]
    vehicles: List[VehicleSpec] = field(default_factory=list)
    start_sim_s: float = 0.0
    pushback_mean_s: float = 282.0     # 4.7 min default (BUILD_PROMPT 5.2); scenarios may compress for the demo
    readback_error_rate: float = 0.08
    observation_delay_s: float = 1.0
    observation_noise_m: float = 3.0

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        raw = json.loads(Path(path).read_text())
        flights = [Flight(**f) for f in raw.pop("flights")]
        vehicles = [VehicleSpec(**v) for v in raw.pop("vehicles", [])]
        for f in flights:
            if f.kind not in KINDS:
                raise ValueError("flight %s has unknown kind %r" % (f.callsign, f.kind))
        return cls(flights=flights, vehicles=vehicles, **raw)
