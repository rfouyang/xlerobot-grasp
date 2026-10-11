"""手臂运动：MoveIt 在 mock 上执行一段轨迹，桥（bridge.py）让真机跟着走；一步结束时真机必须追上 mock。
包括按规划段走、肩部 pan 转一点、夹爪开合、松开时先张开再左移，以及让 mock 和真机对齐。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from component.grasp_planning.arm import RIGHT, Arm
from component.grasp_planning.desk_guard import DeskGuard
from component.task_execution.bridge import ARM_JOINTS, RealRobotBridge
from component.task_execution.stages import JAWS
from config.planning import GripperConfig
from config.settings import use_utf8_output
from config.task import RealRobotConfig, WristAlignConfig
from util.joint_mapping_helper import JointMappingHelper
from util.motion_plan_helper import JointTrajectory
from util.moveit_helper import MoveItHelper
from util.teleop_helper import TeleopHelper


class ArmMotion:
    """Before a step the mock is set to where that step starts and the real arm must already be there, so the mock and
    the real arm never drift apart. pan_offset: the wrist camera's correction to the shoulder pan (URDF radians), added
    to every later step; a step run with unwind takes it back out along the way. segments: the planned trajectories by
    name (set at planning)."""

    def __init__(self, moveit: MoveItHelper, teleop: TeleopHelper, guard: DeskGuard,
                 report: Callable[[str], None]) -> None:
        self.moveit = moveit
        self.teleop = teleop
        self.guard = guard
        self.report = report
        self.mapping = JointMappingHelper()
        self.bridge = RealRobotBridge(moveit, teleop, guard=guard)
        self.arm: Arm = RIGHT
        self.pan_offset = 0.0
        self.segments: dict[str, JointTrajectory] = {}

    def use_arm(self, arm: Arm) -> None:
        self.arm = arm
        self.bridge.arm = arm

    # --- motions ---

    def turn(self, angle: float) -> bool:
        """The shoulder pan by angle (URDF radians), the rest of the arm held."""
        names = list(self.arm.joints)
        current = self.moveit.joint_positions()
        start = np.array([current[name] for name in names])
        end = start.copy()
        end[0] += angle
        steps = max(2, int(abs(np.degrees(angle)) / WristAlignConfig.PAN_SPEED_DEG_S / GripperConfig.JAW_STEP_S))
        rows = np.linspace(start, end, steps)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm, names, rows, GripperConfig.JAW_STEP_S)
        arrived = executed and self.catch_up()
        self.bridge.stop()
        if arrived:
            self.pan_offset += angle
        return arrived

    def move(self, segment_name: str, direction: int, **kwargs) -> bool:
        """unwind: the pan correction goes from all of it at the first row to none at the last (back to the
        planned start); otherwise every row carries it. drift: a further pan turn (URDF radians) spread over the
        rows, kept in the correction afterwards."""
        segment = self.segments[segment_name]
        rows = segment.positions[::direction].copy()
        rows[:, 0] += self.pan_offset * (np.linspace(1.0, 0.0, len(rows)) if kwargs.get("unwind") else 1.0)
        drift = kwargs.get("drift", 0.0)
        rows[:, 0] += drift * np.linspace(0.0, 1.0, len(rows))
        # Every row (densified) clear of the desk by the arm's margin, before anything moves.
        current = self.moveit.joint_positions()
        clear, lowest, _, link = self.guard.check_rows(self.arm, rows, current)
        if not clear:
            self.report(f"桌面守卫：这一步「{segment_name}」会让 {link} 离桌面只剩 {lowest * 100:.1f} cm"
                        f"（要 {self.guard.required_m * 100:.1f} cm），不执行")
            return False
        if not self.start_at(rows[0]):
            return False
        names = list(self.arm.joints)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm, names, rows,
                                                  segment.dt * RealRobotConfig.TIME_SCALE)
        arrived = executed and self.catch_up()
        self.bridge.stop()
        if arrived:
            self.pan_offset += drift
        return arrived

    def release(self, turn: float) -> bool:
        """The jaw opens, and once the real one is RELEASE_OPEN_FIRST wider than where it held the bottle (the moving
        finger has let go: judged on the real jaw, which only starts to move when its target passes where the bottle
        stopped it) the rest of the opening goes together with the shoulder pan turned by turn (URDF radians).
        Turning while the finger still pressed the bottle dragged it over (2026-10-06)."""
        arm = list(self.arm.joints)
        held = self.real_arm()["gripper"]
        let_go = held + WristAlignConfig.RELEASE_OPEN_FIRST
        current = self.moveit.joint_positions()
        start = np.array([current[name] for name in arm + [self.arm.jaw]])
        # Phase 1, the jaw alone and RELEASE_FIRST_SLOWDOWN times slower: far enough that the real one gets
        # past let_go.
        first = min(JAWS["release"],
                    self.mapping.to_urdf({self.arm.part: {**self.real_arm(),
                                                          "gripper": let_go + WristAlignConfig.RELEASE_OPEN_FIRST}}
                                         )[self.arm.jaw])
        steps = max(2, int(GripperConfig.JAW_MOVE_S * WristAlignConfig.RELEASE_FIRST_SLOWDOWN * (first - start[5])
                           / (JAWS["release"] - start[5]) / GripperConfig.JAW_STEP_S))
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_gripper, [self.arm.jaw],
                                                  np.linspace(start[5], first, steps).reshape(-1, 1),
                                                  GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        deadline = time.monotonic() + WristAlignConfig.RELEASE_WAIT_S
        while (executed and self.bridge.reply.get("present", {}).get("gripper", held) < let_go
               and time.monotonic() < deadline):
            time.sleep(0.05)
        opened = self.bridge.reply.get("present", {}).get("gripper", held)
        if not executed or opened < let_go or self.bridge.blocked:
            self.report(f"夹爪没有张开（{held:.0f} → {opened:.0f}，要到 {let_go:.0f}），不往左移")
            self.bridge.stop()
            return False
        self.report(f"动指已离开瓶子（夹爪 {held:.0f} → {opened:.0f}），继续张开并左移")
        # Phase 2: the rest of the opening and the turn together.
        current = self.moveit.joint_positions()
        middle = np.array([current[name] for name in arm + [self.arm.jaw]])
        end = middle.copy()
        end[0] += turn
        end[5] = JAWS["release"]
        steps = max(2, int(GripperConfig.JAW_MOVE_S * (end[5] - middle[5]) / (JAWS["release"] - start[5])
                           / GripperConfig.JAW_STEP_S))
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm_jaw, arm + [self.arm.jaw],
                                                  np.linspace(middle, end, steps),
                                                  GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        arrived = executed and self.catch_up()
        time.sleep(RealRobotConfig.JAW_SETTLE_S)
        self.bridge.stop()
        if arrived:
            self.pan_offset += turn
        return arrived

    def jaw(self, goal: float) -> bool:
        """The jaw over JAW_MOVE_S on the mock, the real one following; a closing jaw stops on the bottle."""
        current = self.moveit.joint_positions()[self.arm.jaw]
        steps = max(2, int(GripperConfig.JAW_MOVE_S / GripperConfig.JAW_STEP_S))
        rows = np.linspace(current, goal, steps).reshape(-1, 1)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_gripper, [self.arm.jaw], rows,
                                                  GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        time.sleep(RealRobotConfig.JAW_SETTLE_S)
        ok = executed and not self.bridge.blocked
        self.bridge.stop()
        return ok

    # --- the real robot ---

    def real_arm(self) -> dict[str, float]:
        arm = {motor: values[0] for motor, values in self.teleop.status()["joints"][self.arm.part].items()}
        return arm

    def head_joints(self, frame: dict) -> dict:
        """Every part's real joints now, the head as it was for frame."""
        joints = {part: {motor: values[0] for motor, values in motors.items()}
                  for part, motors in self.teleop.status()["joints"].items()}
        joints["head"] = {"head_motor_1": float(frame["head_joints"][0]),
                          "head_motor_2": float(frame["head_joints"][1])}
        return joints

    def stopped(self) -> bool:
        """A stop on the teleop side (Esc / its button blocks following again) or the arm's torque gone."""
        status = self.teleop.status()
        stopped = status["follow_blocked"] or not status["torque"][self.arm.part]
        return stopped

    # --- keeping the mock and the real arm together ---

    def sync_mock(self) -> None:
        """Every mock joint to the real robot's (the bridge off): the first step then streams the real arm's own
        pose, and the jaw does not jump to wherever the mock's was."""
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in joints.items()}
                for part, joints in status["joints"].items()}
        urdf = self.mapping.to_urdf(real)
        current = self.moveit.joint_positions()
        groups = {"right_arm": list(ARM_JOINTS[:5]), "right_gripper": ["Jaw"],
                  "left_arm": [f"{joint}_2" for joint in ARM_JOINTS[:5]], "left_gripper": ["Jaw_2"],
                  "head": ["head_pan_joint", "head_tilt_joint"]}
        for group, names in groups.items():
            self.moveit.execute_trajectory(group, names,
                                           np.array([[current[name] for name in names],
                                                     [urdf[name] for name in names]]), 0.2)
        self.report("mock 已对齐真机")

    def start_at(self, row: np.ndarray) -> bool:
        """The mock goes to the step's first row; the real arm has to be there already (within LOADED_ARRIVED_DEG)."""
        names = list(self.arm.joints)
        current = self.moveit.joint_positions()
        self.moveit.execute_trajectory(self.arm.moveit_arm, names, np.array([[current[name] for name in names], row]),
                                       0.2)
        present = self.real_arm()
        wanted = self.bridge.level(self.mapping.to_real(dict(zip(names, map(float, row), strict=False)))[self.arm.part],
                                   present)
        error = max(abs(present[motor] - wanted[motor]) for motor in wanted)
        # The last step may have settled short under load (catch_up): within LOADED_ARRIVED_DEG it starts from there.
        if error > RealRobotConfig.LOADED_ARRIVED_DEG:
            self.report(f"真机不在这一步的起点（差 {error:.1f}°）")
            return False
        return True

    def catch_up(self) -> bool:
        """After MoveIt finished on the mock, until the real arm is within ARRIVED_DEG of the mock (arm joints), or
        has settled within LOADED_ARRIVED_DEG of it (carrying the bottle it cannot get closer)."""
        deadline = time.monotonic() + RealRobotConfig.MOVE_TIMEOUT_S
        history: list[tuple[float, dict[str, float]]] = []
        while time.monotonic() < deadline and not self.bridge.blocked:
            # Fresh each pass: the levelled wrist target follows the shoulder and elbow as they settle.
            target = self.bridge.target()
            error = self.bridge.error(target)
            if error <= RealRobotConfig.ARRIVED_DEG:
                return True
            now = time.monotonic()
            present = {joint: value for joint, value in self.bridge.reply.get("present", {}).items()
                       if joint != "gripper"}
            history = ([(at, joints) for at, joints in history if now - at <= RealRobotConfig.SETTLED_S]
                       + [(now, present)])
            moved = (max(max(joints[joint] for _, joints in history) - min(joints[joint] for _, joints in history)
                         for joint in present) if present else np.inf)
            settled = now - history[0][0] >= RealRobotConfig.SETTLED_S * 0.9 and moved <= RealRobotConfig.SETTLED_DEG
            if settled and error <= RealRobotConfig.LOADED_ARRIVED_DEG:
                worst = max(present, key=lambda joint: abs(present[joint] - target[joint]))
                self.report(f"负载下停住了：{worst} 差 {error:.1f}°"
                            f"（≤ {RealRobotConfig.LOADED_ARRIVED_DEG:.0f}°），算到位")
                return True
            time.sleep(0.1)
        self.report("被停止" if self.bridge.blocked
                    else f"真机没跟上（差 {self.bridge.error(self.bridge.target()):.1f}°）")
        return False


def demo_arm_motion() -> None:
    """Needs the bringup and the robot process; read-only: the real right arm and whether following is blocked."""
    motion = ArmMotion(MoveItHelper(node_name="arm_motion_demo"), TeleopHelper(), DeskGuard(),
                       lambda text: logger.info(text))
    logger.info("右臂真机：{}；停止状态：{}", motion.real_arm(), motion.stopped())


def main() -> None:
    use_utf8_output()
    demo_arm_motion()


if __name__ == "__main__":
    main()
