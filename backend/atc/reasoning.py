"""Claude picks one of the planner's valid options at a key decision (A4). Claude only CHOOSES among options the planner
already built; the chosen option still goes through the safety engine like any other proposal. Any failure (no key,
timeout, API error, invalid JSON, an id not in the list) gives the planner's default with source "fallback".

Wired in atc/controller.py (`Controller.choose` is the planner's `choose` hook; docs/EVAL.md "Claude at key decisions
(A4)"). `Advisor.choose` below never blocks; it returns None while Claude is answering, then the answer, emitted once
as an `ai_decision` event. With no API key or no running event loop it returns the default at once.
"""
import asyncio
import json
import logging
import os
import time

DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_S = 3.0
MAX_TOKENS = 120
MAX_KEPT = 500      # remembered decisions before the finished ones are dropped
ANSWER_TTL_S = 30.0  # an answer is reused this long (wall clock), then the same decision is asked again
SYSTEM = ("You assist the tower controller of a simulated airport (simulation only, no real aircraft). You get procedure "
          "notes, the current situation and a list of valid options, each already safe to propose. Pick one option. "
          'Answer with JSON only, no other text: {"choice": "<option id>", "summary": "<one-line summary of the choice, '
          'under 25 words>"}')
log = logging.getLogger(__name__)


def _fallback(options, default, why):
    label = next((o.get("label") for o in options if o["id"] == default), None) or default
    return {"choice": default, "explanation": f"Planner default: {label} ({why}).", "source": "fallback"}


def make_client():
    """The official SDK's async client, or None without ANTHROPIC_API_KEY. No SDK retries: one try inside the timeout."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=key, max_retries=0, timeout=TIMEOUT_S)


def prompt(situation, options, default, procedures=""):
    opts = "\n".join(f"- {o['id']}: {o.get('label', '')}" for o in options)
    return (f"Procedures:\n{procedures or 'FAA JO 7110.65 tower procedures.'}\n\nSituation:\n{situation}\n\n"
            f"Valid options:\n{opts}\n\nPlanner default: {default}\nReply with the JSON object only.")


async def decide(situation, options, default, procedures="", client=None, model=None, timeout=None):
    """-> {choice, explanation, source}. Never raises; returns within `timeout` (default TIMEOUT_S) seconds."""
    timeout = TIMEOUT_S if timeout is None else timeout
    if client is None:
        return _fallback(options, default, "no Claude API key")
    try:
        msg = await asyncio.wait_for(client.messages.create(
            model=model or os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL, max_tokens=MAX_TOKENS,
            system=SYSTEM, messages=[{"role": "user", "content": prompt(situation, options, default, procedures)}]),
            timeout)
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        ans = json.loads(text)
        choice, summary = ans["choice"], " ".join(str(ans["summary"]).split())
    except asyncio.TimeoutError:
        return _fallback(options, default, f"Claude timed out after {timeout:g} s")
    except (ValueError, KeyError, TypeError):
        return _fallback(options, default, "Claude's answer was not valid JSON")
    except Exception as e:  # API or network error; only the class name is logged, never the request
        log.warning("Claude call failed: %s", type(e).__name__)
        return _fallback(options, default, "Claude API error")
    if not isinstance(choice, str) or choice not in {o["id"] for o in options}:
        return _fallback(options, default, f"Claude chose {str(choice)[:20]!r}, not a valid option")
    return {"choice": choice, "explanation": summary[:200] or "Chosen by Claude.", "source": "claude"}


class Advisor:
    """Sync adapter for the planner's tick: fires `decide` as a task and hands the answer over on a later tick.
    The same decision (kind, callsign, option ids, default) gets one answer, reused on every re-check for ANSWER_TTL_S,
    so a "keep" answer does not stand forever while the situation behind the same options changes."""

    def __init__(self, client=None):
        self.client = client if client is not None else make_client()
        self.answers = {}   # decision key -> asyncio.Task (pending) or the result dict
        self.since = {}     # decision key -> monotonic time the answer was handed over

    def choose(self, decision, situation="", procedures=""):
        """{choice, explanation, source, first} now, or None while Claude is still answering (at most TIMEOUT_S).
        `first` is True the first time a decision's answer is handed over."""
        options, default = decision["options"], decision["default"]
        k = (decision["kind"], decision.get("callsign"), tuple(o["id"] for o in options), default)
        got = self.answers.get(k)
        if got is not None and not isinstance(got, asyncio.Task) and time.monotonic() - self.since[k] > ANSWER_TTL_S:
            got = None  # stale: ask again
        if got is None:
            try:
                loop = asyncio.get_running_loop() if self.client is not None else None
            except RuntimeError:
                loop = None  # headless harness: no loop to run the call on
            if loop is None:
                self.answers[k] = _fallback(options, default, "no Claude API key" if self.client is None else "headless run")
                self.since[k] = time.monotonic()
                return dict(self.answers[k], first=True)
            if len(self.answers) > MAX_KEPT:  # ponytail: drop finished answers wholesale; LRU if it ever matters
                self.answers = {a: v for a, v in self.answers.items() if isinstance(v, asyncio.Task) and not v.done()}
            self.answers[k] = loop.create_task(decide(situation, options, default, procedures, self.client))
            return None
        if isinstance(got, asyncio.Task):
            if not got.done():
                return None
            self.answers[k] = got.result()
            self.since[k] = time.monotonic()
            return dict(self.answers[k], first=True)
        return dict(got, first=False)
