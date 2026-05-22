from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .state import get_state

router = APIRouter()


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    state = get_state()
    await websocket.accept()
    await state.ws_connect(websocket)

    try:
        # Send catch-up batch of recent log lines
        recent = state.get_log_list()[-200:]
        await websocket.send_text(
            json.dumps({"type": "log_batch", "entries": [asdict(e) for e in recent]})
        )

        # Keep connection alive, respond to pings
        while True:
            try:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await state.ws_disconnect(websocket)
