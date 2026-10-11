"""机器人接口：页面配置、状态、按部件上/下力矩（下 = 先回休息位）、停止、回休息位、去 / 存位姿、按关节移动。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from app.robot_context import robot_context
from config.settings import use_utf8_output

router = APIRouter(prefix="/api", tags=["robot"])
# The parts that move to joint targets (the base drives by velocity only).
Jointed = Literal["left_arm", "right_arm", "head"]


class TorqueRequest(BaseModel):
    # "all": the page's one switch; a single part is still possible from scripts.
    part: Literal["all", "left_arm", "right_arm", "head", "base"]
    on: bool


class PartRequest(BaseModel):
    part: Jointed


class PoseRequest(BaseModel):
    name: str


class JointsRequest(BaseModel):
    part: Jointed
    joints: dict[str, float]


class OverlayRequest(BaseModel):
    source: str
    items: list[dict]


@router.get("/config")
def config() -> dict:
    result = robot_context.page_config()
    return result


@router.get("/status")
def status() -> dict:
    result = robot_context.status()
    return result


@router.post("/torque")
def torque(request: TorqueRequest) -> dict:
    """On only from the rest pose; off walks back to it first. The answer says what is on (and why not)."""
    result = (robot_context.set_torque_all(request.on) if request.part == "all"
              else robot_context.set_torque(request.part, request.on))
    return result


@router.post("/overlay")
def overlay(request: OverlayRequest) -> dict:
    """Detections to draw on the head view (pixel coordinates of the head image)."""
    result = robot_context.set_overlay(request.source, request.items)
    return result


@router.post("/stop")
def stop() -> dict:
    result = robot_context.stop()
    return result


@router.post("/rest")
def rest(request: PartRequest) -> dict:
    """To the rest pose, torque stays on."""
    result = robot_context.go_rest(request.part)
    return result


@router.post("/park")
def park() -> dict:
    """Every powered arm and the head to the rest pose, then their torque off; the base off. Blocks until done
    (plain def: it runs in a worker thread)."""
    result = robot_context.park_all()
    return result


@router.post("/pose")
def pose(request: PoseRequest) -> dict:
    """To a saved pose (e.g. grasp_start); only parts with torque move."""
    result = robot_context.go_to_pose(request.name)
    return result


@router.post("/rest_pose")
def record_rest_pose() -> dict:
    result = robot_context.record_rest_pose()
    return result


@router.post("/move_joints")
def move_joints(request: JointsRequest) -> dict:
    """One powered arm or the head to the joint targets set on the page (homing speed, calibrated range)."""
    result = robot_context.move_joints(request.part, request.joints)
    return result


@router.post("/save_pose")
def save_pose(request: PoseRequest) -> dict:
    """The current joints of the arms and head saved as the rest pose ("rest") or another dock ("grasp_start")."""
    result = robot_context.save_pose(request.name)
    return result


def demo_robot_api() -> None:
    """Real robot, no torque (nothing moves): config, status, stop, an unknown part, rest and park with no torque."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.errors import register_error_handlers

    robot_context.start()
    app = FastAPI()
    app.include_router(router)
    register_error_handlers(app)
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
