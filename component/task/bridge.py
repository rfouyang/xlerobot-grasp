"""桥：把 ros2_control mock 上 MoveIt 正在执行的右臂 + 夹爪关节，换成真机角度，经遥操作服务的跟随模式流给真机。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import threading
import time

from loguru import logger

from config.settings import RealRobotConfig, use_utf8_output
from util.joint_mapping_helper import JointMappingHelper
from util.moveit_helper import MoveItHelper
from util.teleop_helper import TeleopHelper

from component.planning.arm import RIGHT
from config.settings import DeskGuardConfig

ARM_JOINTS = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw")


class RealRobotBridge:
    """MoveIt executes on the mock hardware as in simulation; the real arm follows the mock. The teleop service
    caps each joint's speed and range, and once it is stopped (Esc / its stop button) it refuses targets: the bridge
    then stops sending and reports blocked, and the step that was running fails."""

    def __init__(self, moveit: MoveItHelper, teleop: TeleopHelper, **kwargs) -> None:
        self.moveit = moveit
        self.teleop = teleop
        self.mapping = JointMappingHelper()
        # The arm the mock drives and the real one follows (set for each grasp).
        self.arm = kwargs.get("arm", RIGHT)
        # Each target is checked against the desk before it is sent (component/real_robot/desk_guard.py).
        self.guard = kwargs.get("guard")
        self.guard_stop = ""
        self.period = 1.0 / kwargs.get("hz", RealRobotConfig.FOLLOW_HZ)
        self.running = False
        self.thread: threading.Thread | None = None
        # The teleop service's last answer: accepted, blocked, torque, present (real degrees).
        self.reply: dict = {}
        self.blocked = False

    @property
    def part(self) -> str:
        part = self.arm.part
        return part

    def target(self) -> dict[str, float]:
        """The mock's arm + jaw now, in real degrees (gripper 0-100), the wrist levelled against the real arm."""
        positions = self.moveit.joint_positions()
        real = self.mapping.to_real({joint: positions[joint] for joint in (*self.arm.joints, self.arm.jaw)})[self.arm.part]
        levelled = self.level(real, self.reply.get("present", {}))
        return levelled

    @staticmethod
    def level(target: dict[str, float], present: dict[str, float]) -> dict[str, float]:
        """target with wrist_flex taking up what shoulder_lift and elbow_flex are short of theirs, so the jaw keeps the
        planned pitch (pitch = shoulder_lift + elbow_flex + wrist_flex, checked against the URDF: a 10.8° dip
        levelled to 0.0°). Unchanged while nothing is known about the real arm."""
        if not present:
            return dict(target)
        short = sum(target[joint] - present[joint] for joint in ("shoulder_lift", "elbow_flex"))
        limit = RealRobotConfig.LEVEL_MAX_DEG
        levelled = {**target, "wrist_flex": target["wrist_flex"] + max(-limit, min(limit, short))}
        return levelled

    def start(self) -> None:
        self.teleop.arm_follow()
        self.blocked = False
        self.guard_stop = ""
        # The real arm as it is, so the first target sent is already levelled (no wrist jump between steps).
        present = {motor: values[0] for motor, values in self.teleop.status()["joints"][self.part].items()}
        self.reply = {"present": present}
        self.running = True
        self.thread = threading.Thread(target=self.loop, name="real_robot_bridge", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        self.thread.join()

    def loop(self) -> None:
        """Any error on this thread is logged and ends following as blocked (a silent death once left the real arm
        standing while the mock moved, 2026-10-09)."""
        try:
            self.follow()
        except Exception:
            self.blocked = True
            logger.exception("跟随线程出错，停止发送")
        finally:
            self.running = False

    def follow(self) -> None:
        sent = 0
        with self.teleop.follow_stream() as stream:
            while self.running:
                started = time.monotonic()
                target = self.target()
                if self.guard is not None:
                    ok, clearance, link = self.guard.check_real(self.arm, target)
                    if clearance < self.arm.desk_margin_m - DeskGuardConfig.FOLLOW_TOLERANCE_M:
                        self.blocked = True
                        self.guard_stop = f"{link} 离桌面只剩 {clearance * 100:.1f} cm"
                        logger.warning("桌面守卫：{}，桥停止发送，手臂停在原地", self.guard_stop)
                        break
                stream.send(json.dumps({"part": self.part, "joints": target}))
                self.reply = json.loads(stream.recv())
                sent += 1
                if sent == 1:
                    logger.info("跟随开始：{} 第一个目标已发出", self.part)
                if self.reply["blocked"] or not self.reply["torque"]:
                    self.blocked = True
                    logger.warning("真机拒绝跟随（被停止或没有力矩），桥停止发送")
                    break
                time.sleep(max(0.0, self.period - (time.monotonic() - started)))

    def error(self, target: dict[str, float], **kwargs) -> float:
        """Largest |present - target| over the arm joints (the gripper left out unless asked), real degrees."""
        joints = [joint for joint in target if kwargs.get("gripper", False) or joint != "gripper"]
        present = self.reply.get("present", {})
        error = max(abs(present[joint] - target[joint]) for joint in joints) if present else float("inf")
        return error


def demo_real_robot_bridge() -> None:
    """Needs the bringup launch and the teleop service with the right arm powered at the grasp start pose. The
    mock is set to the real arm's pose first, so the stream asks for where the arm already is: nothing moves."""
    import numpy as np

    teleop = TeleopHelper()
    moveit = MoveItHelper(node_name="real_robot_bridge_demo")
    mapping = JointMappingHelper()
    status = teleop.status()
    real = {"right_arm": {motor: values[0] for motor, values in status["joints"]["right_arm"].items()}}
    urdf = mapping.to_urdf(real)
    current = moveit.joint_positions()
    names = list(ARM_JOINTS[:5])
    moveit.execute_trajectory("right_arm", names, np.array([[current[n] for n in names], [urdf[n] for n in names]]), 0.5)
    moveit.execute_trajectory("right_gripper", ["Jaw"], np.array([[current["Jaw"]], [urdf["Jaw"]]]), 0.5)
    bridge = RealRobotBridge(moveit, teleop)
    target = bridge.target()
    logger.info("mock 换成真机角度：{}", {joint: round(value, 1) for joint, value in target.items()})
    bridge.start()
    time.sleep(1.0)
    logger.info("跟随 1 s：接受 {}，被拦 {}，与真机最大差 {:.2f}°", bridge.reply.get("accepted"), bridge.blocked, bridge.error(target))
    bridge.stop()


def main() -> None:
    use_utf8_output()
    demo_real_robot_bridge()


if __name__ == "__main__":
    main()
