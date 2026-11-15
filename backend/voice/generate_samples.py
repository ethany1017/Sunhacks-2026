"""Generate one sample radio line per voice in cast.json, for auditioning the cast.

Usage (from the repo root):  python3 backend/voice/generate_samples.py
Reads ELEVENLABS_API_KEY from the repo-root .env. Writes backend/voice/samples/*.mp3 and samples/manifest.json.
Uses backend/voice/tts.py, so clips share its cache: unchanged lines are not regenerated and re-runs cost nothing.
Standard library only, so it runs on any Python 3.9+.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLES = HERE / "samples"

TOWER_LINES = {
    "tower_north": "Southwest twenty-four thirteen, Phoenix Tower, wind two six zero at eight, runway two six, cleared for takeoff.",
    "tower_south": "American nineteen eighty-two, Phoenix Tower, runway two five left, cleared to land. Traffic holding short of two five left.",
    "ground": "SkyWest fifty-one forty, Phoenix Ground, runway two five right, taxi via Tango, Echo, hold short of runway two five left.",
}
PILOT_LINES = [
    "Runway two five left, cleared to land, Air India one two one.",
    "Runway two six, cleared for takeoff, Southwest twenty-four thirteen.",
    "Hold short of two five left, SkyWest fifty-one forty.",
    "Phoenix Tower, Delta eleven twenty-two, ten miles out on the I-L-S two five left.",
    "Line up and wait, runway two five right, United three oh seven.",
    "Taxi to two five right via Tango, Echo, Frontier nineteen sixty-five.",
    "Going around, American nineteen eighty-two.",
    "Cross runway two five left at Delta, Alaska six forty-four.",
    "Contact ground point niner, good day, Envoy thirty-four ten.",
    "Phoenix Ground, Southwest eight twelve, ready to push, gate C six.",
]


def load_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set in .env")
    return key


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    import shutil
    sys.path.insert(0, str(ROOT))
    from backend.voice import tts                      # same voices, models and style tags as the live sim

    cast = tts.load_cast()
    SAMPLES.mkdir(exist_ok=True)
    jobs = [("tower", t, TOWER_LINES[t["role"]], t["position"]) for t in cast["towers"]]
    jobs += [("pilot", p, PILOT_LINES[i % len(PILOT_LINES)], "Pilot " + (p.get("style") or "")) for i, p in enumerate(cast["pilots"])]
    manifest, failed = [], 0
    for kind, role, text, position in jobs:
        path, how = tts.synthesize(cast, role, text)
        if path is None or how.startswith("say"):
            print("FAILED %-22s %s" % (role["name"], how))
            failed += 1
            continue
        fname = "%s-%s%s" % (kind, slug(role["name"]), path.suffix)
        shutil.copyfile(path, SAMPLES / fname)
        model = role.get("model_id") or cast["model_id"]
        manifest.append({"kind": kind, "name": role["name"], "position": position.strip(), "voice": role["builtin"]["name"],
                         "voice_id": tts.voice_id(cast, role), "source": cast["source"], "model": model, "text": text, "file": fname})
        print("%-10s %-5s %-22s (%s, %s)" % (how, kind, role["name"], role["builtin"]["name"], model))
    keep = {m["file"] for m in manifest} | {"manifest.json"}
    for old in SAMPLES.iterdir():
        if old.name not in keep:
            old.unlink()
    (SAMPLES / "manifest.json").write_text(json.dumps({"model_id": cast["model_id"], "source": cast["source"], "clips": manifest},
                                                      indent=2) + "\n")
    print("%d clips, %d failed -> %s" % (len(manifest), failed, SAMPLES))


if __name__ == "__main__":
    main()
