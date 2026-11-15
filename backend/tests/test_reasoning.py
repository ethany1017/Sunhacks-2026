import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atc import reasoning  # noqa: E402

OPTIONS = [{"id": "switch", "label": "turn the airport round to east flow"}, {"id": "keep", "label": "keep west flow"}]


class FakeClient:
    """Stands in for AsyncAnthropic: `messages.create` answers with `text`, or sleeps `delay` s first."""

    def __init__(self, text="", delay=0.0, error=None):
        self.text, self.delay, self.error, self.calls = text, delay, error, []
        self.messages = self

    async def create(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


def run(client, **kw):
    return asyncio.run(reasoning.decide("Wind 090/14, tailwind 12 kt on 25L.", OPTIONS, "switch", "Tailwind limit 10 kt.",
                                        client=client, **kw))


def test_valid_answer_is_claudes():
    c = FakeClient('{"choice": "keep", "summary": "Keep west flow until the arrival bank lands."}')
    assert run(c) == {"choice": "keep", "explanation": "Keep west flow until the arrival bank lands.", "source": "claude"}
    sent = c.calls[0]
    assert sent["model"] == reasoning.DEFAULT_MODEL and sent["max_tokens"] <= 200
    text = sent["system"] + sent["messages"][0]["content"]
    assert "Tailwind limit 10 kt." in text and "keep: keep west flow" in text and "Wind 090/14" in text
    assert "why" not in text.lower() and "weigh" not in text.lower()


def test_code_fenced_json_and_model_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model")
    c = FakeClient('```json\n{"choice": "switch", "summary": "Turn round now."}\n```')
    assert run(c)["source"] == "claude" and c.calls[0]["model"] == "some-model"


def test_invalid_json_falls_back():
    r = run(FakeClient("I would keep west flow."))
    assert r["choice"] == "switch" and r["source"] == "fallback" and "not valid JSON" in r["explanation"]


def test_unknown_option_falls_back():
    r = run(FakeClient('{"choice": "land_on_taxiway", "summary": "x"}'))
    assert r["choice"] == "switch" and r["source"] == "fallback" and "not a valid option" in r["explanation"]


def test_timeout_falls_back_within_the_timeout():
    loop = asyncio.new_event_loop()
    t0 = loop.time()
    r = loop.run_until_complete(reasoning.decide("s", OPTIONS, "switch", client=FakeClient('{"choice":"keep","summary":"x"}',
                                                                                          delay=5), timeout=0.2))
    assert loop.time() - t0 < 1.0
    loop.close()
    assert r["source"] == "fallback" and r["choice"] == "switch" and "timed out" in r["explanation"]


def test_api_error_falls_back():
    r = run(FakeClient(error=RuntimeError("boom")))
    assert r["source"] == "fallback" and "API error" in r["explanation"]


def test_no_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert reasoning.make_client() is None
    r = run(None)
    assert r == {"choice": "switch", "explanation": "Planner default: turn the airport round to east flow (no Claude API key).",
                 "source": "fallback"}
    # the planner-side adapter: no key -> the default at once, so the agent runs as it does without Claude
    adv = reasoning.Advisor()
    d = {"kind": "runway_flow", "options": OPTIONS, "default": "switch"}
    assert adv.choose(d) == dict(r, first=True)
    assert adv.choose(d)["first"] is False


def test_advisor_never_blocks_and_hands_over_later():
    async def main():
        adv = reasoning.Advisor(FakeClient('{"choice": "keep", "summary": "Keep west flow."}', delay=0.05))
        d = {"kind": "runway_flow", "options": OPTIONS, "default": "switch"}
        assert adv.choose(d) is None          # fired, not waited on
        assert adv.choose(d) is None          # still pending
        await asyncio.sleep(0.1)
        first = adv.choose(d)
        assert first == {"choice": "keep", "explanation": "Keep west flow.", "source": "claude", "first": True}
        assert adv.choose(d)["first"] is False and len(adv.client.calls) == 1
    asyncio.run(main())


def test_advisor_headless_uses_default():
    adv = reasoning.Advisor(FakeClient('{"choice": "keep", "summary": "x"}'))
    r = adv.choose({"kind": "runway_flow", "options": OPTIONS, "default": "switch"})
    assert r["choice"] == "switch" and r["source"] == "fallback" and adv.client.calls == []


def test_advisor_asks_again_after_the_answer_goes_stale(monkeypatch):
    async def main():
        adv = reasoning.Advisor(FakeClient('{"choice": "keep", "summary": "Keep west flow."}'))
        d = {"kind": "runway_flow", "options": OPTIONS, "default": "switch"}
        assert adv.choose(d) is None
        await asyncio.sleep(0.01)
        assert adv.choose(d)["first"] is True
        monkeypatch.setattr(reasoning, "ANSWER_TTL_S", 0.0)
        await asyncio.sleep(0.01)
        assert adv.choose(d) is None   # re-asked, not blocked
        await asyncio.sleep(0.01)
        assert len(adv.client.calls) == 2 and adv.choose(d)["first"] is True
    asyncio.run(main())
