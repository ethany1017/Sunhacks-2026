"""The Simulator Interface: the only way this project talks to the airport simulator. Fields: README.md."""
import os
from typing import Protocol


class Simulator(Protocol):
    def get_airport(self) -> dict:
        """The airport layout: {"config": ..., "geojson": ..., "graph": ...}."""
        ...


def connect() -> Simulator:
    """The in-process KPHX simulator (simif/real.py); the old offline stub only with SIMIF=stub."""
    if os.environ.get("SIMIF") == "stub":
        from .stub import StubSimulator
        return StubSimulator()
    from .real import RealSimulator
    return RealSimulator()
