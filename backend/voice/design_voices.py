"""Design a custom ElevenLabs voice for every role in cast.json that has a "design" description and no designed voice yet.

Usage (from the repo root):  python3 backend/voice/design_voices.py [--only NAME] [--redo]
Needs ELEVENLABS_API_KEY with the voice_generation permission. For each role it asks Voice Design for previews from the
description, saves the first preview as a permanent voice in the account, and writes its id into cast.json under
"designed". Roles that already have a designed voice are skipped unless --redo.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generate_samples import load_key  # noqa: E402

API = "https://api.elevenlabs.io"
DESIGN_MODEL = "eleven_multilingual_ttv_v2"


def post(key: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def design(key: str, role: dict) -> str:
    d = role["design"]
    previews = post(key, "/v1/text-to-voice/design",
                    {"voice_description": d["description"], "text": d["text"], "model_id": DESIGN_MODEL})["previews"]
    gid = previews[0]["generated_voice_id"]
    saved = post(key, "/v1/text-to-voice", {"voice_name": "KPHX %s" % role["name"],
                                            "voice_description": d["description"][:500], "generated_voice_id": gid})
    return saved["voice_id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="design just this role name")
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()
    path = HERE / "cast.json"
    cast = json.loads(path.read_text())
    key = load_key()
    failed = 0
    for role in cast["towers"] + cast["pilots"]:
        if "design" not in role or (args.only and role["name"] != args.only):
            continue
        if role.get("designed") and not args.redo:
            print("skip     %s (already %s)" % (role["name"], role["designed"]["id"]))
            continue
        try:
            vid = design(key, role)
        except urllib.error.HTTPError as e:
            print("FAILED   %s: HTTP %s %s" % (role["name"], e.code, e.read()[:240].decode(errors="replace")))
            failed += 1
            continue
        role["designed"] = {"name": "KPHX %s" % role["name"], "id": vid}
        path.write_text(json.dumps(cast, indent=2, ensure_ascii=False) + "\n")     # save after each, so a failure keeps progress
        print("designed %s -> %s" % (role["name"], vid))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
