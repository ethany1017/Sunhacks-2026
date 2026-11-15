"""A7 voice: cache keys, the fallback chain, stable pilot voices, and that headless runs never speak. No network, no
real `say` (fakes are injected)."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atc import voice_out  # noqa: E402

ENV = {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_VOICE_TOWER": "tw", "ELEVENLABS_VOICE_PILOT_1": "p1",
       "ELEVENLABS_VOICE_PILOT_2": "p2"}


class Fake:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def eleven(self, key, voice, text, path):
        self.calls.append(("elevenlabs", voice, text))
        if self.fail:
            raise RuntimeError("offline")
        Path(path).write_bytes(b"mp3")

    def say(self, voice, text, path):
        self.calls.append(("say", voice, text))
        if self.fail:
            raise RuntimeError("no say")
        Path(path).write_bytes(b"wav")


def make(tmp_path, env=ENV, eleven_fail=False, say_fail=False, have_say=True):
    fe, fs = Fake(eleven_fail), Fake(say_fail)
    v = voice_out.Voice(tmp_path, env=env, eleven=fe.eleven, say=fs.say, have_say=have_say)
    return v, fe, fs


def test_cache_key_stable_per_voice_and_text():
    a = voice_out.cache_name("say", "Samantha", "cleared to land", ".wav")
    assert a == voice_out.cache_name("say", "Samantha", "cleared to land", ".wav")
    assert a != voice_out.cache_name("say", "Daniel", "cleared to land", ".wav")
    assert a != voice_out.cache_name("say", "Samantha", "cleared for takeoff", ".wav")


def test_second_request_is_served_from_cache(tmp_path):
    v, fe, fs = make(tmp_path)
    first = v.synth("tower", "AAL1", "American 1, runway two six, cleared to land.")
    assert first[1] == "elevenlabs" and first[2] == "tw"
    again = v.synth("tower", "AAL1", "American 1, runway two six, cleared to land.")
    assert again == (first[0], "cache", "tw") and len(fe.calls) == 1 and not fs.calls
    assert not list(tmp_path.glob("*.tmp*"))


def test_elevenlabs_failure_falls_back_to_cache_then_say_then_none(tmp_path):
    text = "Southwest 5, hold short runway two five left."
    # 1. A line cached earlier by ElevenLabs plays from cache even with ElevenLabs down (no request made).
    v, _, _ = make(tmp_path)
    cached = v.synth("tower", "SWA5", text)[0]
    v, fe, fs = make(tmp_path, eleven_fail=True)
    assert v.synth("tower", "SWA5", text) == (cached, "cache", "tw") and not fe.calls
    # 2. Not cached: ElevenLabs fails, `say` makes it.
    name, source, voice = v.synth("tower", "SWA5", "new line")
    assert source == "say" and voice == voice_out.SAY_TOWER and (tmp_path / name).read_bytes() == b"wav"
    assert [c[0] for c in fe.calls] == ["elevenlabs"]
    # ... and ElevenLabs is skipped for a while after a failure (no timeout per line during an outage).
    v.synth("tower", "SWA5", "another line")
    assert len(fe.calls) == 1
    # 3. Every engine fails: no audio, and no half-written files left behind.
    v, fe, fs = make(tmp_path, eleven_fail=True, say_fail=True)
    assert v.synth("pilot", "SWA5", "third line") is None and v.stats["none"] == 1
    assert not list(tmp_path.glob("*.tmp*"))


def test_no_key_uses_say_and_no_say_means_no_audio(tmp_path):
    v, fe, fs = make(tmp_path, env={})
    assert v.synth("pilot", "UAL9", "Runway two six, cleared to land, United 9.")[1] == "say" and not fe.calls
    v, fe, fs = make(tmp_path, env={}, have_say=False)
    assert v.engines("tower", None) == [] and v.synth("tower", None, "x") is None


def test_pilot_voice_stable_per_callsign(tmp_path):
    v, _, _ = make(tmp_path)
    picks = {cs: v.engines("pilot", cs) for cs in ("AAL1", "SWA22", "UAL305", "DAL7", "SKW4410", "FFT88")}
    assert picks == {cs: v.engines("pilot", cs) for cs in picks}  # same answer every time
    assert {p[0][1] for p in picks.values()} == {"p1", "p2"}  # both pilot voices get used
    assert {p[1][1] for p in picks.values()} <= set(voice_out.SAY_PILOTS)
    assert v.engines("tower", "AAL1")[0][1] == "tw"


def test_worker_emits_lines_in_order(tmp_path):
    v, _, _ = make(tmp_path, env={})
    got, done = [], threading.Event()

    def ready(line, name, source, voice):
        got.append(line["text"])
        if len(got) == 5:
            done.set()

    for i in range(5):
        v.speak({"speaker": "tower", "callsign": "AAL1", "text": f"line {i}"}, ready)
    assert done.wait(5) and got == [f"line {i}" for i in range(5)]


def test_headless_runner_never_speaks_and_runs_identically(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    import simif
    from app.runner import Runner

    class Recorder:
        def __init__(self):
            self.lines = []

        def speak(self, line, on_ready):
            self.lines.append(line)

        def clear(self):
            pass

    sim = simif.connect()

    def run(voice):
        r, out = Runner(sim, voice=voice), []
        flush = r.flush
        r.flush = lambda: (out.extend(r._buf), flush())
        r.start("busy_arrivals", "agent", 3)
        r.run_headless()
        return [(e["t"], e["type"], e["callsign"], e["data"], e["reason"]) for e in out]

    rec = Recorder()
    plain, with_voice = run(None), run(rec)
    assert plain == with_voice and not rec.lines and not any(e[1] == "audio" for e in plain)
    # The live loop's hook turns exactly the clearances and readbacks into lines, in order.
    r = Runner(sim, voice=rec)
    r.run_id = 1
    evs = [{"t": t, "type": ty, "callsign": cs, "data": d} for t, ty, cs, d, _ in plain]
    r._speak(evs)
    spoken = [(e["type"], e["callsign"]) for e in evs if e["type"] in ("clearance", "readback")]
    assert spoken and [("clearance" if ln["speaker"] == "tower" else "readback", ln["callsign"]) for ln in rec.lines] == spoken
