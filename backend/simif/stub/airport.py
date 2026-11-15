"""Load the stub's airport files (config.json, airport.geojson, graph.json) from simif/stub/airport/."""
import json
from pathlib import Path

AIRPORT_DIR = Path(__file__).resolve().parent / "airport"
FILES = (("config", "config.json"), ("geojson", "airport.geojson"), ("graph", "graph.json"))


def load(directory=AIRPORT_DIR):
    """{"config", "geojson", "graph"} parsed from `directory`. ValueError naming the file if one is missing or not JSON."""
    out = {}
    for key, name in FILES:
        try:
            out[key] = json.loads((Path(directory) / name).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"{name}: cannot load ({e})") from e
    return out
