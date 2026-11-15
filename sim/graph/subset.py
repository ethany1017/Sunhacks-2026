"""Restrict the graph to a region (fallback if the full airport misbehaves)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import networkx as nx

# name -> (west, south, east, north); None keeps the whole airport.
REGIONS: Dict[str, Optional[Tuple[float, float, float, float]]] = {
    "full": None,
    # the two south runways (07L/25R, 07R/25L) plus the terminal 4 area
    "south": (-112.04, 33.42, -111.98, 33.4375),
}


def subset_graph(graph: Dict[str, Any], region: str = "full") -> Dict[str, Any]:
    """Return a copy of the graph restricted to the named region's largest connected component."""
    box = REGIONS[region]
    if box is None:
        return graph
    w, s, e, n = box
    inside = {nd["id"] for nd in graph["nodes"] if w <= nd["lon"] <= e and s <= nd["lat"] <= n}
    g = nx.Graph()
    for ed in graph["edges"]:
        if ed["from"] in inside and ed["to"] in inside:
            g.add_edge(ed["from"], ed["to"])
    keep = max(nx.connected_components(g), key=len)
    out = dict(graph)
    out["nodes"] = [nd for nd in graph["nodes"] if nd["id"] in keep]
    out["edges"] = [ed for ed in graph["edges"] if ed["from"] in keep and ed["to"] in keep]
    out["stands"] = [st for st in graph["stands"] if st["node"] in keep]
    out["holds"] = [h for h in graph["holds"] if h["node"] in keep]
    out["crossings"] = [c for c in graph["crossings"] if c["node"] in keep]
    out["runways"] = [r for r in graph["runways"] if all(end["node"] in keep for end in r["ends"].values())]
    return out
