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
    for _ in range(40):
        sim.step()
    retry = sim.observe()["pending_readbacks"]
    assert len(retry) == 1 and retry[0]["parsed"]["runway"] == "25R"       # the corrected readback, still awaiting a verdict
    sim.resolve_readback(retry[0]["id"], True)
    assert not sim.observe()["pending_readbacks"]
    assert sim.clearances[retry[0]["clearance_id"]].ack_t is not None          # acknowledged (then no effect: it is at the gate)
    assert sim.score()["readback_errors_caught"] == 1 and sim.score()["readback_errors_accepted"] == 0
    sim.close()




def test_mid_run_controller_switch_hands_back_to_the_tower_cleanly(tmp_path, graph) -> None:
    """Restored from the pre-split suite (it ran only through the Claude agent): tower -> other -> tower mid-run."""
    from tests.conftest import SMOKE, NoopController
    sim = Simulation(SMOKE, run_dir=tmp_path / "switch", graph=graph)
    assert sim.controller_name == "tower" and sim.set_controller("nonsense")["ok"] is False
    while sim.t < 100:
        sim.step()
    assert sim.set_controller(NoopController())["ok"] and sim.controller_name == "noop"
    while sim.t < 160:
        sim.step()
    assert sim.set_controller("tower")["ok"] and sim.controller_name == "tower"      # primed from the current state
    sim.run_to_completion()
    sim.close()
    assert sim.finished and sim.score()["completed"] == 6
    once = {"pushback_approved", "line_up_and_wait", "cleared_for_takeoff", "cleared_to_land", "taxi_via"}
    seen = {}
    for e in sim.log.events:
        if e["kind"] == "clearance.issued" and e["data"]["action"] in once:
            key = (e["entity_id"], e["data"]["action"])
            seen[key] = seen.get(key, 0) + 1
    go_arounds = [e["entity_id"] for e in sim.log.events if e["kind"] == "go_around"]   # a new approach needs a new landing clearance
    dup = [k for k, v in seen.items() if v > 1 + (go_arounds.count(k[0]) if k[1] == "cleared_to_land" else 0)]
    assert not dup, dup                                                           # no duplicate clearances after handover
    assert [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")] == []
