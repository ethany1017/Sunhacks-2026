"""SQLite event store. The DB is the single source of truth; writers may be other processes."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
EVENT_TYPES = {
    "airport", "sim_start", "sim_end", "weather", "runway_change", "aircraft_spawn", "position", "proposal",
    "ai_decision", "safety_check", "clearance", "audio", "readback", "readback_correction",
    "sequence", "alert",
}
SCHEMA = """CREATE TABLE IF NOT EXISTS events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, t REAL NOT NULL,
    type TEXT NOT NULL, callsign TEXT, data TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '')"""


def db_path():
    return BACKEND_DIR / os.environ.get("EVENTS_DB", "events.db")  # absolute EVENTS_DB overrides


@contextmanager
def _conn():
    # ponytail: one short-lived connection per call; fine for a demo, pool it if it ever shows up in profiles.
    c = sqlite3.connect(db_path(), timeout=5)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute(SCHEMA)
        with c:  # commits on success
            yield c
    finally:
        c.close()


def _row(r):
    return {"t": r[0], "seq": r[1], "type": r[2], "callsign": r[3], "data": json.loads(r[4]), "reason": r[5]}


def _select(where, args):
    with _conn() as c:
        rows = c.execute(f"SELECT t, seq, type, callsign, data, reason FROM events WHERE {where} ORDER BY seq", args)
        return [_row(r) for r in rows]


def start_run():
    with _conn() as c:
        return (c.execute("SELECT MAX(run_id) FROM events").fetchone()[0] or 0) + 1


def emit(run_id, type, t, callsign=None, data=None, reason=""):
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")
    data = data or {}
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO events(run_id, t, type, callsign, data, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, t, type, callsign, json.dumps(data), reason),
        )
        seq = cur.lastrowid
    return {"t": t, "seq": seq, "type": type, "callsign": callsign, "data": data, "reason": reason}


def emit_many(run_id, events):
    """Write events (dicts with t, type, callsign, data, reason) in one transaction."""
    for e in events:
        if e["type"] not in EVENT_TYPES:
            raise ValueError(f"unknown event type {e['type']!r}")
    with _conn() as c:
        c.executemany(
            "INSERT INTO events(run_id, t, type, callsign, data, reason) VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, e["t"], e["type"], e["callsign"], json.dumps(e["data"]), e["reason"]) for e in events],
        )


def current_run_id():
    with _conn() as c:
        return c.execute("SELECT MAX(run_id) FROM events").fetchone()[0]


def events_for_run(run_id):
    return _select("run_id = ?", (run_id,))


def events_after(seq):
    return _select("seq > ?", (seq,))
