"""Fill backend/audio_cache/ with every clearance and readback of a run, so the demo plays from cache.

Runs each level headless once (same controller and seed as the demo will use: the run is deterministic), collects the
lines exactly as the live server would speak them, and synthesises each through atc.voice_out (ElevenLabs with a key,
else macOS `say`).

Usage (from backend/): .venv/bin/python scripts/prewarm_audio.py [--level normal_day light_rain] [--controller agent] [--seed N]
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import simif  # noqa: E402
from app.runner import Runner  # noqa: E402
from atc import voice_out  # noqa: E402


class _Collect:
    def __init__(self):
        self.lines = []

    def speak(self, line, on_ready):
        self.lines.append(line)

    def clear(self):
        pass


def collect(sim, level, controller, seed):
    """The (speaker, callsign, text) lines the live server would speak for this run, in order."""
    voice, written = _Collect(), []
    r = Runner(sim, voice=voice)
    flush = r.flush
    r.flush = lambda: (written.extend(r._buf), flush())
    r.start(level, controller, seed)
    r.run_headless()
    r._speak(written)
    return voice.lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", nargs="+", default=["normal_day"])
    ap.add_argument("--controller", default="agent")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    os.environ["EVENTS_DB"] = os.path.join(tempfile.mkdtemp(prefix="prewarm-"), "events.db")  # never the live store
    sim, voice = simif.connect(), voice_out.Voice()
    print("engines:", [e for e, _, _ in voice.engines("tower", None)] or "none (no ElevenLabs key and no `say`)")
    for level in args.level:
        t0 = time.monotonic()
        lines = collect(sim, level, args.controller, args.seed)
        before = voice.stats.copy()
        for ln in lines:
            voice.synth(ln["speaker"], ln["callsign"], ln["text"])
        d = voice.stats - before
        print(f"{level} ({args.controller}, seed {sim.get_level()['seed']}): {len(lines)} lines, "
              f"{d['elevenlabs'] + d['say']} synthesised (elevenlabs {d['elevenlabs']}, say {d['say']}), "
              f"{d['cache']} already cached, {d['none']} without audio, {time.monotonic() - t0:.0f} s")


if __name__ == "__main__":
    main()
