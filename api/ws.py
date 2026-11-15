"""WebSocket hub: one snapshot on connect, then a delta about every 100 ms, fed by the simulation runner."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from sim.core.clock import TICK_S
from sim.core.engine import Simulation

QUEUE_LIMIT = 300


class Hub:
    def __init__(self) -> None:
        self.clients: Dict[WebSocket, "asyncio.Queue[Dict[str, Any]]"] = {}

    def broadcast(self, msg: Dict[str, Any]) -> None:
        for ws, q in list(self.clients.items()):
            if q.qsize() >= QUEUE_LIMIT:  # slow consumer: drop it, it will reconnect and get a fresh snapshot
                del self.clients[ws]
                continue
            q.put_nowait(msg)

    async def serve(self, ws: WebSocket, sim: Simulation) -> None:
        await ws.accept()
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        pending = sim.delta()  # flush changes so existing clients stay consistent with the snapshot below
        if len(pending) > 2:
            self.broadcast(pending)
        queue.put_nowait(sim.snapshot())
        self.clients[ws] = queue
        reader = asyncio.ensure_future(self._drain(ws))
        try:
            while not reader.done():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await ws.send_json(msg)
                if ws not in self.clients:
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            self.clients.pop(ws, None)

    @staticmethod
    async def _drain(ws: WebSocket) -> None:
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return


class Runner:
    """Advances the simulation in wall-clock time (speed x 10 ticks per second) and publishes deltas at 10 Hz."""

    def __init__(self, sim: Simulation, hub: Hub) -> None:
        self.sim, self.hub = sim, hub
        self.task: Optional["asyncio.Task[None]"] = None
        self.ticks_run = 0

    def start(self) -> None:
        self.task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.sim.close()

    def publish(self) -> None:
        self.hub.broadcast(self.sim.delta())

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        next_t = loop.time()
        while True:
            sim = self.sim
            service = getattr(sim.controller, "service", None)
            if service is not None:      # optional controller hook that runs even while the clock is paused
                service(sim)
            if not sim.clock.paused and not sim.finished:
                for _ in range(sim.clock.speed):
                    sim.step()
                    self.ticks_run += 1
                    if sim.finished:
                        break
            self.publish()
            next_t += TICK_S
            delay = next_t - loop.time()
            if delay < -1.0:  # fell far behind (debugger, suspend): do not try to catch up
                next_t = loop.time()
            await asyncio.sleep(max(0.0, delay))
