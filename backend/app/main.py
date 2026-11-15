import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from atc import voice_out, weather_live

from . import db
from .runner import Runner
from .ws import router as ws_router

runner = None
live_wx = {"wx": {"source": "unavailable"}}  # real-world METAR for the top bar; display only, never read by the agent


@asynccontextmanager
async def lifespan(app):
    global runner
    tasks = []
    if os.environ.get("AUTOSTART", "1") != "0":  # default: start the first level with the Agent, no dashboard needed
        voice = voice_out.Voice() if os.environ.get("VOICE", "1") != "0" else None  # speech: live server only
        runner = Runner(voice=voice)
        runner.start(runner.sim.get_levels()[0]["id"], "agent")
        tasks.append(asyncio.create_task(runner.loop()))
        tasks.append(asyncio.create_task(weather_live.refresh_forever(runner.sim.get_airport()["config"]["icao"], live_wx)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Tower Zero", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ws_router)
voice_out.CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/audio", StaticFiles(directory=voice_out.CACHE_DIR), name="audio")  # under /api: the dev proxy forwards it


class RunRequest(BaseModel):
    level: str
    controller: str
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


class SwitchRequest(BaseModel):
    controller: str


def _runner():
    if runner is None:
        raise HTTPException(503, "run loop not started (AUTOSTART=0)")
    return runner


@app.get("/health")
def health():
    run_id = db.current_run_id()
    return {"status": "ok", "run_id": run_id, "events": len(db.events_for_run(run_id)) if run_id is not None else 0}


# The dashboard's only actions: list options, start/reset a run, switch controller. No clearances, no traffic.
# Handlers are async so they run on the loop thread, between sim ticks.
@app.get("/api/options")
async def options():
    return _runner().options()


@app.post("/api/run")
async def start_run(req: RunRequest):
    r = _runner()
    try:
        r.start(req.level, req.controller, req.seed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return r.current()


@app.post("/api/controller")
async def switch_controller(req: SwitchRequest):
    r = _runner()
    try:
        r.switch(req.controller)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return r.current()


@app.get("/api/weather/live")
async def live_weather():
    """The live NOAA METAR (source "live", "cache" or "unavailable"). Display only."""
    return live_wx["wx"]
