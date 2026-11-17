"""Voice service: turns any radio line into audio with the cast in cast.json.

- Casting: every aircraft gets a pilot voice in spawn order, so a scenario always sounds the same. Each pilot voice is used
  once before any repeats, and the first pilot in the cast (the Indian captain) is never repeated, so there is exactly one.
  Tower lines go to Ground, Tower north (runway 8/26) or Tower south (the 7/25 pair) from the words in the line.
- Rule: towers only have a voice when a human-style controller is in charge (the built-in tower or the Human). When an AI
  agent controls the airport, tower lines are silent and only the pilots speak.
- Audio: ElevenLabs, cached on disk by a hash of (voice, model, text), so a repeated line never costs credits twice.
  If ElevenLabs fails, macOS `say` makes the clip instead. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = Path(os.environ.get("VOICE_CACHE_DIR", str(HERE / "cache")))
API = "https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_128"
TIMEOUT_S = 20
VOICED_TOWER_CONTROLLERS = {"tower", "human"}     # anything else (an AI agent) leaves the tower silent
_lock = threading.Lock()


def tower_voiced(controller_name: str) -> bool:
    return controller_name.lower() in VOICED_TOWER_CONTROLLERS


def load_cast() -> Dict[str, Any]:
    return json.loads((HERE / "cast.json").read_text())


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
    return key


def voice_id(cast: Dict[str, Any], role: Dict[str, Any]) -> str:
    src = cast.get("source", "builtin")
    if src == "library" and role.get("library_id"):
        return role["library_id"]
    if src == "designed" and role.get("designed"):
        return role["designed"]["id"]
    return role["builtin"]["id"]


def tower_role(cast: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Pick the tower position from the line: Ground for ground movement, else the runway it talks about."""
    t = text.lower()
    roles = {r["role"]: r for r in cast["towers"]}
    if "ground" in t or "pushback" in t or ("taxi" in t and "cleared" not in t):
        return roles["ground"]
    if re.search(r"\b(two six|eight)\b", t) or "one one eight point seven" in t:
        return roles["tower_north"]
    return roles["tower_south"]


class Caster:
    """Assigns pilot voices to callsigns for one run (reset it when the run resets)."""

    def __init__(self, cast: Optional[Dict[str, Any]] = None) -> None:
        self.cast = cast or load_cast()
        self.assigned: Dict[str, Dict[str, Any]] = {}

    def pilot(self, callsign: str, order: Optional[int] = None) -> Dict[str, Any]:
        if callsign not in self.assigned:
            pilots = self.cast["pilots"]
            i = len(self.assigned) if order is None else order
            self.assigned[callsign] = pilots[i] if i < len(pilots) else pilots[1 + (i - len(pilots)) % (len(pilots) - 1)]
        return self.assigned[callsign]

    def role_for(self, speaker: str, callsign: str, text: str, order: Optional[int] = None) -> Dict[str, Any]:
        return self.pilot(callsign, order) if speaker == "pilot" else tower_role(self.cast, text)


def _clip_path(vid: str, model: str, text: str, ext: str) -> Path:
    h = hashlib.sha256(("%s|%s|%s" % (vid, model, text)).encode()).hexdigest()[:20]
    return CACHE / ("%s.%s" % (h, ext))


def _elevenlabs(key: str, vid: str, model: str, text: str) -> bytes:
    body = json.dumps({"text": text, "model_id": model}).encode()
    req = urllib.request.Request(API % vid, data=body, headers={"xi-api-key": key, "Content-Type": "application/json",
                                                                "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


def _say(text: str, out: Path, female: bool) -> bool:
    voice = ["-v", "Samantha"] if female else []
    try:
        subprocess.run(["say", *voice, "--file-format=m4af", "--data-format=aac", "-o", str(out), text],
                       check=True, timeout=30, capture_output=True)
        return out.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def synthesize(cast: Dict[str, Any], role: Dict[str, Any], text: str) -> Tuple[Optional[Path], str]:
    """Audio file for this role saying this text: (path, "cached" | "elevenlabs" | "say:<reason>"), path None on failure."""
    vid = voice_id(cast, role)
    model = role.get("model_id") or cast.get("model_id", "eleven_flash_v2_5")
    spoken = "%s %s" % (role["style"], text) if role.get("style") and model == "eleven_v3" else text
    CACHE.mkdir(parents=True, exist_ok=True)
    mp3 = _clip_path(vid, model, spoken, "mp3")
    if mp3.exists():
        return mp3, "cached"
    key = api_key()
    reason = "no ELEVENLABS_API_KEY"
    if key:
        try:
            data = _elevenlabs(key, vid, model, spoken)
            with _lock:
                tmp = mp3.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(mp3)
            return mp3, "elevenlabs"
        except urllib.error.HTTPError as e:
            reason = "HTTP %s %s" % (e.code, e.read()[:120].decode(errors="replace"))
        except (urllib.error.URLError, OSError) as e:
            reason = type(e).__name__
    m4a = _clip_path("say", "say", text, "m4a")
    if m4a.exists() or _say(text, m4a, female=role["builtin"]["name"] in ("Laura", "Matilda", "Jessica")):
        return m4a, "say:" + reason
    return None, reason
