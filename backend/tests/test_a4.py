"""A4 wiring: the agent controller's `choose` hook in front of reasoning.Advisor (fake clients, no network)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atc import reasoning  # noqa: E402
from atc.controller import Controller  # noqa: E402
from test_a3 import FakeSI, ac, bare_planner, pic  # noqa: E402
from test_reasoning import FakeClient  # noqa: E402

FLOW = {"kind": "runway_flow", "callsign": None, "default": "switch",
        "options": [{"id": "switch", "label": "turn the airport round"}, {"id": "keep", "label": "keep west flow"}]}
OBS = {"t": 100.0, "runway_config": {"arrival_runways": ["26"], "departure_runways": ["25R"]},
       "weather": {"metar": "KPHX 091014KT 4SM -RA BR"}, "aircraft": []}


def agent(client):
    adv = reasoning.Advisor(client)
    if client is None:
        adv.client = None   # no key, whatever the environment says
    c = Controller(FakeSI(), advisor=adv)
    c.events = []
    c.emit = lambda t, typ, cs, data, reason: c.events.append((typ, cs, data, reason))
    c.planner = type("P", (), {"t": 100.0, "obs": OBS})()
    return c


def test_no_key_returns_the_default_at_once_with_one_fallback_event():
    c = agent(None)
    assert c.choose(FLOW) == "switch"
    assert c.choose(FLOW) == "switch" and c.choose(FLOW) == "switch"   # re-checks: same answer, no new event
    assert len(c.events) == 1
    typ, cs, data, reason = c.events[0]
    assert typ == "ai_decision" and cs is None and reason == data["explanation"]
    assert data == {"kind": "runway_flow", "options": FLOW["options"], "default": "switch", "choice": "switch",
                    "explanation": "Planner default: turn the airport round (no Claude API key).", "source": "fallback"}


def test_single_option_is_not_a_decision():
    c = agent(None)
    d = {"kind": "arrival_runway", "callsign": "A1", "default": "26", "options": [{"id": "26", "label": "26"}]}
    assert c.choose(d) == "26" and c.events == []


def test_each_arrival_is_its_own_decision():
    c = agent(None)
    opts = [{"id": "26", "label": "26"}, {"id": "25L", "label": "25L"}]
    for cs in ("A1", "A2"):
        assert c.choose({"kind": "arrival_runway", "callsign": cs, "default": "26", "options": opts}) == "26"
    assert [e[1] for e in c.events] == ["A1", "A2"]


def test_slow_claude_waits_then_answers_without_blocking():
    async def main():
        c = agent(FakeClient('{"choice": "keep", "summary": "Keep west flow until the bank lands."}', delay=0.05))
        assert c.choose(FLOW) is None and c.pending()   # fired, not waited on: the planner does nothing this pass
        assert c.choose(FLOW) is None and c.events == []
        await asyncio.sleep(0.1)
        assert c.choose(FLOW) == "keep" and not c.pending()
        assert c.choose(FLOW) == "keep" and len(c.events) == 1 and len(c.advisor.client.calls) == 1
        data = c.events[0][2]
        assert data["source"] == "claude" and data["choice"] == "keep" and data["default"] == "switch"
        assert "METAR: KPHX 091014KT" in c.advisor.client.calls[0]["messages"][0]["content"]
    asyncio.run(main())


def test_timeout_gives_the_default_as_fallback(monkeypatch):
    monkeypatch.setattr(reasoning, "TIMEOUT_S", 0.1)

    async def main():
        c = agent(FakeClient('{"choice": "keep", "summary": "x"}', delay=5))
        assert c.choose(FLOW) is None
        await asyncio.sleep(0.3)
        assert c.choose(FLOW) == "switch"
        data = c.events[0][2]
        assert data["source"] == "fallback" and "timed out" in data["explanation"]
    asyncio.run(main())


def test_an_option_outside_the_list_is_never_used():
    async def main():
        c = agent(FakeClient('{"choice": "land_on_taxiway_c", "summary": "x"}'))
        assert c.choose(FLOW) is None
        await asyncio.sleep(0.01)
        assert c.choose(FLOW) == "switch" and c.events[0][2]["source"] == "fallback"
    asyncio.run(main())


def test_claudes_departure_still_goes_through_the_safety_engine():
    """Claude picks B over the default A; B's line-up is then proposed and safety-checked like any proposal."""
    async def main():
        si = FakeSI()
        pl = bare_planner(si, pic())
        pl.g = type("G", (), {"end_runway": {"09": "P"}, "node_xy": {"e": (0.0, 0.0)}})()
        pl.last_airborne, pl.watch, pl.assigned = {}, {}, {}
        a = ac("A", phase="hold_short", hold={"runway": "P"}, stopped_s=50.0, entry_node="e", type="B738")
        b = ac("B", phase="hold_short", hold={"runway": "P"}, stopped_s=10.0, entry_node="e", type="B738")
        pl.ac = pl.pic["aircraft"] = {"A": a, "B": b}
        for cs in ("A", "B"):
            pl.assigned[cs] = {"end": "09", "taxi": True}
        pl.obs = dict(OBS, aircraft=[a, b])
        obs = {"t": 1000.0, "aircraft": [a, b], "runways": [{"id": "P", "occupancy": []}]}
        c = Controller(si, pl.emit, reasoning.Advisor(FakeClient('{"choice": "B", "summary": "B first."}')))
        c.planner, pl.choose = pl, c.choose
        for x in (a, b):
            pl._line_up(obs, x, pl.assigned[x["callsign"]])
        assert si.issued == [] and pl.events == []        # Claude still answering: nobody lines up
        await asyncio.sleep(0.01)
        for x in (a, b):
            pl._line_up(obs, x, pl.assigned[x["callsign"]])
        assert [x["callsign"] for x in si.issued] == ["B"]
        assert [(e[0], e[1]) for e in pl.events][:3] == [("ai_decision", "B"), ("proposal", "B"), ("safety_check", "B")]
        assert pl.events[0][2]["kind"] == "departure_gap" and pl.events[0][2]["source"] == "claude"
        assert pl.events[2][2]["approved"] is True
    asyncio.run(main())
