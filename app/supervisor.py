"""子进程管理：app/main.py 启动时把机器人进程和 ROS bringup 带起来（已在运行就复用），之后盯着机器人进程，挂了就重启。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import os
import signal
import subprocess
import threading
import time

import httpx
from loguru import logger

from config.settings import BringupConfig, PathConfig, RobotServerConfig, use_utf8_output


class Supervisor:
    """The robot process outlives app/main.py on purpose: a restart of the pages and planning (new code, say) must not
    drop an arm or change its torque, so a healthy one is reused and only stop_all() ends it. The bringup is reused the
    same way. Both run in their own session (start_new_session), so Ctrl+C on app/main.py does not reach them."""

    def __init__(self) -> None:
        self.robot: subprocess.Popen | None = None
        self.fails = 0
        self.restarts = 0
        self.running = False
        self.thread: threading.Thread | None = None

    # --- checks ---

    @staticmethod
    def robot_healthy() -> bool:
        try:
            return httpx.get(f"{RobotServerConfig.URL}/api/status", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    def bringup_running() -> bool:
        result = subprocess.run(["pgrep", "-x", "-f", f"{BringupConfig.PROCESS} .*"], capture_output=True)
        return result.returncode == 0

    @staticmethod
    def check_environment() -> list[str]:
        """What is wrong outside this project (reported, never fatal): WSL keepalive, clock sync, the Zenoh router."""
        problems = []
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        if "sleep infinity # wsl keepalive" not in ps:
            script = Path("/tmp/keepalive.sh")
            script.write_text('#!/bin/bash\nexec -a "sleep infinity # wsl keepalive" sleep infinity\n')
            subprocess.Popen(["bash", str(script)], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("WSL 保活进程已启动")
        if subprocess.run(["systemctl", "is-active", "--quiet", "systemd-timesyncd"]).returncode == 0:
            problems.append("systemd-timesyncd 在运行（应关闭：它会让 WSL 时间跳变）")
        if subprocess.run(["systemctl", "is-active", "--quiet", BringupConfig.ZENOH_SERVICE]).returncode != 0:
            problems.append(f"Zenoh 路由（{BringupConfig.ZENOH_SERVICE}）没在运行：MoveIt 和机器人跟随都用它")
        for problem in problems:
            logger.warning("环境检查：{}", problem)
        return problems

    # --- starting ---

    def start_robot(self) -> bool:
        if self.robot_healthy():
            logger.info("机器人进程已在运行（{}），复用", RobotServerConfig.URL)
            return True
        PathConfig.LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = open(PathConfig.LOG_DIR / "robot_stdout.log", "ab")
        self.robot = subprocess.Popen([str(PathConfig.ROBOT_PYTHON), "app/robot_main.py"], cwd=BASE_DIR, start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        logger.info("机器人进程启动中（pid {}）", self.robot.pid)
        deadline = time.monotonic() + RobotServerConfig.START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.robot.poll() is not None:
                logger.error("机器人进程退出了（代码 {}），见 log/robot_stdout.log", self.robot.returncode)
                return False
            if self.robot_healthy():
                logger.info("机器人进程就绪")
                return True
            time.sleep(1.0)
        logger.error("机器人进程 {:.0f} s 内没就绪", RobotServerConfig.START_TIMEOUT_S)
        return False

    def start_bringup(self) -> None:
        if self.bringup_running():
            logger.info("ROS bringup 已在运行，复用")
            return
        out = open(PathConfig.LOG_DIR / "bringup.log", "ab")
        package, launch = BringupConfig.LAUNCH
        command = f"source {BringupConfig.ROS_SETUP} && source {BringupConfig.WORKSPACE_SETUP} && exec ros2 launch {package} {launch}"
        subprocess.Popen(["bash", "-c", command], cwd=BASE_DIR, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=out, stderr=subprocess.STDOUT, env={**os.environ, "RMW_IMPLEMENTATION": "rmw_zenoh_cpp"})
        logger.info("ROS bringup 启动中，等 {:.0f} s", BringupConfig.SETTLE_S)
        time.sleep(BringupConfig.SETTLE_S)
        if not self.bringup_running():
            logger.error("ROS bringup 没起来，见 log/bringup.log")

    def start(self) -> None:
        """Called by app/main.py before anything else; the watch runs in a thread afterwards."""
        self.check_environment()
        self.start_robot()
        self.start_bringup()
        self.running = True
        self.thread = threading.Thread(target=self.watch, name="supervisor", daemon=True)
        self.thread.start()

    # --- watching ---

    def watch(self) -> None:
        while self.running:
            time.sleep(RobotServerConfig.CHECK_S)
            if self.robot_healthy():
                self.fails = 0
                continue
            self.fails += 1
            logger.warning("机器人进程没响应（连续 {} 次）", self.fails)
            if self.fails >= RobotServerConfig.RESTART_AFTER_FAILS:
                self.restart_robot()

    def restart_robot(self) -> bool:
        """It takes over parts that still have torque when it connects (component/robot/link.py), so an arm holding
        somewhere stays where it is."""
        self.kill_robot()
        self.restarts += 1
        self.fails = 0
        logger.warning("重启机器人进程（第 {} 次）", self.restarts)
        return self.start_robot()

    # --- stopping ---

    @staticmethod
    def robot_pid() -> int | None:
        """Whoever listens on the robot port (also one started by an earlier app/main.py)."""
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{RobotServerConfig.PORT}"], capture_output=True, text=True).stdout
        for token in out.replace(",", " ").split():
            if token.startswith("pid="):
                return int(token[4:])
        return None

    def kill_robot(self, timeout: float = 10.0) -> None:
        pid = self.robot_pid()
        if pid is None:
            return
        os.kill(pid, signal.SIGINT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.robot_pid() == pid:
            time.sleep(0.5)
        if self.robot_pid() == pid:
            os.kill(pid, signal.SIGKILL)
            logger.warning("机器人进程没在 {:.0f} s 内退出，已强制结束", timeout)

    def stop(self) -> None:
        """app/main.py exiting normally: only the watch ends; the robot process and the bringup keep running."""
        self.running = False

    def stop_all(self) -> None:
        """python app/main.py --stop-all: the arms and head back to rest with torque off, then the robot process and
        the bringup end."""
        self.stop()
        if self.robot_healthy():
            try:
                httpx.post(f"{RobotServerConfig.URL}/api/park", json={}, timeout=60.0)
                logger.info("已回停靠位并关力矩")
            except httpx.HTTPError as error:
                logger.warning("回停靠位没成功：{}", error)
        self.kill_robot()
        subprocess.run(["pkill", "-INT", "-x", "-f", f"{BringupConfig.PROCESS} .*"])
        subprocess.run(["pkill", "-INT", "-f", f"^/usr/bin/python3 /opt/ros/jazzy/bin/ros2 launch {BringupConfig.LAUNCH[0]} "])
        logger.info("机器人进程和 ROS bringup 已停止")


def demo_supervisor() -> None:
    """Read-only: what is running (nothing is started or stopped)."""
    supervisor = Supervisor()
    logger.info("环境问题：{}", supervisor.check_environment() or "无")
    logger.info("机器人进程：{}（pid {}）", "正常" if supervisor.robot_healthy() else "没响应", supervisor.robot_pid())
    logger.info("ROS bringup：{}", "在运行" if supervisor.bringup_running() else "没在运行")


def main() -> None:
    use_utf8_output()
    demo_supervisor()


if __name__ == "__main__":
    main()
