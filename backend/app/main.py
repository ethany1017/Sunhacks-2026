from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import airport, db
from .ws import router as ws_router

AIRPORT = airport.load()  # fail loudly at startup if the airport files are invalid

app = FastAPI(title="Tower Zero")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ws_router)


@app.get("/health")
def health():
    run_id = db.current_run_id()
    return {"status": "ok", "run_id": run_id, "events": len(db.events_for_run(run_id)) if run_id is not None else 0}


@app.get("/api/airport")
def get_airport():
    return AIRPORT
