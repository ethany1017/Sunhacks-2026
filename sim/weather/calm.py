"""Static weather: one calm KPHX report for the whole run of a scenario file. Levels bring their own (sim/levels.py)."""
from __future__ import annotations

from typing import Any, Dict

CALM_REPORT: Dict[str, Any] = {"metar": "KPHX 151800Z 27005KT 10SM CLR 24/02 A3002", "wind_dir": 270, "wind_kt": 5,
                               "gust_kt": None, "vis_sm": 10.0, "wx": None, "temp_f": 75.0}


class CalmWeather:
    """Same report every tick (scenario files). Levels use sim.levels.LevelWeather, which has the same attributes.
    Physics reads the multipliers (1.0 = no effect on taxi speed, runway occupancy or approach speed)."""
    taxi_mult = 1.0
    occupancy_mult = 1.0
    approach_mult = 1.0
    go_around_rate = 0.0
    spacing_margin_nm = 1.0     # approach control's in-trail buffer above the radar / wake minimum (= levels.SPAWN_MARGIN_NM)

    def __init__(self) -> None:
        self.current: Dict[str, Any] = dict(CALM_REPORT, t=0.0)

    def tick(self, t: float) -> bool:
        return False
