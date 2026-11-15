import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atc import weather_live  # noqa: E402

# Trimmed real response from aviationweather.gov (2026-11-15).
SAMPLE = [{"icaoId": "KPHX", "obsTime": 1794729060, "wdir": 100, "wspd": 5, "visib": "10+",
           "rawOb": "METAR KPHX 150751Z 10005KT 10SM FEW050 BKN075 17/13 A3002 RMK AO2 SLP154 T01670128"}]


def fail(icao):
    raise OSError("network is unreachable")


def test_parse():
    wx = weather_live.parse(SAMPLE)
    assert wx["metar"].startswith("METAR KPHX 150751Z 10005KT")
    assert (wx["icao"], wx["wind_dir"], wx["wind_kt"], wx["gust_kt"], wx["vis_sm"]) == ("KPHX", 100, 5, None, "10+")
    assert wx["obs_time"] == "2026-11-15T07:51Z"


def test_live_fetch_writes_cache(tmp_path):
    asked = []
    wx = weather_live.get("KPHX", fetcher=lambda icao: asked.append(icao) or SAMPLE, cache_dir=tmp_path)
    assert asked == ["KPHX"] and wx["source"] == "live" and wx["wind_kt"] == 5
    assert json.loads((tmp_path / "KPHX.json").read_text())["metar"] == SAMPLE[0]["rawOb"]


def test_failed_fetch_uses_cache_then_unavailable(tmp_path):
    assert weather_live.get("KPHX", fetcher=fail, cache_dir=tmp_path) == {"icao": "KPHX", "source": "unavailable"}
    weather_live.get("KPHX", fetcher=lambda icao: SAMPLE, cache_dir=tmp_path)
    wx = weather_live.get("KPHX", fetcher=fail, cache_dir=tmp_path)
    assert wx["source"] == "cache" and wx["metar"] == SAMPLE[0]["rawOb"]
    # an empty or junk response is a failure too, and must not overwrite the good cache
    wx = weather_live.get("KPHX", fetcher=lambda icao: [], cache_dir=tmp_path)
    assert wx["source"] == "cache" and wx["metar"] == SAMPLE[0]["rawOb"]


def test_planner_and_safety_never_read_it():
    atc = Path(weather_live.__file__).parent
    for name in ("planner.py", "safety.py", "controller.py"):
        assert "weather_live" not in (atc / name).read_text()
