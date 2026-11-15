"""Decision records: written when the decision is made, never reconstructed afterwards."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def make_decision(decision_id: str, t: float, snapshot: Dict[str, Any], **fields: Any) -> Dict[str, Any]:
    """Build the contract Decision plus snapshot summary (with data ages) and linked event ids."""
    alternatives: List[Dict[str, str]] = [dict(a) for a in fields.get("alternatives", [])]
    return {
        "id": decision_id, "t": round(t, 1), "callsign": fields.get("callsign"), "action": fields.get("action", ""),
        "summary": fields.get("summary", ""), "facts": list(fields.get("facts", [])), "rules": list(fields.get("rules", [])),
        "alternatives": alternatives, "next_review_s": fields.get("next_review_s"),
        "voice_id": fields.get("voice_id"), "linked_event_ids": list(fields.get("linked_event_ids", [])),
        "snapshot": snapshot,
    }


def snapshot_summary(t: float, observation: Optional[Dict[str, Any]], weather: Dict[str, Any]) -> Dict[str, Any]:
    """What the controller could see: counts, the age of the picture and the weather report in force."""
    aircraft = observation["aircraft"] if observation else []
    return {"t": round(t, 1), "aircraft_seen": len(aircraft), "data_age_s": observation["data_age_s"] if observation else None,
            "weather_vis_sm": weather.get("vis_sm"), "weather_age_s": round(t - weather.get("t", 0.0), 1)}
