# Voices (ElevenLabs)

Pilot and tower voices for the radio. Standard library only (Python 3.9+), no extra installs.

## Hear it

- **Cast audition:** `cd backend/voice && python3 -m http.server 8765`, then open http://127.0.0.1:8765/preview/ (plays the committed samples, no key needed).
- **Live in the sim:** put `ELEVENLABS_API_KEY=...` in the repo-root `.env`, run `SCENARIO=sim/scenarios/kphx_smoke.json make demo`, open http://localhost:5173 and click once to enable audio.

## How it works

- `tts.py` is the service. `Caster` gives each aircraft a pilot voice in spawn order: every voice once before repeats, and the Indian captain (Amrut Deshmukh, first in the cast) only ever once. Tower lines go to Ground, Tower north (runway 8/26) or Tower south (the 7/25 pair) from the words in the line.
- **Rule:** towers only speak while a human-style controller runs the airport (`tower` or `human`). When an AI agent controls it, tower lines are silent (`tower_voiced()`); pilots still talk.
- Clips are cached in `cache/` (gitignored) by a hash of voice + model + text, so a repeated line never costs credits twice. If ElevenLabs fails, macOS `say` makes the clip.
- The sim viewer serves clips at `GET /voice/{line_id}` (`api/main.py`; 204 = deliberately silent) and `web/src/voice.ts` plays them in order through a radio filter, falling back to browser voices.
- This is written to be reusable for A7 in the main backend: call `Caster.role_for(...)` and `synthesize(...)` and emit the `audio` event.

## Cast (`cast.json`)

`"source"` picks which voice id every role uses:

| source | what | plan |
|---|---|---|
| `builtin` (current) | ElevenLabs default voices. Pilots use `eleven_v3` with a style tag per line (e.g. `[strong Indian accent]`); towers use `eleven_flash_v2_5` | free |
| `designed` | custom voices from the `design` descriptions, made with `design_voices.py` | paid (saving voices via API) |
| `library` | Gus's original library picks (`library_id`) | paid |

Free plan: about 10k credits a month (as far as we know). One smoke run is roughly 3-4k credits the first time and free after (cached). `generate_samples.py` rebuilds the audition clips.
