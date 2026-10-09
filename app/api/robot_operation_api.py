"""机器人操作接口：关节状态（含 WebSocket 推送）、命名位姿。"""

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
from pydantic import BaseModel

from app.context import grasp_context
from config.settings import ServerConfig, use_utf8_output


class MoveRequest(BaseModel):
    arm: str
    part: str
    pose: str


def create_robot_operation_api() -> APIRouter:
    router = APIRouter()

    @router.get("/api/robot/state")
    def state():
        response = {"joints": grasp_context.joint_state()}
        return response

    @router.get("/api/robot/poses")
    def poses():
        response = grasp_context.robot_poses()
        return response

    @router.post("/api/robot/move")
    def move(request: MoveRequest):
        """Plain def: FastAPI runs it in a worker thread, so a slow motion never blocks the WebSocket."""
        response = {"moved": grasp_context.move_robot(request.arm, request.part, request.pose)}
        return response

    @router.websocket("/ws/robot/state")
    async def state_stream(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json({"joints": grasp_context.joint_state()})
                await asyncio.sleep(ServerConfig.STATE_PUSH_INTERVAL_S)
        except WebSocketDisconnect:
            # The page was closed or reloaded; nothing to clean up.
            pass

    return router


def demo_robot_operation_api() -> None:
    """Needs the bringup launch; starts the application (and its 3D view) in-process."""
    grasp_context.start()
    app = FastAPI()
    app.include_router(create_robot_operation_api())
    client = TestClient(app)
    logger.info("/api/robot/poses：{}", client.get("/api/robot/poses").json())
    logger.info("move ready：{}", client.post("/api/robot/move", json={"arm": "right", "part": "arm", "pose": "ready"}).json())
    with client.websocket_connect("/ws/robot/state") as websocket:
        joints = websocket.receive_json()["joints"]
    logger.info("WebSocket 推送：Pitch={:.3f} Elbow={:.3f}", joints["Pitch"], joints["Elbow"])
    client.post("/api/robot/move", json={"arm": "right", "part": "arm", "pose": "zero"})


def main() -> None:
    use_utf8_output()
    demo_robot_operation_api()


if __name__ == "__main__":
    main()
