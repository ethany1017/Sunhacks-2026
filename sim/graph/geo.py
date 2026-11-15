"""Local metric frame (azimuthal equidistant) shared by the graph builder and the simulator."""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

from pyproj import Transformer

XY = Tuple[float, float]


class Frame:
    """Converts WGS84 lon/lat to meters east/north of an origin and back."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0, self.lat0 = lon0, lat0
        crs = "+proj=aeqd +lat_0=%.8f +lon_0=%.8f +datum=WGS84 +units=m" % (lat0, lon0)
        self._fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self._inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def to_xy(self, lon: float, lat: float) -> XY:
        x, y = self._fwd.transform(lon, lat)
        return float(x), float(y)

    def to_lonlat(self, x: float, y: float) -> XY:
        lon, lat = self._inv.transform(x, y)
        return float(lon), float(lat)

    def line_to_lonlat(self, pts: Iterable[XY]) -> List[List[float]]:
        return [[round(v, 7) for v in self.to_lonlat(x, y)] for x, y in pts]


def bearing_deg(a: XY, b: XY) -> float:
    """Compass bearing (0 = north, clockwise) from a to b in the local frame."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference b - a in degrees, in (-180, 180]."""
    return (b - a + 180.0) % 360.0 - 180.0


def polyline_length(pts: Sequence[XY]) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
