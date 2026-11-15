"""Static weather: one calm KPHX report for the whole run. Levels (S3) will supply their own weather."""
from __future__ import annotations

from typing import Any, Dict

CALM_REPORT: Dict[str, Any] = {"metar": "KPHX 151800Z 27005KT 10SM CLR 24/02 A3002", "wind_dir": 270, "wind_kt": 5,
                               "gust_kt": None, "vis_sm": 10.0, "wx": None, "temp_f": 75.0}


class CalmWeather:
    """Same report every tick. Physics reads the two multipliers (1.0 = no effect on taxi speed or runway occupancy)."""
    taxi_mult = 1.0
    occupancy_mult = 1.0

    def __init__(self) -> None:
        self.current: Dict[str, Any] = dict(CALM_REPORT, t=0.0)
