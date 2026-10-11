"""遥操作 WebSocket：页面发按住的键（变化时 + 心跳），服务推机器人状态。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from loguru import logger

from app.robot_context import robot_context
from config.settings import RobotServerConfig, use_utf8_output

router = APIRouter()

@router.websocket("/ws/teleop")
async def teleop_stream(websocket: WebSocket):
    """Keys in ({"keys": [KeyboardEvent.code, ...]}), state out. When the page goes away the keys are
    released at once; the control loop's watchdog covers a page that stalls without closing."""
    await websocket.accept()
    # Each page keeps its own held keys; the control loop uses the union of all pages.
    client = f"ws-{id(websocket)}"

    async def receive_keys():
        while True:
            message = await websocket.receive_json()
            robot_context.set_keys(message["keys"], client)

    receiver = asyncio.create_task(receive_keys())
    try:
        while not receiver.done():
            await websocket.send_json(robot_context.status())
            await asyncio.sleep(RobotServerConfig.STATE_PUSH_INTERVAL_S)
    except WebSocketDisconnect:
        # The page was closed or reloaded.
        pass
    finally:
        receiver.cancel()
        robot_context.drop_client(client)


def demo_teleop_ws_api() -> None:
    """Real robot, no torque: send a held key, read the state back."""
    robot_context.start()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with client.websocket_connect("/ws/teleop") as websocket:
        websocket.send_json({"keys": ["KeyW", "ArrowUp"]})
        state = websocket.receive_json()
        state = websocket.receive_json()
        logger.info("推送：按键 {}，力矩 {}，底盘指令 {}", state["pressed"], state["torque"], state["base_command"])
    robot_context.shutdown()


def main() -> None:
    use_utf8_output()
    demo_teleop_ws_api()


if __name__ == "__main__":
    main()
