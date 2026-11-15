"""The Simulator Interface: the only way this project talks to the airport simulator."""
from typing import Protocol

from .stub import StubSimulator


class Simulator(Protocol):
    def get_airport(self) -> dict:
        """The airport layout: {"config": ..., "geojson": ..., "graph": ...}."""
        ...


def connect() -> Simulator:
    return StubSimulator()  # the single place where a different adapter will be chosen later
