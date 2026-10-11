"""唯一入口：python app/main.py。先把机器人进程（8011）和 ROS bringup 带起来（已在运行就复用），再在本进程里跑
页面、抓取 API（/grasp-api）和到机器人进程的转发（8010）。--stop-all：回停靠位、关力矩，停掉所有进程。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import os


def with_ros_environment() -> None:
    """MoveIt's Python modules come from ROS: started from a plain shell, this process runs itself again with ROS and
    the project's ros_ws sourced (and RMW_IMPLEMENTATION set), in the project's .venv, so `python app/main.py` (any
    python) is all it takes."""
    workspace = BASE_DIR / "ros_ws" / "install"
    # The project's own environment, whichever python started it (the system python3 lacks its packages).
    venv = BASE_DIR / ".venv" / "bin" / "python"
    python = venv if venv.exists() else Path(sys.executable)
    if (str(workspace) in os.environ.get("AMENT_PREFIX_PATH", "")
            and Path(sys.prefix).resolve() == (BASE_DIR / ".venv").resolve()):
        return
    command = (f"source /opt/ros/jazzy/setup.bash && source {workspace / 'setup.bash'} && "
               f"exec {python} {' '.join(map(repr, [str(Path(__file__).resolve()), *sys.argv[1:]]))}")
    os.execvpe("bash", ["bash", "-c", command], {**os.environ, "RMW_IMPLEMENTATION": "rmw_zenoh_cpp"})


if __name__ == "__main__":
    with_ros_environment()

import argparse

import uvicorn
from fastapi import FastAPI
from loguru import logger

from app.api import (
    calibration_api,
    grasp_execution_api,
    grasp_generation_api,
    planning_api,
    robot_operation_api,
    robot_proxy_api,
    system_api,
    task_api,
    voice_api,
)
from app.api.errors import register_error_handlers
from app.context import grasp_context
from app.ui import routes as ui_routes
from component.process_supervision.service import Supervisor
from config.settings import PathConfig, ServerConfig, use_utf8_output
from util.log_helper import setup_logger

# The pages call the grasp API under this prefix (it was another service before the merge).
GRASP_PREFIX = "/grasp-api"


def create_app() -> FastAPI:
    """The pages and every route of the page process; nothing starts here, so tests can drive the API with stand-ins."""
    app = FastAPI(title="XLeRobot")
    for router in (ui_routes.router, robot_proxy_api.router, system_api.router, calibration_api.router,
                   planning_api.router, voice_api.router):
        app.include_router(router)
    for router in (robot_operation_api.router, grasp_generation_api.router, grasp_execution_api.router,
                   task_api.router, task_api.depth_router, planning_api.grasp_router):
        app.include_router(router, prefix=GRASP_PREFIX)
    register_error_handlers(app)
    ui_routes.mount_ui_static(app)
    return app


def serve(app: FastAPI) -> None:
    """The app on HTTP (ServerConfig.PORT) and HTTPS (HTTPS_PORT, a self-signed certificate: the iPad's microphone).
    log_config=None: uvicorn's records go through the loguru intercept. The camera streams never end on their own, so
    a graceful shutdown would wait for every open page to go away."""
    import asyncio

    from util.tls_helper import ensure_certificate

    certificate, key = ensure_certificate()
    common = {"host": ServerConfig.HOST, "log_config": None, "timeout_graceful_shutdown": 2}
    servers = [uvicorn.Server(uvicorn.Config(app, port=ServerConfig.PORT, **common)),
               uvicorn.Server(uvicorn.Config(app, port=ServerConfig.HTTPS_PORT, ssl_certfile=str(certificate),
                                             ssl_keyfile=str(key), **common))]
    logger.info("HTTPS：https://<本机地址>:{}（自签名证书，第一次打开要选「继续访问」）", ServerConfig.HTTPS_PORT)

    async def both() -> None:
        await asyncio.gather(*(server.serve() for server in servers))

    asyncio.run(both())


def demo_app() -> None:
    """Every page and the API's reads answer; nothing starts (no robot process, no models, no MoveIt)."""
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    for url in ("/", "/grasp", "/pick-place", "/planning", "/calibration", "/settings", "/api/planning/report",
                "/api/calibration/results", "/static/ui/common.js"):
        logger.info("{} -> {}", url, client.get(url).status_code)


@logger.catch(reraise=True)
def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="XLeRobot：页面、抓取和机器人进程")
    parser.add_argument("--stop-all", action="store_true",
                        help="回停靠位并关力矩，停掉机器人进程和 ROS bringup，然后退出")
    parser.add_argument("--demo", action="store_true", help="只检查页面和接口能不能打开（不启动任何东西）")
    args = parser.parse_args()
    if args.demo:
        demo_app()
        return
    setup_logger(PathConfig.LOG_DIR, ServerConfig.LOG_LEVEL, name="grasp")
    supervisor = Supervisor()
    if args.stop_all:
        supervisor.stop_all()
        return
    supervisor.start()
    # It loads the models (and talks to the robot process and MoveIt) when it starts: after the robot process is up.
    grasp_context.supervisor = supervisor
    grasp_context.start()
    logger.info("XLeRobot：http://localhost:{}（机器人进程 8011，3D 视图端口 {}）", ServerConfig.PORT,
                grasp_context.viewer_port)
    try:
        serve(create_app())
    finally:
        supervisor.stop()
        logger.info("页面和抓取已停止；机器人进程和 ROS bringup 继续运行（python app/main.py --stop-all 才会停）")


if __name__ == "__main__":
    main()
