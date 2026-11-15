"""Text to speech for clearances (tower voice) and pilot readbacks (2-3 pilot voices, stable per callsign).

Each line is tried per engine in order: ElevenLabs (only with a key and a voice id), then macOS `say`. For each engine
the cached file (backend/audio_cache/<hash of engine, voice, text>.<ext>) is used if present, else it is synthesised
once and cached. Every engine failing means no audio for that line; the text still shows on the dashboard.
Synthesis runs on one worker thread, in order, so it never blocks the run loop.
"""
import hashlib
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import zlib
from collections import Counter
from pathlib import Path

import httpx

CACHE_DIR = Path(__file__).resolve().parents[1] / "audio_cache"
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
ELEVEN_MODEL = "eleven_flash_v2_5"
ELEVEN_TIMEOUT_S = 4.0
ELEVEN_BACKOFF_S = 60.0  # after a failure, skip ElevenLabs this long so an outage doesn't add a timeout per line
SAY_TOWER = "Samantha"
SAY_PILOTS = ("Daniel", "Karen", "Moira")
SAY_TIMEOUT_S = 15.0
log = logging.getLogger(__name__)


def cache_name(engine, voice, text, ext):
    return hashlib.sha256(f"{engine}\n{voice}\n{text}".encode()).hexdigest()[:32] + ext


def pick(callsign, voices):
    """Stable per callsign across runs and processes (unlike hash())."""
    return voices[zlib.crc32((callsign or "").encode()) % len(voices)]


def elevenlabs_tts(api_key, voice, text, path):
    r = httpx.post(ELEVEN_URL.format(voice=voice), headers={"xi-api-key": api_key},
                   json={"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL") or ELEVEN_MODEL},
                   timeout=ELEVEN_TIMEOUT_S)
    r.raise_for_status()
    if not r.content or not r.headers.get("content-type", "").startswith("audio"):
        raise RuntimeError(f"ElevenLabs returned {r.headers.get('content-type')!r}, not audio")
    Path(path).write_bytes(r.content)


def say_tts(voice, text, path):
    # 16-bit little-endian WAV: plays in every browser (say's default AIFF/m4a LPCM does not).
    subprocess.run(["say", "-v", voice, "-o", str(path), "--data-format=LEI16@22050", text],
                   check=True, timeout=SAY_TIMEOUT_S, capture_output=True)


class Voice:
    def __init__(self, cache_dir=CACHE_DIR, env=None, eleven=elevenlabs_tts, say=say_tts, have_say=None):
        env = os.environ if env is None else env
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = env.get("ELEVENLABS_API_KEY") or None
        self.eleven_tower = env.get("ELEVENLABS_VOICE_TOWER") or None
        self.eleven_pilots = [v for v in (env.get(f"ELEVENLABS_VOICE_PILOT_{i}") for i in (1, 2, 3)) if v]
        self.eleven, self.say = eleven, say
        self.have_say = shutil.which("say") is not None if have_say is None else have_say
        self._eleven_down_until = 0.0
        self.stats = Counter()  # cache / elevenlabs / say / none
        self._q = queue.Queue()
        self._thread = None

    def engines(self, speaker, callsign):
        """[(engine, voice, ext)] to try, best first."""
        out = []
        if self.api_key:
            v = self.eleven_tower if speaker == "tower" else (pick(callsign, self.eleven_pilots) if self.eleven_pilots else None)
            if v:
                out.append(("elevenlabs", v, ".mp3"))
        if self.have_say:
            out.append(("say", SAY_TOWER if speaker == "tower" else pick(callsign, SAY_PILOTS), ".wav"))
        return out

    def synth(self, speaker, callsign, text):
        """Blocking. Returns (file name in cache_dir, source, voice) or None when no engine produced audio."""
        for engine, voice, ext in self.engines(speaker, callsign):
            name = cache_name(engine, voice, text, ext)
            path = self.cache_dir / name
            if path.exists():
                self.stats["cache"] += 1
                return name, "cache", voice
            if engine == "elevenlabs" and time.monotonic() < self._eleven_down_until:
                continue
            tmp = path.with_suffix(f".tmp{ext}")  # never leave a half-written file under the cached name
            try:
                if engine == "elevenlabs":
                    self.eleven(self.api_key, voice, text, tmp)
                else:
                    self.say(voice, text, tmp)
                tmp.replace(path)
                self.stats[engine] += 1
                return name, engine, voice
            except Exception as e:
                log.warning("%s TTS failed (%s); falling back", engine, e)
                if engine == "elevenlabs":
                    self._eleven_down_until = time.monotonic() + ELEVEN_BACKOFF_S
                tmp.unlink(missing_ok=True)
        self.stats["none"] += 1
        return None

    # -- background worker: one thread, lines in the order they were queued ---------------------------------------
    def speak(self, line, on_ready):
        """line: {speaker, callsign, text, ...}. on_ready(line, name, source, voice) runs on the worker thread."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="voice_out", daemon=True)
            self._thread.start()
        self._q.put((line, on_ready))

    def clear(self):
        """Drop lines not yet synthesised (a new run started)."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _work(self):
        while True:
            line, on_ready = self._q.get()
            try:
                res = self.synth(line["speaker"], line["callsign"], line["text"])
                if res:
                    on_ready(line, *res)
            except Exception:
                log.exception("voice line failed")
