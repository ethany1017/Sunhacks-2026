import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .runner import Runner
from .ws import router as ws_router

runner = None


@asynccontextmanager
async def lifespan(app):
    global runner
    task = None
    if os.environ.get("AUTOSTART", "1") != "0":  # default: start the first level with the Agent, no dashboard needed
        runner = Runner()
        runner.start(runner.sim.get_levels()[0]["id"], "agent")
        task = asyncio.create_task(runner.loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Tower Zero", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ws_router)


class RunRequest(BaseModel):
    level: str
    controller: str
    seed: int | None = None


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
