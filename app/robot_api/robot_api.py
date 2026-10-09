"""机器人接口：页面配置、状态、按部件上/下力矩（下 = 先回休息位）、停止、回休息位、记录休息位。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import BaseModel

from app.robot_context import robot_context
from config.robot import RestPoseConfig, RobotConfig
from config.settings import use_utf8_output


class TorqueRequest(BaseModel):
    part: str
    on: bool


class PartRequest(BaseModel):
    part: str


class PoseRequest(BaseModel):
    name: str


class OverlayRequest(BaseModel):
    source: str
    items: list[dict]


def check_part(part: str, allowed=tuple(RobotConfig.PARTS)) -> None:
    if part not in allowed:
        raise HTTPException(status_code=404, detail=f"没有部件 {part}")


def create_robot_api() -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    def config():
        response = robot_context.page_config()
        return response

    @router.get("/api/status")
    def status():
        response = robot_context.status()
        return response

    @router.post("/api/torque")
    def torque(request: TorqueRequest):
        """part "all" is the page's one switch; a single part is still possible from scripts."""
        if request.part == "all":
            response = robot_context.set_torque_all(request.on)
        else:
            check_part(request.part)
            response = robot_context.set_torque(request.part, request.on)
        return response

    @router.post("/api/overlay")
    def overlay(request: OverlayRequest):
        """Detections to draw on the head view (pixel coordinates of the head image)."""
        response = robot_context.set_overlay(request.source, request.items)
        return response

    @router.post("/api/stop")
    def stop():
        response = robot_context.stop()
        return response

    @router.post("/api/rest")
    def rest(request: PartRequest):
        """To the rest pose, torque stays on."""
        check_part(request.part, ("left_arm", "right_arm", "head"))
        response = robot_context.go_rest(request.part)
        return response

    @router.post("/api/park")
    def park():
        """Every powered arm and the head to the rest pose, then their torque off; the base off. Blocks until done
        (plain def: it runs in a worker thread)."""
        response = robot_context.park(["left_arm", "right_arm", "head"])
        robot_context.set_torque("base", False)
        return response

    @router.post("/api/pose")
    def pose(request: PoseRequest):
        """To a saved pose (e.g. grasp_start); only parts with torque move."""
        if not (RestPoseConfig.FILE.parent / f"{request.name}_pose.json").is_file():
            raise HTTPException(status_code=404, detail=f"没有位姿 {request.name}")
        response = robot_context.go_to_pose(request.name)
        return response

    @router.post("/api/rest_pose")
    def record_rest_pose():
        response = robot_context.record_rest_pose()
        return response

    return router


def demo_robot_api() -> None:
    """Real robot, no torque (nothing moves): config, status, stop, an unknown part, rest and park with no torque."""
    robot_context.start()
    app = FastAPI()
    app.include_router(create_robot_api())
    client = TestClient(app)
    logger.info("/api/config 按键组：{}", list(client.get("/api/config").json()["keymap"]))
    logger.info("/api/status 力矩：{}", client.get("/api/status").json()["torque"])
    logger.info("/api/stop：{}", client.post("/api/stop").json())
    logger.info("/api/torque 未知部件：{}", client.post("/api/torque", json={"part": "tail", "on": True}).status_code)
    logger.info("/api/rest 未上力矩：{}", client.post("/api/rest", json={"part": "left_arm"}).json())
    logger.info("/api/park 都没力矩：{}", client.post("/api/park").json())
    robot_context.shutdown()


def main() -> None:
    use_utf8_output()
    demo_robot_api()


if __name__ == "__main__":
    main()
