"""Replay of the Aug 25-26, 2025 KPHX haboob METAR/SPECI series on the simulation clock."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

CSV_PATH = Path(__file__).resolve().parents[2] / "data/weather_and_status/haboob_clean_2025-08-25/kphx_haboob_20250825_clean.csv"
LOW_VIS_SM = 1.0
TAXI_SPEED_MULT_LOW_VIS = 0.6      # assumption (docs/ASSUMPTIONS.md)
OCCUPANCY_MULT_LOW_VIS = 1.2       # assumption: runway occupancy +20%


def _f(v: str) -> Optional[float]:
    return float(v) if v not in ("", None) else None


def _minutes(utc: str) -> float:
    return datetime.strptime(utc, "%Y-%m-%d %H:%M").timestamp() / 60.0


class WeatherReplay:
    """Pre-trigger weather is the first (calm) report; after trigger_s the CSV plays at sec_per_min sim seconds per minute."""

    def __init__(self, start_utc: str = "2025-08-26 00:20", sec_per_min: float = 2.0, path: Path = CSV_PATH) -> None:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        self.calm = self._row(rows[0], {})
        carry: Dict[str, Any] = {}
        self.rows: List[Dict[str, Any]] = []
        for r in rows:
            parsed = self._row(r, carry)
            carry = {k: parsed[k] for k in ("wind_dir", "wind_kt")}
            parsed["_min"] = _minutes(r["utc"])
            self.rows.append(parsed)
        self.start_min = _minutes(start_utc)
        self.sec_per_min = sec_per_min
        self.rows = [r for r in self.rows if r["_min"] >= self.start_min]
        self.trigger_s: Optional[float] = None
        self._sent = 0
        self.t_first_low: Optional[float] = None
        self.t_recover: Optional[float] = None
        self.current: Dict[str, Any] = dict(self.calm, t=0.0, active=False, haboob_progress=0.0)

    @staticmethod
    def _row(r: Dict[str, str], carry: Dict[str, Any]) -> Dict[str, Any]:
        wd, wk = _f(r["wind_dir"]), _f(r["wind_kt"])
        return {"metar": r["metar"], "wind_dir": int(wd) if wd is not None else carry.get("wind_dir"),
                "wind_kt": int(wk) if wk is not None else carry.get("wind_kt"),
                "gust_kt": int(_f(r["gust_kt"])) if _f(r["gust_kt"]) is not None else None,
                "vis_sm": _f(r["vis_sm"]), "wx": r["wx"] or None, "temp_f": _f(r["temp_f"])}

    def due_time(self, row: Dict[str, Any]) -> float:
        assert self.trigger_s is not None
        return self.trigger_s + (row["_min"] - self.start_min) * self.sec_per_min

    def trigger(self, t: float) -> bool:
        if self.trigger_s is not None:
            return False
        self.trigger_s = t
        lows = [r for r in self.rows if (r["vis_sm"] or 10) < LOW_VIS_SM]
        if lows:
            self.t_first_low = self.due_time(lows[0])
            after = [r for r in self.rows if r["_min"] > lows[-1]["_min"] and (r["vis_sm"] or 10) >= 2.0]
            self.t_recover = self.due_time(after[0]) if after else self.due_time(self.rows[-1])
        return True

    def progress(self, t: float) -> float:
        """0 before the trigger, 0.5 when visibility first drops below 1 SM, 1.0 when the dust has passed."""
        if self.trigger_s is None or self.t_first_low is None or self.t_recover is None:
            return 0.0
        if t <= self.trigger_s:
            return 0.0
        if t <= self.t_first_low:
            return 0.5 * (t - self.trigger_s) / max(self.t_first_low - self.trigger_s, 1e-6)
        if t <= self.t_recover:
            return 0.5 + 0.5 * (t - self.t_first_low) / max(self.t_recover - self.t_first_low, 1e-6)
        return 1.0

    def step(self, t: float) -> List[Dict[str, Any]]:
        """Reports that became due at sim time t; updates self.current."""
        fresh: List[Dict[str, Any]] = []
        if self.trigger_s is not None:
            while self._sent < len(self.rows) and self.due_time(self.rows[self._sent]) <= t:
                row = {k: v for k, v in self.rows[self._sent].items() if k != "_min"}
                self.current = dict(row, t=round(t, 1), active=True)
                fresh.append(self.current)
                self._sent += 1
        if self.trigger_s is not None:
            self.current["haboob_progress"] = round(self.progress(t), 3)
            self.current["active"] = self.current["haboob_progress"] < 1.0
        return fresh

    @property
    def low_vis(self) -> bool:
        v = self.current.get("vis_sm")
        return v is not None and v < LOW_VIS_SM

    @property
    def taxi_mult(self) -> float:
        return TAXI_SPEED_MULT_LOW_VIS if self.low_vis else 1.0

    @property
    def occupancy_mult(self) -> float:
        return OCCUPANCY_MULT_LOW_VIS if self.low_vis else 1.0
