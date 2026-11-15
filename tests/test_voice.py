"""Voice service rules (backend/voice/tts.py) and the sim API's /voice endpoint. No network: ElevenLabs is faked."""
from __future__ import annotations

import time
from typing import Any, List

import pytest
from fastapi.testclient import TestClient

from backend.voice import tts
from tests.conftest import NoopController


def test_one_indian_pilot_and_every_voice_used_before_repeats() -> None:
    c = tts.Caster()
    names = [c.pilot("AC%d" % i, i)["name"] for i in range(25)]
    pilots = [p["name"] for p in c.cast["pilots"]]
    assert names[:len(pilots)] == pilots                       # each voice once, in cast order
    assert names.count("Amrut Deshmukh") == 1                  # exactly one Indian captain
    assert c.pilot("AC3")["name"] == names[3]                  # sticky per callsign


def test_tower_positions_from_the_line() -> None:
    cast = tts.load_cast()
    role = lambda t: tts.tower_role(cast, t)["role"]          # noqa: E731
    assert role("American eight twenty-nine, Phoenix ground, pushback approved") == "ground"
    assert role("Southwest five forty-one, runway two six, cleared to land") == "tower_north"
    assert role("Sky West six eighty, runway two fife left, cleared for takeoff") == "tower_south"


def test_tower_silent_when_an_agent_controls() -> None:
    assert tts.tower_voiced("tower") and tts.tower_voiced("Human")
    assert not tts.tower_voiced("claude") and not tts.tower_voiced("agent")


def test_clips_are_cached_and_v3_gets_the_style_tag(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tts, "CACHE", tmp_path)
    monkeypatch.setattr(tts, "api_key", lambda: "k")
    sent: List[Any] = []
    monkeypatch.setattr(tts, "_elevenlabs", lambda key, vid, model, text: sent.append((vid, model, text)) or b"ID3fake")
    cast = tts.load_cast()
    indian = cast["pilots"][0]
    p1, how1 = tts.synthesize(cast, indian, "Cleared to land two five left")
    p2, how2 = tts.synthesize(cast, indian, "Cleared to land two five left")
    assert (how1, how2) == ("elevenlabs", "cached") and p1 == p2 and len(sent) == 1
    assert sent[0][1] == "eleven_v3" and sent[0][2].startswith("[strong Indian accent] ")
    tower = cast["towers"][0]
    tts.synthesize(cast, tower, "Runway two six, cleared for takeoff")
    assert sent[1][1] == "eleven_flash_v2_5" and not sent[1][2].startswith("[")


def test_falls_back_to_say_when_elevenlabs_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tts, "CACHE", tmp_path)
    monkeypatch.setattr(tts, "api_key", lambda: "")
    made: List[Any] = []
    monkeypatch.setattr(tts, "_say", lambda text, out, female: made.append(text) or out.write_bytes(b"m4a") or True)
    path, how = tts.synthesize(tts.load_cast(), tts.load_cast()["towers"][1], "Hold short")
    assert path is not None and path.suffix == ".m4a" and how.startswith("say:") and made == ["Hold short"]


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(tts, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(tts, "api_key", lambda: "k")
    monkeypatch.setattr(tts, "_elevenlabs", lambda key, vid, model, text: b"ID3fake")
    from api.main import app
    with TestClient(app) as c:
        yield c


def _wait_for_lines(client: TestClient) -> List[Any]:
    client.post("/control", json={"action": "speed", "value": 20})
    deadline = time.time() + 8
    while time.time() < deadline:
        voice = client.get("/state").json()["voice"]
        if any(v["speaker"] == "pilot" for v in voice) and any(v["speaker"] == "agent" for v in voice):
            return voice
        time.sleep(0.2)
    raise AssertionError("no radio lines")


def test_voice_endpoint_speaks_and_mutes_the_tower_under_an_agent(client: TestClient) -> None:
    voice = _wait_for_lines(client)
    pilot = next(v for v in voice if v["speaker"] == "pilot")
    tower = next(v for v in voice if v["speaker"] == "agent")
    r = client.get("/voice/%s" % pilot["id"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.headers["x-voice"]
    assert client.get("/voice/%s" % tower["id"]).status_code == 200            # the built-in tower has a voice
    from api.main import app
    app.state.sim.set_controller(type("Agent", (NoopController,), {"name": "agent"})())
    assert client.get("/voice/%s" % tower["id"]).status_code == 204            # agent in charge: tower silent
    assert client.get("/voice/%s" % pilot["id"]).status_code == 200            # pilots still talk
    assert client.get("/voice/v-99999").status_code == 404
