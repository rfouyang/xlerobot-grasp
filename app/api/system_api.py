"""系统设置页的接口：自检（只读）和重启机器人进程。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter
from loguru import logger

from component.system_check.self_check import SelfCheck
from component.system_check.supervisor import Supervisor
from config.settings import use_utf8_output


def create_system_api(supervisor: Supervisor) -> APIRouter:
    router = APIRouter()

    def reaches() -> dict | None:
        from app.context import grasp_context

        runner = getattr(grasp_context, "real", None)
        return runner.reaches if runner is not None else None

    @router.get("/api/system/check")
    def check():
        """The five stages, read-only (no torque, no motion); ~0.5 s. Plain def: a worker thread."""
        response = SelfCheck(supervisor=supervisor, reaches=reaches()).run()
        return response

    @router.post("/api/system/restart_robot")
    def restart_robot():
        """The robot process again: it takes over parts that still have torque, so an arm holding stays where it is."""
        logger.warning("页面要求重启机器人进程")
        response = {"ok": supervisor.restart_robot()}
        return response

    @router.get("/api/system/devices")
    def devices():
        from component.robot.devices import status

        response = status()
        return response

    @router.post("/api/system/devices/swap")
    def devices_swap():
        """Left and right wrist swapped, then the robot process again so the cameras open by the new roles."""
        from component.robot.devices import status, swap

        swap()
        restarted = supervisor.restart_robot()
        response = {"ok": restarted, **status()}
        return response

    @router.post("/api/system/devices/confirm")
    def devices_confirm():
        from component.robot.devices import confirm, status

        confirm()
        response = {"ok": True, **status()}
        return response

    return router


def demo_system_api() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_system_api(Supervisor()))
    report = TestClient(app).get("/api/system/check").json()
    logger.info("就绪：{}，需处理 {}，提醒 {}", report["ready"], report["problems"], report["warnings"])


def main() -> None:
    use_utf8_output()
    demo_system_api()


if __name__ == "__main__":
    main()
