"""机器人进程的入口（8011）：电机与相机的 FastAPI（API、WebSocket、相机画面）。
由 app/main.py 拉起；页面在 app/ui，经 8010 转发过来。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import uvicorn
from fastapi import FastAPI
from loguru import logger

from app.api.errors import register_error_handlers
from app.robot_api import camera_api, follow_api, robot_api, teleop_ws_api
from app.robot_context import robot_context
from config.settings import PathConfig, RobotServerConfig, use_utf8_output
from util.log_helper import setup_logger


def create_app() -> FastAPI:
    app = FastAPI(title="XLeRobot Teleop")
    for module in (robot_api, teleop_ws_api, camera_api, follow_api):
        app.include_router(module.router)
    register_error_handlers(app)
    return app


def demo_app() -> None:
    """The robot process's routes, without connecting to the robot (nothing moves)."""
    for path, methods in create_app().openapi()["paths"].items():
        logger.info("{} {}", ",".join(method.upper() for method in methods), path)


@logger.catch(reraise=True)
def main() -> None:
    use_utf8_output()
    if "--demo" in sys.argv:
        demo_app()
        return
    setup_logger(PathConfig.LOG_DIR, RobotServerConfig.LOG_LEVEL, name="robot")
    robot_context.start()
    logger.info("XLeRobot 电机与相机后台：http://{}:{}", RobotServerConfig.HOST, RobotServerConfig.PORT)
    try:
        # log_config=None: uvicorn's records go through the loguru intercept set up above. The camera streams never
        # end on their own, so a graceful shutdown would wait for every open page to go away.
        uvicorn.run(create_app(), host=RobotServerConfig.HOST, port=RobotServerConfig.PORT, log_config=None,
                    timeout_graceful_shutdown=2)
    finally:
        robot_context.shutdown()


if __name__ == "__main__":
    main()
