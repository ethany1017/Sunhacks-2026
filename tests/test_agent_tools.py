from __future__ import annotations

from sim.core.engine import Simulation


def departure(sim: Simulation) -> str:
    return next(a.callsign for a in sim.aircraft.values() if a.kind == "departure")


def test_unknown_callsign_is_rejected_as_a_shape_error(manual_sim: Simulation) -> None:
    res = manual_sim.issue_clearance("NOPE999", "hold_position")
    assert res["ok"] is False and "unknown callsign" in res["error"]


def test_unknown_action_and_runway_are_shape_errors(manual_sim: Simulation) -> None:
    cs = departure(manual_sim)
    assert manual_sim.issue_clearance(cs, "do_a_barrel_roll")["ok"] is False
    assert manual_sim.issue_clearance(cs, "cleared_for_takeoff", {"runway": "99X"})["ok"] is False
    assert manual_sim.set_runway_config(["26"], ["31"])["ok"] is False


def test_route_must_use_existing_graph_nodes(manual_sim: Simulation) -> None:
    cs = departure(manual_sim)
    assert manual_sim.issue_clearance(cs, "taxi_via", {"route": ["not-a-node", "also-not"]})["ok"] is False
    assert manual_sim.issue_clearance(cs, "taxi_via", {"to": "ZZ99"})["ok"] is False
    assert manual_sim.issue_clearance(cs, "taxi_via", {"to": "25R", "via": ["QQQ"]})["ok"] is False


def test_unsafe_clearances_are_accepted_and_spoken(manual_sim: Simulation) -> None:
    """The simulator validates shape only: it never refuses a clearance for being unsafe."""
    sim = manual_sim
    cs = departure(sim)
    sim.res.enter("07L/25R", "SOMEONE", sim.t)                       # runway occupied
    for action, params in (("cleared_for_takeoff", {"runway": "25R"}), ("line_up_and_wait", {"runway": "25R"}),
                           ("cross_runway", {"runway": "25R"}), ("cleared_to_land", {"runway": "25R"})):
        res = sim.issue_clearance(cs, action, params)
        assert res["ok"] is True and res["text"]
    assert any(v.speaker == "agent" for v in sim.voice)


def test_valid_tool_calls_return_ids_and_log_events(manual_sim: Simulation) -> None:
    sim = manual_sim
    cs = departure(sim)
    res = sim.issue_clearance(cs, "pushback_approved")
    assert res["ok"] and res["clearance_id"].startswith("c-") and "pushback approved" in res["text"]
    assert sim.speak(cs, "Phoenix ground, standby.")["ok"]
    assert sim.set_runway_config(["26"], ["25R"])["ok"]
    dec = sim.record_decision(callsign=cs, action="pushback_approved", summary="test", facts=["f"], rules=["r"],
                              alternatives=[{"option": "o", "reason": "why"}], next_review_s=10)
    assert dec["ok"] and sim.decisions[dec["decision_id"]]["alternatives"][0]["option"] == "o"
    kinds = {e["kind"] for e in sim.log.events}
    assert {"clearance.issued", "speech", "runway.config", "decision"} <= kinds


def test_observation_is_delayed_and_noisy_but_truth_is_unchanged(manual_sim: Simulation) -> None:
    sim = manual_sim
    for _ in range(40):
        sim.step()
    obs = sim.observe()
    assert abs(obs["data_age_s"] - sim.scenario.observation_delay_s) < 0.11
    ac = sim.aircraft[obs["aircraft"][0]["callsign"]]
    lon, lat = sim.graph.lonlat((ac.x, ac.y))
    seen = obs["aircraft"][0]
    assert (seen["lon"], seen["lat"]) != (round(lon, 7), round(lat, 7))     # noise applied to the view only


def test_readback_with_wrong_runway_side_must_be_noticed(tmp_path, graph) -> None:
    from tests.conftest import SMOKE, NoopController
    sim = Simulation(SMOKE, run_dir=tmp_path / "rb", controller=NoopController(), graph=graph)
    sim.scenario.readback_error_rate = 1.0
    for _ in range(3):
        sim.step()
    cs = departure(sim)
    sim.issue_clearance(cs, "hold_short", {"runway": "25R"})
    for _ in range(60):
        sim.step()
    pending = sim.observe()["pending_readbacks"]
    assert pending and pending[0]["parsed"]["runway"] in ("25L", None)
    sim.resolve_readback(pending[0]["id"], False)                          # say again
    for _ in range(60):
        sim.step()
    assert not sim.observe()["pending_readbacks"]
    sim.close()


# -- Claude agent tool surface (sim/agent/tools.py) ---------------------------------------------------------------------------
import json  # noqa: E402

from sim.agent.tools import ACTION_TOOLS, TOOL_DEFS, TOOL_NAMES, ToolBox, dispatch, wake_spacing  # noqa: E402
from sim.core.engine import ACTIONS  # noqa: E402


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
