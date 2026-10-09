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
    the project's ros_ws sourced (and RMW_IMPLEMENTATION set), so `python app/main.py` is all it takes."""
    workspace = BASE_DIR / "ros_ws" / "install"
    if str(workspace) in os.environ.get("AMENT_PREFIX_PATH", ""):
        return
    command = (f"source /opt/ros/jazzy/setup.bash && source {workspace / 'setup.bash'} && "
               f"exec {sys.executable} {' '.join(map(repr, [str(Path(__file__).resolve()), *sys.argv[1:]]))}")
    os.execvpe("bash", ["bash", "-c", command], {**os.environ, "RMW_IMPLEMENTATION": "rmw_zenoh_cpp"})


if __name__ == "__main__":
    with_ros_environment()

import argparse

import uvicorn
from fastapi import FastAPI
from loguru import logger

from app.api.calibration_api import create_calibration_api
from app.api.grasp_execution_api import create_grasp_execution_api
from app.api.grasp_generation_api import create_grasp_generation_api
from app.api.planning_api import create_planning_api
from app.api.real_grasp_api import create_real_grasp_api
from app.api.robot_operation_api import create_robot_operation_api
from app.api.robot_proxy_api import create_robot_proxy_api
from app.api.system_api import create_system_api
from component.system_check.supervisor import Supervisor
from app.ui.routes import create_ui, mount_ui_static
from config.settings import PathConfig, ServerConfig, use_utf8_output
from util.log_helper import setup_logger

# The pages call the grasp API under this prefix (it was another service before the merge).
GRASP_PREFIX = "/grasp-api"


def create_app(supervisor: Supervisor) -> FastAPI:
    app = FastAPI(title="XLeRobot")
    app.include_router(create_ui())
    app.include_router(create_robot_proxy_api())
    app.include_router(create_system_api(supervisor))
    app.include_router(create_calibration_api())
    app.include_router(create_planning_api())
    for router in (create_robot_operation_api(), create_grasp_generation_api(), create_grasp_execution_api(), create_real_grasp_api()):
        app.include_router(router, prefix=GRASP_PREFIX)
    mount_ui_static(app)
    return app


@logger.catch(reraise=True)
def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="XLeRobot：页面、抓取和机器人进程")
    parser.add_argument("--stop-all", action="store_true", help="回停靠位并关力矩，停掉机器人进程和 ROS bringup，然后退出")
    args = parser.parse_args()
    setup_logger(PathConfig.LOG_DIR, ServerConfig.LOG_LEVEL, name="grasp")
    supervisor = Supervisor()
    if args.stop_all:
        supervisor.stop_all()
        return
    supervisor.start()
    # Imported only now: it loads the models (and talks to the robot process and MoveIt) when it starts.
    from app.context import grasp_context

    grasp_context.start()
    logger.info("XLeRobot：http://localhost:{}（机器人进程 8011，3D 视图端口 {}）", ServerConfig.PORT, grasp_context.viewer_port)
    try:
        # log_config=None: uvicorn's records go through the loguru intercept. The camera streams never end on their
        # own, so a graceful shutdown would wait for every open page to go away.
        uvicorn.run(create_app(supervisor), host=ServerConfig.HOST, port=ServerConfig.PORT, log_config=None, timeout_graceful_shutdown=2)
    finally:
        supervisor.stop()
        logger.info("页面和抓取已停止；机器人进程和 ROS bringup 继续运行（python app/main.py --stop-all 才会停）")


if __name__ == "__main__":
    main()
