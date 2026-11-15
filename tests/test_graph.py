from __future__ import annotations

import json
from collections import Counter

import networkx as nx

from sim.graph.model import AirportGraph
from sim.graph.subset import subset_graph

ROOT_JSON = "data/processed/kphx_graph.json"


def test_runway_order_north_to_south(graph: AirportGraph) -> None:
    assert graph.meta["runway_order_north_to_south"] == ["08/26", "07L/25R", "07R/25L"]
    lats = {r.id: graph.lonlat(graph.node_xy[r.chain[0]])[1] for r in graph.runways.values()}
    assert lats["08/26"] > lats["07L/25R"] > lats["07R/25L"]


def test_runway_pavements_have_both_ends(graph: AirportGraph) -> None:
    assert set(graph.runways) == {"08/26", "07L/25R", "07R/25L"}
    for rw in graph.runways.values():
        assert len(rw.ends) == 2
        assert len(rw.polygon_ll) >= 5
        assert rw.length_m > 2000
    assert set(graph.end_runway) == {"08", "26", "07L", "25R", "07R", "25L"}
    assert round(graph.runways["07R/25L"].length_m) == 2377  # 7,800 ft


def test_graph_is_one_connected_component(graph: AirportGraph) -> None:
    assert nx.number_connected_components(graph.g) == 1
    assert graph.meta["report"]["taxiway_fraction_kept"] >= 0.95


def test_path_from_gate_to_each_runway_end_and_back(graph: AirportGraph) -> None:
    gates = sorted(s.name for s in graph.stands.values() if s.kind == "gate")
    sample = [gates[0], gates[len(gates) // 2], gates[-1]]
    for name in sample:
        stand = graph.stands[name]
        for end in graph.end_runway:
            entries = graph.entries(end)
            assert entries, "no taxiway entry to runway end %s" % end
            out = graph.route(stand.lane_node, entries[0][0])
            back = graph.route(entries[0][0], stand.node)
            assert out and back, "no route between gate %s and runway end %s" % (name, end)
            assert graph.route_length(out) > 50


def test_no_isolated_hold_positions(graph: AirportGraph) -> None:
    data = graph.data
    nodes = {n["id"] for n in data["nodes"]}
    degree = Counter()
    for e in data["edges"]:
        degree[e["from"]] += 1
        degree[e["to"]] += 1
    assert data["holds"]
    for h in data["holds"]:
        assert h["node"] in nodes and degree[h["node"]] >= 2, "hold %s is not on a through edge" % h["id"]
    assert any(h["runway"] for h in data["holds"])


def test_stands_attach_to_the_network(graph: AirportGraph) -> None:
    gates = [s for s in graph.stands.values() if s.kind == "gate"]
    assert len(gates) >= 100
    for s in gates:
        assert graph.g.degree(s.node) == 1 and s.lane_node in graph.g


def test_runway_crossings_exist(graph: AirportGraph) -> None:
    assert len(graph.data["crossings"]) > 20
    pavement_edges = [e for e in graph.edges.values() if e.pavement and e.kind != "runway"]
    assert pavement_edges


def test_geojson_contract_shape() -> None:
    gj = json.load(open(ROOT_JSON.replace(".json", ".geojson")))
    assert set(gj["meta"]) == {"center", "bounds"}
    kinds = {f["properties"]["kind"] for f in gj["features"]}
    assert {"taxiway", "taxilane", "runway", "runway_pavement", "apron", "stand", "hold", "crossing"} <= kinds
    for f in gj["features"]:
        if f["properties"]["kind"] in ("taxiway", "taxilane", "runway"):
            assert f["geometry"]["type"] == "LineString" and "ref" in f["properties"] and "id" in f["properties"]
        if f["properties"]["kind"] == "stand":
            assert "name" in f["properties"]
        if f["properties"]["kind"] == "hold":
            assert "runway" in f["properties"]


def test_south_subset_keeps_two_runways_and_stays_connected(graph: AirportGraph) -> None:
    sub = subset_graph(graph.data, "south")
    assert {r["id"] for r in sub["runways"]} == {"07L/25R", "07R/25L"}
    g = nx.Graph()
    for e in sub["edges"]:
        g.add_edge(e["from"], e["to"])
    assert nx.number_connected_components(g) == 1
    assert sub["stands"]
