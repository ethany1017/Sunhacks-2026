"""Fixed-tick simulation clock."""
from __future__ import annotations

TICK_S = 0.1
SPEEDS = (1, 2, 5, 10, 20)


class SimClock:
    def __init__(self) -> None:
        self.tick = 0
        self.speed = 1
        self.paused = False

    @property
    def t(self) -> float:
        return round(self.tick * TICK_S, 6)

    @property
    def t_ms(self) -> int:
        return self.tick * 100

    def advance(self) -> None:
        self.tick += 1

    def set_speed(self, speed: int) -> None:
        if speed not in SPEEDS:
            raise ValueError("speed must be one of %s" % (SPEEDS,))
        self.speed = speed
