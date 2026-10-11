"""The pages' /robot-api/* and /robot-ws/* forwarded to the robot process (8011): the browser opens only 8010."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from loguru import logger

from config.settings import RobotServerConfig, use_utf8_output
from util.proxy_helper import ProxyHelper

METHODS = ["GET", "POST", "PUT", "DELETE"]


router = APIRouter()
robot = ProxyHelper(RobotServerConfig.URL)

@router.api_route("/robot-api/{path:path}", methods=METHODS)
async def robot_api(request: Request, path: str):
    """/robot-api/api/status -> 8011 /api/status; /robot-api/stream/head -> 8011 /stream/head (MJPEG)."""
    response = await robot.http(request, path)
    return response

@router.websocket("/robot-ws/{path:path}")
async def robot_ws(websocket: WebSocket, path: str):
    """/robot-ws/teleop -> 8011 /ws/teleop."""
    await robot.websocket(websocket, f"ws/{path}")


def demo_robot_proxy_api() -> None:
    """Needs the robot process: one read, and the teleop WebSocket's first state."""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    logger.info("电机后台：{}", client.get("/robot-api/api/status").json()["torque"])
    with client.websocket_connect("/robot-ws/teleop") as websocket:
        logger.info("WebSocket：控制循环 {}", websocket.receive_json()["loop_alive"])


def main() -> None:
    use_utf8_output()
    demo_robot_proxy_api()


if __name__ == "__main__":
    main()
