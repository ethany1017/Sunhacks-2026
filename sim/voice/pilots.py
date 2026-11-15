"""Simulated pilots: generate readbacks with a configurable error rate (wrong runway side, dropped word)."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

import numpy as np

from sim.voice.phraseology import spoken_callsign, spoken_frequency, spoken_runway, spoken_taxiway, taxi_route

RUNWAY_ACTIONS = ("hold_short", "cross_runway", "line_up_and_wait", "cleared_for_takeoff", "cleared_to_land", "taxi_via")
SWAP = {"L": "R", "R": "L"}


def flip_side(end: str) -> Optional[str]:
    """25R -> 25L; runways without a side letter have nothing to confuse."""
    m = re.match(r"^(\d+)([LR])$", end)
    return m.group(1) + SWAP[m.group(2)] if m else None


def readback_text(action: str, callsign: str, params: Dict[str, Any], runway: Optional[str]) -> str:
    cs = spoken_callsign(callsign)
    rwy = spoken_runway(runway) if runway else ""
    if action == "pushback_approved":
        return "Pushback approved, %s" % cs
    if action == "taxi_via":
        parts = []
        if params.get("departure_runway"):
            parts.append("runway %s" % spoken_runway(runway or params["departure_runway"]))
        via = params.get("taxiways") or []
        parts.append("taxi via %s" % taxi_route(via) if via else "taxi")
        for hs in params.get("hold_short", []):
            parts.append("hold short of runway %s" % spoken_runway(runway or hs))
        return "%s, %s" % (", ".join(parts), cs)
    if action == "hold_short":
        return "Hold short of runway %s, %s" % (rwy, cs)
    if action == "cross_runway":
        at = " at %s" % spoken_taxiway(params["at_taxiway"]) if params.get("at_taxiway") else ""
        return "Cross runway %s%s, %s" % (rwy, at, cs)
    if action == "line_up_and_wait":
        return "Runway %s, line up and wait, %s" % (rwy, cs)
    if action == "cleared_for_takeoff":
        return "Runway %s, cleared for takeoff, %s" % (rwy, cs)
    if action == "cleared_to_land":
        return "Runway %s, cleared to land, %s" % (rwy, cs)
    if action == "go_around":
        return "Going around, %s" % cs
    if action == "hold_position":
        return "Holding position, %s" % cs
    if action == "resume_taxi":
        return "Resuming taxi, %s" % cs
    freq = params.get("frequency") or "132.55"
    return "Contact %s %s, %s" % (params.get("facility", "ground"), spoken_frequency(freq), cs)


def make_readback(rng: np.random.Generator, error_rate: float, action: str, callsign: str, params: Dict[str, Any],
                  runway: Optional[str], retry: bool) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """Return (text, parsed fields the controller can check, error kind or None). Retries are always correct."""
    error: Optional[str] = None
    heard = runway
    if not retry and action in RUNWAY_ACTIONS and runway and rng.random() < error_rate:
        flipped = flip_side(runway)
        if flipped and rng.random() < 0.7:
            error, heard = "wrong_runway_side", flipped
        else:
            error, heard = "dropped_word", None
    if error == "dropped_word":
        text = "Roger, %s" % spoken_callsign(callsign)
    else:
        text = readback_text(action, callsign, params, heard)
    return text, {"action": action, "runway": heard}, error
