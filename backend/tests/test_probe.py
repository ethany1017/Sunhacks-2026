"""Ground probe prediction: a pushback is predicted at push speed, a taxiing aircraft still accelerates (round G4)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from atc.planner import PROBE_S, Planner  # noqa: E402
from simif import connect  # noqa: E402


def test_pushback_is_predicted_at_push_speed_and_taxi_still_accelerates():
    si = connect()
    p = Planner(si, lambda *a, **k: None)
    g = p.g
    stand = g.stands["A11"]
    lon, lat = g.frame.to_lonlat(*stand.xy)

    def fake(phase, speed, **kw):
        return dict({"callsign": "T1", "phase": phase, "gate": "A11", "lon": lon, "lat": lat, "heading_deg": 270.0,
                     "speed_mps": speed, "next_node": None}, **kw)

    f = p._future(fake("pushback", 1.1), 1.1)
    pushed = float(np.hypot(*(f[-1] - f[0])))
    assert pushed <= 1.1 * PROBE_S + 0.5        # LR21: at taxi speed it "moved" far enough to fake a clash

    p.routes["T1"] = [stand.lane_node] + list(si.taxi_route(stand.lane_node, "a136") or [])[1:]
    f2 = p._future(fake("taxi_out", 1.1, next_node=p.routes["T1"][0]), 1.1)
    assert float(np.hypot(*(f2[-1] - f2[0]))) > pushed + 20
