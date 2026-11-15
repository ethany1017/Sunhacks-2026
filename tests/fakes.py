"""A fake Anthropic client for tests: no network, no key. It plays a simple scripted controller.

The policy reads the JSON picture in the wake message and answers with tool_use blocks (clearance + record_decision pairs),
exactly the shape the real Messages API returns. It is NOT a model of Claude's behaviour; it only exercises the plumbing.
"""
from __future__ import annotations

import json
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

PAV = {"25R": "07L/25R", "07L": "07L/25R", "25L": "07R/25L", "07R": "07R/25L", "26": "08/26", "08": "08/26"}
END_OF = {"07L/25R": "25R", "07R/25L": "25L", "08/26": "26"}
FREQ = {"08/26": "118.7", "07L/25R": "120.9", "07R/25L": "120.9"}
WORD = {"zero": "0", "one": "1", "two": "2", "tree": "3", "fower": "4", "fife": "5", "six": "6", "seven": "7", "eight": "8", "niner": "9"}
SIDE = {"left": "L", "right": "R", "center": "C"}


def runway_in(text: Optional[str]) -> Optional[str]:
    """'..., runway two five right, ...' -> '25R' (None when the text names no runway)."""
    m = re.search(r"runway ((?:(?:%s) ?)+)(left|right|center)?" % "|".join(WORD), (text or "").lower())
    if not m:
        return None
    return "".join(WORD[w] for w in m.group(1).split()) + SIDE.get(m.group(2) or "", "")


def tool_use(i: int, name: str, args: Dict[str, Any]) -> Any:
    return SimpleNamespace(type="tool_use", id="toolu_%d_%s" % (i, name), name=name, input=args)


