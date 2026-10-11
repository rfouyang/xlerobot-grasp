"""系统设置页的接口：自检（只读）、重启机器人进程，以及两个腕部相机的左右匹配（查看、交换、确认）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter
from loguru import logger

from app.context import grasp_context
from config.settings import use_utf8_output

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/check")
def check() -> dict:
    """The five stages, read-only (no torque, no motion); ~0.5 s. Plain def: a worker thread."""
    result = grasp_context.system_check()
    return result


@router.post("/restart_robot")
def restart_robot() -> dict:
    result = grasp_context.restart_robot()
    return result


@router.get("/devices")
def devices() -> dict:
    result = grasp_context.devices()
    return result


@router.post("/devices/swap")
def devices_swap() -> dict:
    result = grasp_context.swap_devices()
    return result


@router.post("/devices/confirm")
def devices_confirm() -> dict:
    result = grasp_context.confirm_devices()
    return result


def demo_system_api() -> None:
    """Needs the robot process (read-only): the self check."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from component.process_supervision.service import Supervisor

    grasp_context.supervisor = Supervisor()
    app = FastAPI()
    app.include_router(router)
    report = TestClient(app).get("/api/system/check").json()
    logger.info("就绪：{}，需处理 {}，提醒 {}", report["ready"], report["problems"], report["warnings"])


def main() -> None:
    use_utf8_output()
    demo_system_api()


if __name__ == "__main__":
    main()
