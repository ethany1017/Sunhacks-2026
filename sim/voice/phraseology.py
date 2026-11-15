"""FAA phraseology templates (JO 7110.65 2-4, 3-7, 3-9, 3-10; AIM 4-2 pronunciation) for spoken clearances.

Digits use the AIM pronunciations (tree, fower, fife, niner). Airline flight numbers use group form
("Southwest twelve thirty-four"). Output is plain text meant to be spoken by browser text-to-speech.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

DIGIT_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "tree", "4": "fower", "5": "fife", "6": "six",
               "7": "seven", "8": "eight", "9": "niner"}
PHONETIC = {c: w for c, w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima Mike November Oscar Papa "
    "Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu").split())}
TELEPHONY = {"SWA": "Southwest", "AAL": "American", "SKW": "Sky West", "ENY": "Envoy", "MQ": "Envoy", "DAL": "Delta",
             "UAL": "United", "FFT": "Frontier", "ASA": "Alaska"}
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
SIDES = {"L": "left", "R": "right", "C": "center"}
FACILITY = "Phoenix"
FREQ = {"ground_north": "119.75", "ground_south": "132.55", "tower_08_26": "118.7", "tower_south": "120.9"}


def digits(s: str) -> str:
    return " ".join(DIGIT_WORDS.get(c, c) for c in s if c.strip())


ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def two_digit(n: int) -> str:
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")


def group_number(num: str) -> str:
    """Group form for flight numbers: 1234 -> twelve thirty-four, 105 -> one oh five, 7 -> seven."""
    if len(num) <= 2:
        return two_digit(int(num)) if len(num) == 2 else ONES[int(num)]
    head, tail = (num[:-2], num[-2:]) if len(num) == 4 else (num[0], num[1:])
    head_w = two_digit(int(head))
    tail_w = ("oh " + ONES[int(tail[1])]) if tail[0] == "0" and tail != "00" else ("hundred" if tail == "00" else two_digit(int(tail)))
    return "%s %s" % (head_w, tail_w)


def spoken_callsign(callsign: str) -> str:
    m = re.match(r"^([A-Z]{3}|MQ)(\d+)$", callsign)
    if m and m.group(1) in TELEPHONY:
        return "%s %s" % (TELEPHONY[m.group(1)], group_number(m.group(2)))
    if callsign.startswith("N"):  # general aviation tail number
        return "November " + " ".join(PHONETIC[c] if c.isalpha() else DIGIT_WORDS[c] for c in callsign[1:] if c.isalnum())
    return callsign


def spoken_runway(end: str) -> str:
    m = re.match(r"^(\d+)([LRC]?)$", end)
    if not m:
        return end
    num = str(int(m.group(1)))
    return digits(num) + (" " + SIDES[m.group(2)] if m.group(2) else "")


def spoken_taxiway(ref: str) -> str:
    return " ".join(PHONETIC[c] if c.isalpha() else DIGIT_WORDS[c] for c in ref if c.isalnum())


def spoken_frequency(freq: str) -> str:
    return " point ".join(digits(p) for p in freq.split("."))


def spoken_wind(wind: Optional[Dict[str, Any]]) -> str:
    if not wind or not wind.get("wind_kt"):
        return ""
    gust = " gusting %s" % digits(str(int(wind["gust_kt"]))) if wind.get("gust_kt") else ""
    return "wind %s at %s%s, " % (digits("%03d" % int(wind.get("wind_dir") or 0)), digits(str(int(wind["wind_kt"]))), gust)


def taxi_route(letters: List[str]) -> str:
    return ", ".join(spoken_taxiway(l) for l in letters)


def clearance_text(action: str, callsign: str, params: Dict[str, Any], wind: Optional[Dict[str, Any]] = None) -> str:
    """The controller's spoken transmission for a clearance action."""
    cs = spoken_callsign(callsign)
    rwy = spoken_runway(params["runway"]) if params.get("runway") else ""
    if action == "pushback_approved":
        return "%s, %s ground, pushback approved" % (cs, FACILITY)
    if action == "taxi_via":
        parts = []
        if params.get("departure_runway"):
            parts.append("runway %s" % spoken_runway(params["departure_runway"]))
        via = params.get("taxiways") or []
        parts.append("taxi via %s" % taxi_route(via) if via else "taxi")
        if params.get("gate"):
            parts.append("to gate %s" % spoken_taxiway(params["gate"]))
        for rw in params.get("cross", []):
            parts.append("cross runway %s" % spoken_runway(rw))
        for rw in params.get("hold_short", []):
            parts.append("hold short of runway %s" % spoken_runway(rw))
        return "%s, %s" % (cs, ", ".join(parts))
    if action == "hold_short":
        return "%s, hold short of runway %s" % (cs, rwy)
    if action == "cross_runway":
        at = " at %s" % spoken_taxiway(params["at_taxiway"]) if params.get("at_taxiway") else ""
        return "%s, cross runway %s%s" % (cs, rwy, at)
    if action == "line_up_and_wait":
        return "%s, runway %s, line up and wait" % (cs, rwy)
    if action == "cleared_for_takeoff":
        return "%s, %srunway %s, cleared for takeoff" % (cs, spoken_wind(wind), rwy)
    if action == "cleared_to_land":
        return "%s, %srunway %s, cleared to land" % (cs, spoken_wind(wind), rwy)
    if action == "go_around":
        return "%s, go around" % cs
    if action == "hold_position":
        return "%s, hold position" % cs
    if action == "resume_taxi":
        return "%s, resume taxi" % cs
    if action == "contact_tower_or_ground":
        facility = params.get("facility", "ground")
        freq = params.get("frequency") or (FREQ["ground_south"] if facility == "ground" else FREQ["tower_south"])
        expect = ", expect runway %s" % rwy if rwy else ""
        return "%s%s, contact %s %s" % (cs, expect, facility, spoken_frequency(freq))
    raise ValueError("unknown action %s" % action)


def correction_text(callsign: str, clearance_text_: str) -> str:
    """Controller asks again after a wrong readback (7110.65 3-7-2: request a read back)."""
    return "%s, negative, I say again: %s" % (spoken_callsign(callsign), clearance_text_.split(", ", 1)[-1])
