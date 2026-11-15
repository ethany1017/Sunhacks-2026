from __future__ import annotations

import json

from sim.core.engine import ACTIONS, Simulation
from agent.tools import ACTION_TOOLS, TOOL_DEFS, TOOL_NAMES, ToolBox, dispatch, wake_spacing


def departure(sim: Simulation) -> str:
    return next(a.callsign for a in sim.aircraft.values() if a.kind == "departure")


def test_tool_schemas_are_valid_json_schema_objects() -> None:
    assert len(set(TOOL_NAMES)) == len(TOOL_NAMES)
    for t in TOOL_DEFS:
        assert t["name"] and len(t["description"]) > 20
        schema = t["input_schema"]
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert set(schema["required"]) <= set(schema["properties"])
        json.dumps(t)                                     # serialisable for the API
    expected = {"get_airport_state", "get_aircraft", "get_route_options", "get_wake_spacing", "get_weather", "get_runway_status",
                "issue_clearance", "speak", "set_runway_config", "record_decision", "resolve_readback"}
    assert expected == set(TOOL_NAMES)
    assert set(ACTION_TOOLS) <= set(TOOL_NAMES)
    action_enum = next(t for t in TOOL_DEFS if t["name"] == "issue_clearance")["input_schema"]["properties"]["action"]["enum"]
    assert tuple(action_enum) == ACTIONS


def test_toolbox_rejects_malformed_calls_without_touching_the_sim(manual_sim: Simulation) -> None:
    box = ToolBox(manual_sim)
    n = len(manual_sim.log.events)
    assert box.call("nope", {})["ok"] is False
    assert "missing" in box.call("issue_clearance", {"callsign": "X"})["error"]
    assert "unexpected" in box.call("get_weather", {"foo": 1})["error"]
    assert box.call("issue_clearance", "not-an-object")["ok"] is False
    cs = departure(manual_sim)
    assert box.call("issue_clearance", {"callsign": cs, "action": "pushback_approved", "params": "x"})["ok"] is False
    assert box.call("issue_clearance", {"callsign": "NOPE1", "action": "hold_position"})["ok"] is False
    assert box.call("speak", {"callsign": cs, "text": "  "})["ok"] is False
    assert box.call("set_runway_config", {"arrival_runways": ["26"], "departure_runways": ["99"]})["ok"] is False
    assert box.call("record_decision", {"callsign": "NOPE1", "action": "a", "summary": "s", "facts": [], "rules": [],
                                        "alternatives": [], "next_review_s": 5})["ok"] is False
    assert box.call("record_decision", {"callsign": cs, "action": "a", "summary": "s", "facts": [], "rules": [],
                                        "alternatives": [{"option": "x"}], "next_review_s": 5})["ok"] is False
    assert box.call("resolve_readback", {"readback_id": "rb-999", "accept": True})["ok"] is False
    assert len(manual_sim.log.events) == n                 # nothing above changed the simulation


def test_action_tools_mutate_the_sim_and_link_the_voice_line(manual_sim: Simulation) -> None:
    sim, box = manual_sim, ToolBox(manual_sim)
    cs = departure(sim)
    res = box.call("issue_clearance", {"callsign": cs, "action": "pushback_approved"})
    assert res["ok"] and res["clearance_id"] in sim.clearances and "pushback approved" in res["text"]
    assert box.call("speak", {"callsign": cs, "text": "Standby for taxi."})["ok"]
    assert box.call("set_runway_config", {"arrival_runways": ["26"], "departure_runways": ["25R"]})["ok"]
    assert sim.runway_cfg == {"arrival_runways": ["26"], "departure_runways": ["25R"]}
    dec = box.call("record_decision", {"callsign": cs, "action": "pushback_approved", "summary": "Pushback", "facts": ["ready"],
                                       "rules": ["JO 7110.65BB 3-7-1"], "alternatives": [{"option": "wait", "reason": "lane clear"}],
                                       "next_review_s": 15})
    d = sim.decisions[dec["decision_id"]]
    assert d["voice_id"] and d["alternatives"][0]["reason"] == "lane clear" and d["next_review_s"] == 15


def test_information_tools_are_compact_and_geometry_free(manual_sim: Simulation) -> None:
    sim, box = manual_sim, ToolBox(manual_sim)
    for _ in range(60):
        sim.step()
    cs = departure(sim)
    state = box.call("get_airport_state", {})
    text = json.dumps(state)
    assert len(text) < 5000 and '"lon"' not in text and '"route"' not in text
    assert {"t", "weather", "runway_config", "runways", "aircraft", "active_clearances", "pending_readbacks"} <= set(state)
    ac = box.call("get_aircraft", {"callsign": cs})
    assert ac["cs"] == cs and "cwt" in ac and "radio" in ac
    assert box.call("get_aircraft", {"callsign": "NOPE1"})["ok"] is False
    assert box.call("get_weather", {})["vis_sm"] == 10
    rs = box.call("get_runway_status", {})
    assert {r["id"] for r in rs["runways"]} == {"08/26", "07L/25R", "07R/25L"}


def test_route_options_give_letters_lengths_and_crossings(manual_sim: Simulation) -> None:
    box = ToolBox(manual_sim)
    cs = departure(manual_sim)
    res = box.call("get_route_options", {"callsign": cs, "destination": "25R"})
    assert res["ok"] and res["options"][0]["option"] == "shortest"
    first = res["options"][0]
    assert first["via"] and first["length_m"] > 100 and isinstance(first["runway_crossings"], list)
    gate = box.call("get_route_options", {"callsign": cs, "destination": manual_sim.aircraft[cs].gate})
    assert gate["ok"] or "route" in gate["error"]
    assert box.call("get_route_options", {"callsign": cs, "destination": "ZZ99"})["ok"] is False


def test_wake_spacing_from_the_aircraft_table() -> None:
    w = wake_spacing("B77W", "B738")           # cwt B behind: F follower
    assert w["leader"]["cwt"] == "B" and w["follower"]["cwt"] == "F"
    assert w["wake_distance_nm"] == 5 and w["departure_interval_s"] == 120 and w["srs_departure_ft"] == 6000
    assert wake_spacing("B738", "B738")["departure_interval_s"] == 0
    e_i = wake_spacing("B752", "C172")                  # category I behind E
    assert e_i["departure_interval_s"] == 120 and e_i["wake_distance_nm"] == 4
    assert wake_spacing("A332", "C172")["departure_interval_s"] == 120
    assert dispatch(Simulation.__new__(Simulation), "get_wake_spacing", {"leader_type": "XXXX", "follower_type": "B738"})["ok"] is False
