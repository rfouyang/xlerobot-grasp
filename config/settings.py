"""Project configuration, split by domain."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# graspgenx clones its weights at import time when this is unset;
# they live in the project's model/ (PathConfig.MODEL_DIR).
os.environ.setdefault("GRASPGENX_CHECKPOINT_DIR", str(BASE_DIR / "model" / "graspgenx"))
# WSL runs with mirrored networking, where DDS discovery breaks; ROS 2 goes through the Zenoh router instead.
os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_zenoh_cpp")
# MuJoCo renders offscreen through EGL. PyOpenGL fixes its platform on first import (graspgenx pulls it in
# early), so both are set here, before anything else is imported.
os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")


def use_utf8_output() -> None:
    """Make stdout/stderr able to print Chinese; entry points call this first."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


class PathConfig:
    BASE_DIR = BASE_DIR
    # package:// URIs in the URDF resolve under this folder.
    ROS_SRC_DIR = BASE_DIR / "ros_ws" / "src"
    URDF_PATH = ROS_SRC_DIR / "xlerobot_description" / "urdf" / "xlerobot.urdf"
    # Model weights (not in git).
    MODEL_DIR = BASE_DIR / "model"
    # Captured data: calibration sessions, depth evaluation frames.
    DATA_DIR = BASE_DIR / "data"
    # Both processes' logs (grasp_*.log, robot_*.log).
    LOG_DIR = BASE_DIR / "log"
    # What the programs make: caches, plan pictures, videos.
    OUTPUT_DIR = BASE_DIR / "output"
    # Demos write here and only here.
    DEMO_DIR = OUTPUT_DIR / "demo"
    # The robot process runs in its own environment (lerobot cannot share this one, see robot_env/pyproject.toml).
    ROBOT_PYTHON = BASE_DIR / "robot_env" / ".venv" / "bin" / "python"


class MoveItConfig:
    ROBOT_NAME = "xlerobot"
    MOVEIT_PACKAGE = "xlerobot_moveit_config"
    # Relative to the MoveIt config package.
    MOVEIT_CPP_FILE = "config/moveit_cpp.yaml"
    NODE_NAME = "xlerobot_grasp"
    # Arm name used on the page -> its MoveIt groups.
    ARMS = {
        "right": {"arm": "right_arm", "gripper": "right_gripper"},
        "left": {"arm": "left_arm", "gripper": "left_gripper"},
    }
    ARM_LABELS = {"right": "右臂", "left": "左臂"}


class ServerConfig:
    """app/main.py: the pages, the grasp API (under /grasp-api) and the forwarding to the robot process; the only port a
    browser opens (the iPad too, hence every interface)."""

    HOST = "0.0.0.0"
    PORT = 8010
    # The same app over HTTPS: browsers give the microphone only to https pages (and localhost), so the iPad uses
    # https://<this PC>:8443 with the self-signed certificate made at start (util/tls_helper.py; accept it once).
    HTTPS_PORT = 8443
    TLS_DIR = BASE_DIR / "config" / "tls"
    LOG_LEVEL = "INFO"
    # How often the page's joint table is pushed over the WebSocket.
    STATE_PUSH_INTERVAL_S = 0.1


class RobotServerConfig:
    """The robot process (app/robot_main.py): motors, cameras, keyboard teleop. app/main.py starts it and forwards
    the pages' /robot-api/* and /robot-ws/* here; nothing outside this machine reaches it."""

    HOST = "127.0.0.1"
    PORT = 8011
    LOG_LEVEL = "INFO"
    # How often the page gets the robot state over the WebSocket.
    STATE_PUSH_INTERVAL_S = 0.1
    URL = f"http://{HOST}:{PORT}"
    CONNECT_TIMEOUT_S = 3.0
    # component/process_supervision: started from app/main.py; healthy when /api/status answers; restarted after this
    # many failed checks in a row (one every CHECK_S). A restart that fails (USB unplugged, say) is tried again after a
    # wait that doubles up to RESTART_MAX_WAIT_S, not every 20 s all night.
    START_TIMEOUT_S = 90.0
    CHECK_S = 5.0
    RESTART_AFTER_FAILS = 3
    RESTART_MAX_WAIT_S = 300.0


class UsbConfig:
    """The robot's USB devices, passed from Windows into WSL by usbipd-win (util/usbip_helper.py, at app/main.py's
    start). Sharing them (bind) needs an administrator once (launch/attach_usb.ps1); attaching does not."""

    USBIPD = Path("/mnt/c/Program Files/usbipd-win/usbipd.exe")
    # VID:PID -> (what it is, how many); attached in this order, the RealSense last (it sometimes needs a moment).
    DEVICES = {"1a86:55d3": ("电机总线（串口）", 2), "0c45:64ab": ("腕部相机（UVC）", 2),
               "8086:0ad3": ("头部 RealSense D415", 1)}
    # After each attach, before the next (the RealSense fails with "Set Address Failed" when rushed).
    SETTLE_S = 1.0


class BringupConfig:
    """The ROS 2 bringup app/main.py starts when it is not running (robot_state_publisher + ros2_control's mock, which
    MoveIt plans against); the Zenoh router is the systemd service rmw-zenohd."""

    LAUNCH = ("xlerobot_moveit_config", "bringup.launch.py")
    PROCESS = "/opt/ros/jazzy/lib/controller_manager/ros2_control_node"
    ROS_SETUP = "/opt/ros/jazzy/setup.bash"
    WORKSPACE_SETUP = BASE_DIR / "ros_ws" / "install" / "setup.bash"
    SETTLE_S = 12.0
    ZENOH_SERVICE = "rmw-zenohd"
