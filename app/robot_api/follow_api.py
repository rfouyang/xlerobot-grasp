"""跟随接口：外部规划（抓取服务的 MoveIt 轨迹）逐点流式下发手臂关节目标。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from loguru import logger

from app.robot_context import robot_context
from config.settings import use_utf8_output


def create_follow_api() -> APIRouter:
    router = APIRouter()

    @router.post("/api/follow/arm")
    def arm():
        """Following is blocked after every stop until this is called (the planner does, before each step)."""
        response = robot_context.arm_follow()
        return response

    @router.websocket("/ws/follow")
    async def follow_stream(websocket: WebSocket):
        """In: {"part": "right_arm", "joints": {shoulder_pan: deg, ..., gripper: 0-100}}; out after each one:
        {accepted, blocked, torque, present}. The part stops following when the socket closes."""
        await websocket.accept()
        parts = set()
        try:
            while True:
                message = await websocket.receive_json()
                parts.add(message["part"])
                await websocket.send_json(robot_context.follow(message["part"], message["joints"]))
        except WebSocketDisconnect:
            # The planner finished or went away; the arm holds where it is.
            pass
        finally:
            for part in parts:
                robot_context.end_follow(part)

    return router


def demo_follow_api() -> None:
    """Real robot, no torque: a follow request is refused (no torque), arming works."""
    robot_context.start()
    app = FastAPI()
    app.include_router(create_follow_api())
    client = TestClient(app)
    logger.info("/api/follow/arm：{}", client.post("/api/follow/arm").json())
    with client.websocket_connect("/ws/follow") as websocket:
        present = robot_context.status()["joints"]["right_arm"]
        websocket.send_json({"part": "right_arm", "joints": {joint: values[0] for joint, values in present.items()}})
        logger.info("无力矩时跟随：{}", websocket.receive_json())
    robot_context.shutdown()


def main() -> None:
    use_utf8_output()
    demo_follow_api()


if __name__ == "__main__":
    main()
