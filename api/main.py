"""FastAPI app: REST + WebSocket per docs/API_CONTRACT.md. The simulation runs in a background task."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import ControlRequest, ControlResponse, Decision, Health
from api.ws import Hub, Runner
from backend.voice import tts
from sim.core.engine import Simulation

ROOT = Path(__file__).resolve().parents[1]
GRAPH_GEOJSON = ROOT / "data/processed/kphx_graph.geojson"
DEFAULT_SCENARIO = ROOT / "sim/scenarios/kphx_haboob.json"
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win. Never prints values."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v


def make_simulation() -> Simulation:
    load_dotenv()
    scenario = Path(os.environ.get("SCENARIO", str(DEFAULT_SCENARIO)))
    run_dir = Path(os.environ.get("RUNS_DIR", str(ROOT / "runs"))) / ("%s-%s" % (scenario.stem, time.strftime("%Y%m%d-%H%M%S")))
    return Simulation(scenario, run_dir=run_dir, controller="tower")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    sim = make_simulation()
    app.state.sim = sim
    app.state.hub = Hub()
    app.state.runner = Runner(sim, app.state.hub)
    app.state.graph = json.loads(GRAPH_GEOJSON.read_text())
    app.state.runner.start()
    yield
    await app.state.runner.stop()


app = FastAPI(title="KPHX airport simulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Voice", "X-Voice-Source"])


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health()


@app.get("/graph")
def graph() -> Dict[str, Any]:
    return app.state.graph  # type: ignore[no-any-return]


@app.get("/state")
def state() -> Dict[str, Any]:
    return app.state.sim.snapshot(mark_sent=False)  # type: ignore[no-any-return]


@app.post("/control", response_model=ControlResponse)
async def control(req: ControlRequest) -> Dict[str, Any]:
    # async on purpose: runs on the event loop, the same thread that steps the simulation, so a control action
    # can never land in the middle of a tick.
    res = app.state.sim.control(req.action, req.value)
    if req.action == "reset" and res["ok"]:
        app.state.hub.broadcast(app.state.sim.snapshot())
    elif res["ok"]:
        app.state.runner.publish()
    return res  # type: ignore[no-any-return]


@app.get("/decisions/{decision_id}", response_model=Decision)
def decision(decision_id: str) -> Dict[str, Any]:
    d = app.state.sim.decisions.get(decision_id)
    if d is None:
        raise HTTPException(status_code=404, detail="unknown decision %r" % decision_id)
    return d  # type: ignore[no-any-return]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await app.state.hub.serve(ws, app.state.sim)


def _caster(sim: Simulation) -> tts.Caster:
    """One pilot-voice casting per run; a reset starts a new run id and a fresh casting."""
    casters = getattr(app.state, "casters", None)
    if casters is None:
        casters = app.state.casters = {}
    run = sim.log.run_id
    if run not in casters:
        casters.clear()
        casters[run] = tts.Caster()
    return casters[run]  # type: ignore[no-any-return]


@app.get("/voice/{line_id}")
async def voice_clip(line_id: str) -> Response:
    """Audio for one radio line. 204 = deliberately silent (tower lines while an AI agent controls the airport)."""
    sim = app.state.sim
    line = next((v for v in reversed(sim.voice) if v.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="unknown voice line %r" % line_id)
    if line.speaker != "pilot" and not tts.tower_voiced(sim.controller_name):
        return Response(status_code=204)
    order = [a.callsign for a in sorted(sim.aircraft.values(), key=lambda a: a.seq) if a.kind != "vehicle"]
    caster = _caster(sim)
    role = caster.role_for(line.speaker, line.callsign, line.text, order.index(line.callsign) if line.callsign in order else None)
    path, how = await asyncio.to_thread(tts.synthesize, caster.cast, role, line.text)
    if path is None:
        raise HTTPException(status_code=503, detail="no audio: %s" % how)
    media = "audio/mpeg" if path.suffix == ".mp3" else "audio/mp4"
    return FileResponse(path, media_type=media, headers={"X-Voice": role["name"], "X-Voice-Source": how.split(":")[0],
                                                         "Cache-Control": "no-store"})