class ScriptedPolicy:
    """First-come, first-served controller written against the wake-message picture (a stand-in for the model)."""

    def __init__(self, max_pairs: int = 4, takeoff_gap_s: float = 70.0) -> None:
        self.done: set = set()
        self.claims: Dict[str, Tuple[str, float]] = {}
        self.last_takeoff: Dict[str, float] = {}
        self.max_pairs = max_pairs
        self.takeoff_gap_s = takeoff_gap_s

    # -- helpers ---------------------------------------------------------------------------------------------------
    def _free(self, ctx: Dict[str, Any], pav: str, me: str, t0: float, t1: float) -> bool:
        t = ctx["t"]
        run = next(r for r in ctx["runways"] if r["id"] == pav)
        for w in run.get("windows", []):
            if w["cs"] == me:
                continue
            if w["kind"] == "occupied" and w["to_s"] >= t - 0.2:
                return False
            if w["kind"] == "reserved" and w["from_s"] < t1 and w["to_s"] > t0:
                return False
        return not any(cs != me and p == pav and exp > t0 for cs, (p, exp) in self.claims.items())

    @staticmethod
    def _landing_due(ctx: Dict[str, Any], pav: str, within: float, me: str) -> bool:
        return any(a["kind"] == "arrival" and a["phase"] == "final" and a["cs"] != me and PAV.get(a.get("rwy", "")) == pav
                   and a.get("eta_s", 999) <= within for a in ctx["aircraft"])

    def plan(self, ctx: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """Return (tool, args, decision-record fields) triples, most urgent first."""
        t = ctx["t"]
        out: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        vis = ctx["weather"].get("vis_sm", 10)

        def act(name: str, args: Dict[str, Any], summary: str, facts: List[str], rules: List[str], alts: List[Dict[str, str]],
                review: float = 10.0, key: Optional[Tuple[str, ...]] = None) -> None:
            if key is not None:
                self.done.add(key)
            out.append((name, args, {"callsign": args.get("callsign"), "action": args.get("action", name), "summary": summary,
                                     "facts": facts, "rules": rules, "alternatives": alts, "next_review_s": review}))

        for rb in ctx["pending_readbacks"]:
            want = runway_in(rb.get("issued"))
            if want is not None and rb.get("heard_runway") != want:
                act("resolve_readback", {"readback_id": rb["id"], "accept": False},
                    "Readback from %s wrong: heard %s, issued %s; say again" % (rb["cs"], rb.get("heard_runway"), want),
                    ["heard: %s" % rb["heard"], "issued: %s" % rb.get("issued")], ["JO 7110.65BB 3-7-2 readback"],
                    [{"option": "accept", "reason": "wrong runway"}], 5)
            else:
                out.append(("resolve_readback", {"readback_id": rb["id"], "accept": True}, {}))
        if vis < 3 and ("lowvis",) not in self.done:
            act("set_runway_config", {"arrival_runways": ["25L", "26"], "departure_runways": ["25R"]},
                "Low visibility: departures only on 25R", ["vis %s SM" % vis], ["JO 7110.65BB 3-9-10"],
                [{"option": "keep both", "reason": "more crossings"}], 30, key=("lowvis",))
        dep_rwys = ctx["runway_config"]["departure_runways"]
        for a in sorted(ctx["aircraft"], key=lambda x: (x.get("eta_s", 9999), x["cs"])):
            cs, ph, kind = a["cs"], a["phase"], a["kind"]
            if kind == "arrival":
                end = a.get("rwy", "")
                pav = PAV.get(end)
                if ph == "final" and pav:
                    if a.get("freq") != "tower" and ("tower", cs) not in self.done:
                        act("issue_clearance", {"callsign": cs, "action": "contact_tower_or_ground",
                                                "params": {"facility": "tower", "frequency": FREQ[pav], "runway": end}},
                            "Assigned %s to runway %s" % (cs, end), ["%s %.0f s out" % (cs, a.get("eta_s", 0))], ["first come first served"],
                            [], 10, key=("tower", cs))
                    elif not a.get("landing_cleared") and ("land", cs) not in self.done and a.get("eta_s", 999) <= 60:
                        eta = a["eta_s"]
                        if self._free(ctx, pav, cs, t + eta - 8, t + eta + 65):
                            self.claims[cs] = (pav, t + eta + 65)
                            act("issue_clearance", {"callsign": cs, "action": "cleared_to_land", "params": {"runway": end}},
                                "Cleared %s to land runway %s" % (cs, end), ["eta %.0f s" % eta, "runway free through %.0f s" % (eta + 65)],
                                ["JO 7110.65BB 3-10-5"], [{"option": "hold clearance", "reason": "runway free"}], 10, key=("land", cs))
                        elif eta <= 22 and ("ga", cs) not in self.done:
                            act("issue_clearance", {"callsign": cs, "action": "go_around"}, "Go around %s: runway not clear" % cs,
                                ["eta %.0f s, runway %s busy" % (eta, pav)], ["JO 7110.65BB 3-10-8"],
                                [{"option": "clear to land", "reason": "conflict"}], 60, key=("ga", cs))
                elif ph == "go_around":
                    self.done -= {("land", cs), ("ga", cs)}
                elif ph in ("taxi_in", "runway_exit") and a.get("stopped_s", 0) > 0.5 and not a.get("holding_short"):
                    if ("ground", cs) not in self.done:
                        act("issue_clearance", {"callsign": cs, "action": "contact_tower_or_ground",
                                                "params": {"facility": "ground", "frequency": "132.55"}},
                            "Handed %s to ground" % cs, ["%s clear of runway" % cs], ["JO 7110.65BB 3-10-9"], [], 20, key=("ground", cs))
                    elif ("taxi", cs) not in self.done:
                        act("issue_clearance", {"callsign": cs, "action": "taxi_via", "params": {"to": a["gate"]}},
                            "Taxi %s to gate %s" % (cs, a["gate"]), ["gate %s" % a["gate"]], ["JO 7110.65BB 3-7-2"], [], 20, key=("taxi", cs))
                if a.get("holding_short") and a.get("stopped_s", 0) > 1.0 and ph in ("taxi_in", "runway_exit"):
                    self._cross(ctx, a, act)
            elif kind == "departure":
                if ph == "at_gate" and a.get("ready") and vis >= 3 and ("push", cs) not in self.done:
                    act("issue_clearance", {"callsign": cs, "action": "pushback_approved"}, "Pushback approved for %s" % cs,
                        ["ready at gate %s" % a["gate"]], ["JO 7110.65BB 3-7-1"], [], 15, key=("push", cs))
                elif ph == "taxi_out" and a.get("stopped_s", 0) > 0.5 and ("taxi", cs) not in self.done:
                    act("issue_clearance", {"callsign": cs, "action": "taxi_via", "params": {"to": dep_rwys[0]}},
                        "Taxi %s to runway %s" % (cs, dep_rwys[0]), ["pushed back"], ["JO 7110.65BB 3-7-2"], [], 20, key=("taxi", cs))
                elif ph in ("hold_short", "taxi_out") and a.get("holding_short") and a.get("stopped_s", 0) > 1.0:
                    pav = a["holding_short"]
                    if END_OF.get(pav) != a.get("rwy") and PAV.get(a.get("rwy", "")) != pav:
                        self._cross(ctx, a, act)
                    elif ("luaw", cs) not in self.done and self._free(ctx, pav, cs, t, t + 40) and not self._landing_due(ctx, pav, 100, cs) \
                            and t - self.last_takeoff.get(pav, -1e9) >= self.takeoff_gap_s:
                        self.claims[cs] = (pav, t + 60)
                        act("issue_clearance", {"callsign": cs, "action": "line_up_and_wait", "params": {"runway": a["rwy"]}},
                            "%s line up and wait runway %s" % (cs, a["rwy"]), ["runway free", "no landing within 100 s"],
                            ["JO 7110.65BB 3-9-4"], [{"option": "hold short", "reason": "runway available"}], 10, key=("luaw", cs))
                elif ph == "line_up" and not a.get("takeoff_cleared") and ("to", cs) not in self.done:
                    pav = PAV[a["rwy"]]
                    if self._free(ctx, pav, cs, t, t + 30) and not self._landing_due(ctx, pav, 55, cs) and \
                            t - self.last_takeoff.get(pav, -1e9) >= self.takeoff_gap_s:
                        self.last_takeoff[pav] = t
                        self.claims[cs] = (pav, t + 60)
                        act("issue_clearance", {"callsign": cs, "action": "cleared_for_takeoff", "params": {"runway": a["rwy"]}},
                            "Cleared %s for takeoff runway %s" % (cs, a["rwy"]), ["runway free", "no landing within 55 s"],
                            ["JO 7110.65BB 3-9-10", "3-9-6 spacing"], [{"option": "wait", "reason": "conditions met"}], 15, key=("to", cs))
        return out

    def _cross(self, ctx: Dict[str, Any], a: Dict[str, Any], act: Callable[..., None]) -> None:
        cs, pav, t = a["cs"], a["holding_short"], ctx["t"]
        key = ("cross", cs, pav)
        if key in self.done or not self._free(ctx, pav, cs, t, t + 45) or self._landing_due(ctx, pav, 90, cs):
            return
        self.claims[cs] = (pav, t + 45)
        act("issue_clearance", {"callsign": cs, "action": "cross_runway", "params": {"runway": END_OF[pav]}},
            "Cleared %s to cross runway %s" % (cs, END_OF[pav]), ["runway free 45 s", "no landing within 90 s"],
            ["JO 7110.65BB 3-7-2"], [{"option": "keep holding", "reason": "runway clear"}], 20, key=key)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: `client.messages.create(**kw)` returns scripted responses."""

    def __init__(self, policy: Optional[ScriptedPolicy] = None, latency_s: float = 0.0, fail: Any = None,
                 script: Optional[Callable[[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]]]] = None, keep_going: bool = False) -> None:
        self.policy = policy or ScriptedPolicy()
        self.latency_s = latency_s
        self.fail = fail              # None, an Exception instance to raise, or a callable(call_no) -> Exception|None
        self.keep_going = keep_going  # with a script: keep answering tool results with more tool calls
        self.script = script          # optional: ctx -> [(tool, args)] replaces the policy
        self.messages = self
        self.calls: List[Dict[str, Any]] = []
        self.in_flight = 0
        self._lock = threading.Lock()

    def create(self, **kw: Any) -> Any:
        with self._lock:
            self.calls.append(kw)
            n = len(self.calls)
            self.in_flight += 1
        try:
            if self.latency_s:
                time.sleep(self.latency_s)
            exc = self.fail(n) if callable(self.fail) else self.fail
            if exc is not None:
                raise exc
            msgs = kw["messages"]
            if len(msgs) > 1 and not self.keep_going:      # tool results came back: finish the wake
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done; next look in a few seconds.")], stop_reason="end_turn")
            text = msgs[0]["content"]
            ctx = json.loads(text[text.index("\n") + 1:])
            if self.script is not None:
                calls = [(n_, a_) for n_, a_ in self.script(ctx)]
            else:
                calls = []
                for name, args, rec in self.policy.plan(ctx)[: self.policy.max_pairs]:
                    calls.append((name, args))
                    if rec:
                        calls.append(("record_decision", rec))
            if not calls:
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Nothing needs a decision.")], stop_reason="end_turn")
            return SimpleNamespace(content=[tool_use(i, n_, a_) for i, (n_, a_) in enumerate(calls)], stop_reason="tool_use")
        finally:
            with self._lock:
                self.in_flight -= 1
