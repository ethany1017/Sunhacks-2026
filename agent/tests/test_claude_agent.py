"""ClaudeAgent against a FAKE Anthropic client (tests/fakes.py). No network, no key, no real model."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, List

import pytest

from agent.claude_agent import ClaudeAgent, ClaudeConfig, _Call
from sim.core.engine import Simulation
from tests.conftest import BUSY, SMOKE
from agent.tests.fakes import FakeAnthropic, ScriptedPolicy

SECRET = "fake-test-key-must-never-appear-in-logs"


def make_sim(tmp_path: Path, graph: Any, agent: Any, scenario: Path = SMOKE, name: str = "run") -> Simulation:
    return Simulation(scenario, run_dir=tmp_path / name, controller=agent, graph=graph)


def issued(sim: Simulation) -> List[Any]:
    return [e for e in sim.log.events if e["kind"] == "clearance.issued"]


def bad_events(sim: Simulation) -> List[Any]:
    return [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")]


def run_threaded(sim: Simulation, agent: ClaudeAgent, until_t: float = 1e9, wall_s: float = 90.0, tick_sleep: float = 0.001) -> None:
    """Step the sim the way the API runner does: service the agent every loop, never wait for it."""
    t0 = time.time()
    while not sim.finished and sim.t < until_t and time.time() - t0 < wall_s:
        agent.service(sim)
        if not sim.clock.paused:
            sim.step()
        time.sleep(tick_sleep)


def test_smoke_scenario_completes_through_claude_agent_with_fake_client(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic()
    agent = ClaudeAgent(client=client, config=ClaudeConfig(sync=True))
    sim = make_sim(tmp_path, graph, agent)
    sim.run_to_completion()
    score = sim.score()
    assert sim.finished and score["completed"] == 6
    assert score["separation_losses"] == 0 and score["runway_conflicts"] == 0 and score["deadlocks"] == 0
    assert agent.stats["api_errors"] == 0 and agent.stats["fallback_ticks"] == 0     # the baseline never ran
    # every action is followed by a decision record (the policy sends action + record pairs)
    acts = len(issued(sim))
    assert acts >= 12 and len(sim.decisions) >= acts
    assert all(d["facts"] and d["rules"] and d["next_review_s"] for d in sim.decisions.values())
    # prompt caching on the system prompt and the full tool list on every call
    first = client.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"} and "KPHX" in first["system"][0]["text"]
    assert {t["name"] for t in first["tools"]} >= {"issue_clearance", "record_decision"}
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "low"}
    sim.close()


def test_busy_with_readback_errors_through_claude_agent(tmp_path: Path, graph: Any) -> None:
    agent = ClaudeAgent(client=FakeAnthropic(), config=ClaudeConfig(sync=True))
    sim = make_sim(tmp_path, graph, agent, BUSY)
    sim.run_to_completion()
    score = sim.score()
    assert sim.finished and score["completed"] == 12 and score["readback_errors_accepted"] == 0
    assert any(e["kind"] == "readback.rejected" for e in sim.log.events)            # wrong readbacks were caught
    sim.close()


def test_slow_llm_call_does_not_block_the_sim_loop(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic(latency_s=0.4)
    agent = ClaudeAgent(client=client, config=ClaudeConfig())
    sim = make_sim(tmp_path, graph, agent)
    ticks_while_in_flight, thinking_seen, gaps = 0, False, []
    t0, last = time.time(), time.time()
    while sim.t < 40 and time.time() - t0 < 60:
        agent.service(sim)
        sim.step()
        now = time.time()
        gaps.append(now - last)
        last = now
        if client.in_flight:
            ticks_while_in_flight += 1
            thinking_seen = thinking_seen or agent.status == "thinking"
        time.sleep(0.001)
    assert thinking_seen and ticks_while_in_flight > 100        # the sim kept stepping through slow calls
    assert max(gaps) < 0.2                                      # no step waited for a 0.4 s model call
    assert agent.stats["wakes"] >= 2 and len(client.calls) >= 2
    t1 = time.time()
    while agent._busy and time.time() - t1 < 10:      # the worker needs the sim thread to run its queued tool calls
        agent.service(sim)
        time.sleep(0.01)
    assert agent.status == "idle"
    agent.shutdown()
    sim.close()


def test_threaded_smoke_completes_with_slow_fake(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic(latency_s=0.05)
    agent = ClaudeAgent(client=client)
    sim = make_sim(tmp_path, graph, agent)
    run_threaded(sim, agent)
    agent.shutdown()
    agent.join(5)
    assert sim.finished and sim.score()["completed"] == 6
    assert agent.stats["api_errors"] == 0 and not any(t.name == "claude-wake" and t.is_alive() for t in threading.enumerate())
    sim.close()


def test_api_errors_fall_back_to_baseline_and_never_leak_the_key(tmp_path: Path, graph: Any, monkeypatch: Any, caplog: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    client = FakeAnthropic(fail=RuntimeError("529 overloaded for key %s" % SECRET))
    agent = ClaudeAgent(client=client, config=ClaudeConfig(sync=True, backoff_base_s=10_000.0))
    sim = make_sim(tmp_path, graph, agent)
    with caplog.at_level(logging.INFO, logger="kphx.claude_agent"):
        sim.run_to_completion()
    assert sim.finished and sim.score()["completed"] == 6          # baseline covered every wake
    assert agent.stats["api_errors"] == 1 and agent.stats["fallback_ticks"] > 100
    assert agent.status == "fallback" and agent.last_error and SECRET not in agent.last_error
    assert SECRET not in caplog.text and "baseline covers" in caplog.text
    assert len(client.calls) == 1                                  # in backoff: no further calls
    sim.close()


def test_agent_retries_after_backoff_and_resumes(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic(fail=lambda n: RuntimeError("rate limited") if n <= 2 else None)
    agent = ClaudeAgent(client=client, config=ClaudeConfig(sync=True, backoff_base_s=0.0))
    sim = make_sim(tmp_path, graph, agent)
    sim.run_to_completion()
    assert agent.stats["api_errors"] == 2 and sim.finished and sim.score()["completed"] == 6
    assert len(sim.decisions) > 5 and agent.stats["tool_calls"] > 20      # Claude ran after the errors cleared
    sim.close()


def test_tool_call_cap_and_followup_wake(tmp_path: Path, graph: Any) -> None:
    calls = [("get_weather", {})] * 20
    client = FakeAnthropic(script=lambda ctx: calls)
    agent = ClaudeAgent(client=client, config=ClaudeConfig(sync=True, max_tool_calls=8))
    sim = make_sim(tmp_path, graph, agent)
    for _ in range(30):
        sim.step()
    assert agent.stats["budget_exhausted"] >= 2                           # woken again straight away
    assert agent.stats["tool_calls"] == 8 * agent.stats["wakes"] - 8 * (agent.stats["wakes"] - agent.stats["budget_exhausted"])
    first_results = client.calls[1]["messages"][-1]["content"]
    assert sum('"ok":false' in r["content"] for r in first_results) == 12  # the 12 over-budget calls were refused
    sim.close()


def test_wake_wall_time_cap(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic(latency_s=0.15, script=lambda ctx: [("get_weather", {})], keep_going=True)
    agent = ClaudeAgent(client=client, config=ClaudeConfig(wake_wall_s=0.6, max_tool_calls=50))
    sim = make_sim(tmp_path, graph, agent)
    for _ in range(3):
        sim.step()
    t0 = time.time()
    while agent.status == "thinking" and time.time() - t0 < 5:
        agent.service(sim)
        time.sleep(0.01)
    assert time.time() - t0 < 2.0
    assert 2 <= len(client.calls) <= 5 and agent.stats["api_errors"] == 0   # capped by wall time, not an error
    agent.shutdown()
    sim.close()


def test_pause_while_thinking_pauses_and_resumes_the_clock(tmp_path: Path, graph: Any) -> None:
    client = FakeAnthropic(latency_s=0.3)
    agent = ClaudeAgent(client=client, config=ClaudeConfig(pause_while_thinking=True))
    sim = make_sim(tmp_path, graph, agent)
    sim.step()
    assert sim.clock.paused and agent.status == "thinking"
    t = sim.t
    time.sleep(0.1)
    agent.service(sim)
    assert sim.t == t and sim.clock.paused
    t0 = time.time()
    while sim.clock.paused and time.time() - t0 < 5:
        agent.service(sim)
        time.sleep(0.01)
    assert not sim.clock.paused and agent.status == "idle"
    agent.shutdown()
    sim.close()


def test_default_does_not_pause(tmp_path: Path, graph: Any) -> None:
    assert ClaudeConfig().pause_while_thinking is False
    agent = ClaudeAgent(client=FakeAnthropic(latency_s=0.2))
    sim = make_sim(tmp_path, graph, agent)
    sim.step()
    assert not sim.clock.paused
    agent.shutdown()
    assert agent.join(5)                              # shutdown fails queued tool calls so the worker ends
    sim.close()


def test_tool_calls_from_before_a_reset_are_discarded(tmp_path: Path, graph: Any) -> None:
    agent = ClaudeAgent(client=FakeAnthropic(), config=ClaudeConfig(sync=True))
    sim = make_sim(tmp_path, graph, agent)
    sim.step()
    old_epoch = sim.log
    sim.control("reset")
    agent.service(sim)
    res = agent._run_tool(sim, _Call(old_epoch, "get_weather", {}))
    assert res["ok"] is False and "reset" in res["error"]
    sim.close()


def test_switching_controllers_mid_run_is_safe(tmp_path: Path, graph: Any, monkeypatch: Any) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "switch", graph=graph)
    assert sim.controller_name == "tower"
    while sim.t < 100:
        sim.step()
    agent = ClaudeAgent(client=FakeAnthropic(latency_s=0.01))
    assert sim.set_controller(agent)["ok"] and sim.controller_name == "claude"
    run_threaded(sim, agent, until_t=330)
    assert sim.set_controller("tower")["ok"] and sim.controller_name == "tower"
    agent.join(5)
    assert agent.status == "idle"
    sim.run_to_completion()
    assert sim.finished and sim.score()["completed"] == 6
    once = {"pushback_approved", "line_up_and_wait", "cleared_for_takeoff", "cleared_to_land", "taxi_via"}
    seen = {}
    for e in issued(sim):
        key = (e["entity_id"], e["data"]["action"])
        if e["data"]["action"] in once:
            seen[key] = seen.get(key, 0) + 1
    assert max(seen.values()) == 1, [k for k, v in seen.items() if v > 1]          # no duplicate clearances across handovers
    assert sim.score()["separation_losses"] == 0 and sim.score()["runway_conflicts"] == 0
    sim.close()
