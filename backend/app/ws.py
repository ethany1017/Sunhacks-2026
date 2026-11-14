"""WebSocket /ws: replay the current run, then stream new events by polling the DB."""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import db

router = APIRouter()
POLL_S = 0.2


async def _wait_for_disconnect(ws):
    # Read-only dashboard: drain and ignore client messages until it goes away.
    while (await ws.receive())["type"] != "websocket.disconnect":
        pass


@router.websocket("/ws")
async def events_ws(ws: WebSocket):
    await ws.accept()
    gone = asyncio.create_task(_wait_for_disconnect(ws))
    try:
        run_id = await asyncio.to_thread(db.current_run_id)
        batch = await asyncio.to_thread(db.events_for_run, run_id) if run_id is not None else []
        last = 0
        while not gone.done():
            for event in batch:
                await ws.send_text(json.dumps(event))
                last = event["seq"]
            await asyncio.wait([gone], timeout=POLL_S)
            if not gone.done():
                batch = await asyncio.to_thread(db.events_after, last)
    except (WebSocketDisconnect, RuntimeError):
        pass  # client went away mid-send
    finally:
        gone.cancel()
