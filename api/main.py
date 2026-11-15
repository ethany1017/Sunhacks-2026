"""FastAPI app: REST + WebSocket per docs/API_CONTRACT.md. The simulation runs in a background task."""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import ControlRequest, ControlResponse, Decision, Health
from api.ws import Hub, Runner
from sim.core.engine import Simulation

ROOT = Path(__file__).resolve().parents[1]
GRAPH_GEOJSON = ROOT / "data/processed/kphx_graph.geojson"
DEFAULT_SCENARIO = ROOT / "sim/scenarios/kphx_busy.json"
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
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


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
