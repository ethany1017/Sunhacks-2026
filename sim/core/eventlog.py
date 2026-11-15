"""Append-only JSONL event log using the P2.4 event envelope (deterministic: no wall-clock fields)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO


class EventLog:
    def __init__(self, run_id: str, run_dir: Optional[Path] = None, airport: str = "KPHX") -> None:
        self.run_id = run_id
        self.airport = airport
        self.events: List[Dict[str, Any]] = []
        self._fh: Optional[TextIO] = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self.path: Optional[Path] = run_dir / "events.jsonl"
            self._fh = open(self.path, "w")
        else:
            self.path = None

    def emit(self, kind: str, entity_id: Optional[str], t_ms: int, data: Dict[str, Any]) -> Dict[str, Any]:
        seq = len(self.events) + 1
        ev = {
            "schema_version": 1, "run_id": self.run_id, "mode": "simulation", "event_id": "event-%d" % seq,
            "airport_icao": self.airport, "kind": kind, "entity_id": entity_id, "source_id": "sim-core",
            "source_record_id": None, "source_version": "1", "observed_at": None, "issued_at": None,
            "received_at": None, "valid_from": None, "valid_to": None, "sim_time_ms": t_ms,
            "source_sequence": seq, "quality": "valid", "quality_reason": "simulation_clock_only",
            "raw_ref": None, "data": data,
        }
        self.events.append(ev)
        if self._fh is not None:
            self._fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n")
        return ev

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def brief(ev: Dict[str, Any]) -> Dict[str, Any]:
    """The compact event shape sent over the WebSocket."""
    return {"id": ev["event_id"], "t": ev["sim_time_ms"] / 1000.0, "kind": ev["kind"],
            "entity_id": ev["entity_id"], "data": ev["data"]}
