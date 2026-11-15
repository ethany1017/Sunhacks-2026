"""Our controller (the Agent). The simulator calls on_tick(sim, events) every tick once it is plugged in."""


class Controller:
    name = "agent"

    def __init__(self):
        self.status = "idle"

    def on_tick(self, sim, events):
        # A1: does nothing, so aircraft hold. Never read `sim` here: later phases act only through backend/simif.
        pass
