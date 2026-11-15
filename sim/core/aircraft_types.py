"""Aircraft type data from kphx_aircraft_normalized.csv plus kinematic placeholders (see docs/ASSUMPTIONS.md)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

CSV_PATH = Path(__file__).resolve().parents[2] / "data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv"
FT = 0.3048
KT = 0.514444


@dataclass(frozen=True)
class AircraftType:
    icao: str
    wtc: str            # ICAO wake turbulence letter
    cwt: str            # FAA consolidated wake turbulence category A..I
    recat: str          # RECAT 1.5 category A..F
    wingspan_m: float
    length_m: float
    vref_mps: float     # approach speed from the CSV (ACD Vref proxy)
    vlof_mps: float     # lift-off speed (placeholder: 1.13 x Vref)
    takeoff_accel: float  # m/s^2 average over the roll (placeholder by weight class)
    landing_decel: float  # m/s^2 average braking (placeholder by weight class)

    @property
    def light(self) -> bool:
        return self.wtc.startswith("L")


def _num(v: str, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def _table() -> Dict[str, AircraftType]:
    out: Dict[str, AircraftType] = {}
    with open(CSV_PATH, newline="") as f:
        for r in csv.DictReader(f):
            span_ft = _num(r["wingspan_ft_no_winglets"], 0) or _num(r["wingspan_ft_with_winglets"], 118)
            vref = _num(r["approach_speed_kt_acd_vref_proxy"], 135) * KT
            wtc = r["icao_wtc_letter"]
            if wtc.startswith("H"):
                accel, decel = 2.1, 2.0
            elif wtc.startswith("L"):
                accel, decel = 1.5, 1.4
            else:
                accel, decel = 2.4, 2.2
            out[r["icao_type"]] = AircraftType(
                icao=r["icao_type"], wtc=wtc, cwt=r["faa_cwt_A_to_I"], recat=r["recat15_A_to_F"],
                wingspan_m=round(span_ft * FT, 1), length_m=round(_num(r["length_ft"], 120) * FT, 1),
                vref_mps=vref, vlof_mps=round(1.13 * vref, 1), takeoff_accel=accel, landing_decel=decel)
    return out


def get_type(icao: str) -> AircraftType:
    t = _table()
    return t[icao] if icao in t else t["B738"]


def known_types() -> Dict[str, AircraftType]:
    return dict(_table())


def lookup(icao: str) -> Optional[AircraftType]:
    return _table().get(icao)
