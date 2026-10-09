"""服务入口：抓取后台的 FastAPI（页面在 xlerobot-ui），viser 3D 视图在同一进程。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import uvicorn
from fastapi import FastAPI
from loguru import logger

from app.api_grasp.grasp_execution_api import create_grasp_execution_api
from app.api_grasp.grasp_generation_api import create_grasp_generation_api
from app.api_grasp.real_grasp_api import create_real_grasp_api
from app.api_grasp.robot_operation_api import create_robot_operation_api
from app.context import grasp_context
from config.settings import PathConfig, ServerConfig, use_utf8_output
from util.log_helper import setup_logger


def create_app() -> FastAPI:
    # No pages here: xlerobot-ui serves them (app/ui_grasp, the simulation's page, is kept but not mounted).
    app = FastAPI(title="XLeRobot Grasp")
    app.include_router(create_robot_operation_api())
    app.include_router(create_grasp_generation_api())
    app.include_router(create_grasp_execution_api())
    app.include_router(create_real_grasp_api())
    return app


@logger.catch(reraise=True)
def main() -> None:
    use_utf8_output()
    setup_logger(PathConfig.LOG_DIR, ServerConfig.LOG_LEVEL)
    grasp_context.start()
    logger.info("XLeRobot 抓取后台：http://{}:{}（3D 视图端口 {}）", ServerConfig.HOST, ServerConfig.PORT, grasp_context.viewer_port)
    # log_config=None: uvicorn's records go through the loguru intercept set up above.
    uvicorn.run(create_app(), host=ServerConfig.HOST, port=ServerConfig.PORT, log_config=None)


if __name__ == "__main__":
    main()
