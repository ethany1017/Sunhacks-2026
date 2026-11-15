"""Development stub simulator: serves its own airport files and runs scripted scenarios (see loop.py)."""
from . import airport


class StubSimulator:
    def get_airport(self):
        return airport.load()
