"""Live NOAA METAR for the dashboard's top bar (A5.2). DISPLAY ONLY: the planner and the safety engine never read it;
the agent flies the level's simulated weather. Refreshed in a background task, cached on disk for when NOAA is down."""
import asyncio
import json
import logging
import time
import urllib.request
from pathlib import Path

URL = "https://aviationweather.gov/api/data/metar?ids={icao}&format=json"
CACHE_DIR = Path(__file__).resolve().parents[1] / "weather_cache"
REFRESH_S = 300.0   # METARs are issued hourly (plus specials); every 5 min is plenty
TIMEOUT_S = 5.0
log = logging.getLogger(__name__)


def fetch(icao):
    req = urllib.request.Request(URL.format(icao=icao), headers={"User-Agent": "tower-zero-demo"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def parse(rows):
    """NOAA's JSON list -> {icao, metar, wind_dir, wind_kt, gust_kt, vis_sm, obs_time}. ValueError if no report."""
    if not rows or not isinstance(rows, list) or not rows[0].get("rawOb"):
        raise ValueError("no METAR in the response")
    r = rows[0]
    obs = r.get("obsTime")
    return {"icao": r.get("icaoId"), "metar": r["rawOb"], "wind_dir": r.get("wdir"), "wind_kt": r.get("wspd"),
            "gust_kt": r.get("wgst"), "vis_sm": r.get("visib"),
            "obs_time": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(obs)) if isinstance(obs, (int, float)) else None}


def get(icao, fetcher=fetch, cache_dir=CACHE_DIR):
    """Fresh METAR (source "live"), else the last good one (source "cache"), else {"source": "unavailable"}."""
    path = Path(cache_dir) / f"{icao}.json"
    try:
        wx = parse(fetcher(icao))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(wx))
        return dict(wx, source="live")
    except Exception as e:  # network, HTTP, bad JSON: all the same to a display
        log.warning("live METAR fetch failed (%s); using the cache", e)
    try:
        return dict(json.loads(path.read_text()), source="cache")
    except Exception:
        return {"icao": icao, "source": "unavailable"}


async def refresh_forever(icao, store, every_s=REFRESH_S):
    """Keep store["wx"] current. The fetch runs in a worker thread, so the run loop never waits on NOAA."""
    while True:
        store["wx"] = await asyncio.to_thread(get, icao)
        await asyncio.sleep(every_s)
