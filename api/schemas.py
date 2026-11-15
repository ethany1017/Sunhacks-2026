"""Pydantic models for the REST/WS contract (docs/API_CONTRACT.md)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "speed", "reset"]
    value: Any = None


class ControlResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    state: Optional[Dict[str, Any]] = None


class Health(BaseModel):
    ok: bool = True


class Hold(BaseModel):
    runway: str
    lon: float
    lat: float


class Aircraft(BaseModel):
    id: str
    callsign: str
    type: str
    kind: Literal["arrival", "departure", "vehicle"]
    airline: str
    lon: float
    lat: float
    heading_deg: float
    speed_mps: float
    phase: str
    status_text: str
    wingspan_m: float
    length_m: float
    gate: Optional[str]
    runway: Optional[str]
    route: List[List[float]]
    planned_route: List[List[float]]
    hold: Optional[Hold]
    eta_s: Optional[float]
    altitude_ft: int


class OccupancyWindow(BaseModel):
    callsign: str
    start_s: float
    end_s: float
    kind: Literal["occupied", "reserved"]


class Runway(BaseModel):
    id: str
    name: str
    status: Literal["free", "reserved", "occupied"]
    owner: Optional[str]
    occupancy: List[OccupancyWindow]


class Clearance(BaseModel):
    id: str
    callsign: str
    action: str
    text: str
    state: Literal["issued", "acknowledged", "complete", "amended", "cancelled"]
    issued_t: float
    ack_t: Optional[float]
    done_t: Optional[float]


class Alternative(BaseModel):
    option: str
    reason: str


class Decision(BaseModel):
    id: str
    t: float
    callsign: Optional[str]
    action: str
    summary: str
    facts: List[str]
    rules: List[str]
    alternatives: List[Alternative]
    next_review_s: Optional[float]
    voice_id: Optional[str]
    linked_event_ids: List[str] = []
    snapshot: Dict[str, Any] = {}


class VoiceLine(BaseModel):
    id: str
    t: float
    speaker: Literal["agent", "pilot"]
    callsign: str
    text: str
    status: Literal["ok", "corrected", "pending"]


class Weather(BaseModel):
    t: float
    metar: str
    wind_dir: Optional[int]
    wind_kt: Optional[int]
    gust_kt: Optional[int]
    vis_sm: Optional[float]
    wx: Optional[str]
    temp_f: Optional[float]


class Snapshot(BaseModel):
    type: Literal["snapshot"]
    t: float
    speed: int
    paused: bool
    agent: Dict[str, str]
    scenario: Dict[str, Any]
    weather: Weather
    aircraft: List[Aircraft]
    runways: List[Runway]
    clearances: List[Clearance]
    decisions: List[Decision]
    voice: List[VoiceLine]
    score: Dict[str, Any]
