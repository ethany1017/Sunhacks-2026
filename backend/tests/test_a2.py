import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import simif  # noqa: E402
from app import db, evaluate, runner as runner_mod  # noqa: E402
from app.runner import Runner  # noqa: E402

SIM = simif.connect()


@pytest.fixture(autouse=True)
def events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setattr(evaluate, "_SIM", SIM)


def test_level_table_has_the_real_levels_first_with_their_seeds():
    levels = SIM.get_levels()
    assert [(lv["id"], lv["name"]) for lv in levels[:2]] == [("normal_day", "Normal Day"), ("light_rain", "Light Rain")]
    practice = {lv["id"] for lv in levels if lv.get("practice")}
    assert {"busy_arrivals", "departure_push", "runway_crossing", "wrong_readback", "runway_config_change", "rain_peak",
            "smoke", "busy"} == practice
    assert all(lv["description"] for lv in levels if lv.get("practice"))
    assert SIM.get_seeds("normal_day") == {"training": [1, 2, 3, 4, 5], "heldout": [9001, 9002, 9003]}
    assert SIM.get_seeds("light_rain") == {"training": [21, 22, 23, 24, 25], "heldout": [9021, 9022, 9023]}
    assert SIM.get_seeds("rain_peak") == {"training": [21], "heldout": []}
    assert SIM.get_seeds("wrong_readback") == {"training": [104], "heldout": []}


def test_harness_runs_a_practice_scenario_for_both_controllers():
    res = [evaluate.run_one((c, "wrong_readback", None, "practice")) for c in ("human", "agent")]
    for r in res:
        assert r["verdict"] in ("PASS", "FAIL") and r["seed"] == 104 and r["section"] == "practice"
        assert {"fails", "fails_by_rule", "completed", "avg_delay_s", "total_delay_s", "go_arounds", "diversions",
                "readback_errors_caught", "readback_errors_accepted"} <= set(r["score"])
        assert r["score"]["fails"] == len(r["fails"])
    human, agent = res
    assert human["human_impl"] in ("human", "tower") and agent["human_impl"] is None
    assert human["score"]["readback_errors_caught"] > 0  # the scenario really produces wrong readbacks
    assert agent["verdict"] == "PASS" and agent["score"]["readback_errors_caught"] > 0
    assert agent["score"]["readback_errors_accepted"] == 0


def test_heldout_seeds_need_the_explicit_option():
    training = evaluate.plan(SIM, ["human", "agent"], ["levels"])
    assert {s for _, _, s, _ in training} == {1, 2, 3, 4, 5, 21, 22, 23, 24, 25}
    assert {sec for *_, sec in training} == {"training"}
    for levels, seeds in ((["normal_day"], "9001"), (["normal_day"], "1,9002"), (["rain_peak"], "9021"), (["smoke"], "9003")):
        with pytest.raises(ValueError, match="held-out"):
            evaluate.plan(SIM, ["human"], levels, seeds)
    with pytest.raises(SystemExit):
        evaluate.main(["--levels", "normal_day", "--seeds", "9001"])
    held = evaluate.plan(SIM, ["human"], ["all"], "heldout")
    assert sorted(s for _, _, s, _ in held) == [9001, 9002, 9003, 9021, 9022, 9023]
    assert {sec for *_, sec in held} == {"heldout"}
    assert {lv for _, lv, _, _ in evaluate.plan(SIM, ["human"], ["practice"])} >= {"busy_arrivals", "rain_peak"}


def _fake(controller, level, seed, section, verdict="FAIL"):
    rules = {"stuck": 0 if verdict == "PASS" else 2}
    score = {"fails": rules["stuck"], "fails_by_rule": rules, "completed": 3, "avg_delay_s": 10.0, "total_delay_s": 30.0,
             "go_arounds": 1, "diversions": 0, "readback_errors_caught": 1, "readback_errors_accepted": 0}
    fails = [{"rule": "stuck", "callsigns": ["X1"], "reason": "X1 stuck", "t": 5.0}] * rules["stuck"]
    return {"controller": controller, "human_impl": "tower" if controller == "human" else None, "level": level,
            "level_name": level, "seed": seed, "section": section, "verdict": verdict, "fails": fails, "score": score,
            "sim_s": 100.0, "wall_s": 1.0}


def test_report_separates_training_and_heldout():
    results = [_fake("human", "normal_day", 1, "training", "PASS"), _fake("agent", "normal_day", 1, "training"),
               _fake("human", "normal_day", 9001, "heldout"), _fake("agent", "normal_day", 9001, "heldout")]
    text = evaluate.report(results)
    tr, ho, pr = (text.index(h) for h in ("== TRAINING SEEDS ==", "== HELD-OUT SEEDS", "== PRACTICE SCENARIOS =="))
    assert tr < ho < pr and "(not run)" in text[pr:]
    training, heldout = text[tr:ho], text[ho:pr]
    assert " 9001 " not in training and " 9001 " in heldout and "normal_day                 1  human PASS" in training
    assert "human  pass 1/2" not in text and "human  pass 1/1 (100%)" in training and "agent  pass 0/1" in heldout
    s = evaluate.summary(results)
    assert set(s["sections"]) == {"training", "heldout"} and s["sections"]["training"]["human"]["passed"] == 1
    assert s["sections"]["heldout"]["agent"]["fails_by_rule"] == {"stuck": 2}


def test_publish_and_run_start_carry_the_comparison(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    monkeypatch.setattr(evaluate, "SUMMARY", path)
    monkeypatch.setattr(runner_mod, "SUMMARY", path)
    r = Runner(SIM)
    r.start("smoke", "agent")
    evaluate.publish(evaluate.summary([_fake("human", "normal_day", 1, "training")]))
    evaluate.publish(evaluate.summary([_fake("human", "normal_day", 9001, "heldout")]))  # merges, keeps training
    assert set(json.loads(path.read_text())["sections"]) == {"training", "heldout"}
    pushed = [e for e in db.events_for_run(r.run_id) if e["type"] == "metrics" and "comparison" in e["data"]]
    assert len(pushed) == 2
    r.start("smoke", "human")  # a new run starts with the saved comparison, the weather and the runway config
    ev = db.events_for_run(r.run_id)
    types = [e["type"] for e in ev]
    assert "comparison" in next(e for e in ev if e["type"] == "metrics")["data"]
    assert {"weather", "runway_change"} <= set(types)


class _FlowChanger:
    name, status = "agent", "idle"

    def on_tick(self, sim, events):
        if SIM.t < 0.15:
            assert SIM.set_runway_config(["07R", "08"], ["07L", "08"])["ok"]


def test_runway_config_change_is_translated():
    SIM.reset("smoke")
    assert SIM.get_runway_config()["arrival_runways"] == ["25L", "26"]
    SIM.set_controller("agent", _FlowChanger())
    ev = SIM.step()
    rc = next(e for e in ev if e["type"] == "runway_change")
    assert rc["data"]["arrival_runways"] == ["07R", "08"] and rc["data"]["runway"] == "ARR 07R 08 / DEP 07L 08"
