"""Fixed-tick sim clock. Sim time is ticks x TICK_S, never wall time, so a run is identical at any speed."""
import asyncio
import time

TICK_S = 1.0  # sim seconds per tick


class Clock:
    def __init__(self, speed):
        self.speed, self.ticks, self._t0 = speed, 0, time.monotonic()

    @property
    def t(self):
        return self.ticks * TICK_S

    async def tick(self):
        """Advance one tick; sleeps to keep pace at `speed` sim seconds per wall second (0 = no sleeping)."""
        self.ticks += 1
        if self.speed > 0:  # sleep to a deadline so slow DB writes do not make the sim drift
            await asyncio.sleep(max(0.0, self._t0 + self.t / self.speed - time.monotonic()))
