"""Runway reservations and occupancy history: readable state, not enforced rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Reservation:
    runway: str
    owner: str
    kind: str       # landing | takeoff | line_up | crossing
    start_s: float
    end_s: float


class Reservations:
    def __init__(self, runways: List[str]) -> None:
        self.items: Dict[str, List[Reservation]] = {r: [] for r in runways}
        self.open: Dict[str, Dict[str, float]] = {r: {} for r in runways}       # runway -> owner -> start
        self.closed: Dict[str, List[Dict[str, Any]]] = {r: [] for r in runways}  # finished occupancy intervals

    # -- reservations ----------------------------------------------------------------
    def set(self, runway: str, owner: str, kind: str, start_s: float, end_s: float) -> None:
        """Create or move the owner's reservation of this kind."""
        for r in self.items[runway]:
            if r.owner == owner and r.kind == kind:
                r.start_s, r.end_s = start_s, end_s
                return
        self.items[runway].append(Reservation(runway, owner, kind, start_s, end_s))

    def release(self, owner: str, kind: Optional[str] = None, runway: Optional[str] = None) -> None:
        for rw, lst in self.items.items():
            if runway is None or rw == runway:
                self.items[rw] = [r for r in lst if not (r.owner == owner and (kind is None or r.kind == kind))]

    def overlapping(self, runway: str, t0: float, t1: float, exclude: Optional[str] = None) -> List[Reservation]:
        return [r for r in self.items[runway] if r.owner != exclude and r.start_s < t1 and r.end_s > t0]

    # -- occupancy ---------------------------------------------------------------------
    def enter(self, runway: str, owner: str, t: float) -> None:
        self.open[runway].setdefault(owner, t)

    def leave(self, runway: str, owner: str, t: float) -> None:
        start = self.open[runway].pop(owner, None)
        if start is not None:
            self.closed[runway].append({"callsign": owner, "start_s": start, "end_s": t, "kind": "occupied"})

    def occupants(self, runway: str) -> List[str]:
        return sorted(self.open[runway])

    def view(self, runway: str, t: float) -> Dict[str, Any]:
        occ = self.occupants(runway)
        windows = [dict(w) for w in self.closed[runway] if w["end_s"] > t - 180]
        windows += [{"callsign": o, "start_s": s, "end_s": round(t, 1), "kind": "occupied"} for o, s in sorted(self.open[runway].items())]
        future = [r for r in self.items[runway] if r.end_s > t and r.start_s < t + 180]
        windows += [{"callsign": r.owner, "start_s": round(r.start_s, 1), "end_s": round(r.end_s, 1), "kind": "reserved"}
                    for r in sorted(future, key=lambda x: (x.start_s, x.owner))]
        active = [r for r in self.items[runway] if r.start_s <= t + 1.0 < r.end_s]
        if occ:
            status, owner = "occupied", occ[0]
        elif active:
            status, owner = "reserved", sorted(active, key=lambda r: r.start_s)[0].owner
        else:
            status, owner = "free", None
        return {"id": runway, "name": runway, "status": status, "owner": owner, "occupancy": windows}
