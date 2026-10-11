"""执行中的检查：夹没夹住（没夹住就张开、沿原路退回）、瓶子还立着没有（倒了就语音请人扶起）、
头部相机看夹爪到没到位，以及（默认关闭的）按头部相机实测修正预抓取位置。"""

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

from component.camera_calibration.check import CalibrationCheck
from component.task_execution.motion import ArmMotion
from component.task_execution.stages import JAWS, SEGMENTS
from config.planning import MotionConfig, RealPlanningConfig
from config.settings import use_utf8_output
from config.task import RealRobotConfig
from config.voice import AlertConfig
from util.motion_plan_helper import JointTrajectory
from util.speech_helper import SpeechHelper
from util.teleop_helper import TeleopHelper


class TaskChecks:
    def __init__(self, motion: ArmMotion, planning, check: CalibrationCheck, teleop: TeleopHelper, speech: SpeechHelper,
                 report: Callable[[str], None]) -> None:
        self.motion = motion
        self.planning = planning
        self.check = check
        self.teleop = teleop
        self.speech = speech
        self.report = report

    def holding(self, start_jaw: float) -> bool:
        """After closing: a jaw that shut on nothing reads about 0-2 (on the spray bottle 14-16). Then, instead of
        lifting and placing nothing (2026-10-09: closed at 1, the bottle pushed ~3 cm aside, then a false fallen-bottle
        alarm), it opens, backs out along the reach-in and the approach to the start, and the step fails."""
        motion = self.motion
        gripper = motion.real_arm()["gripper"]
        if gripper > RealRobotConfig.EMPTY_JAW_READING:
            return True
        self.report(f"夹爪合到 {gripper:.0f}（不到 {RealRobotConfig.EMPTY_JAW_READING:.0f}），"
                    f"没夹住：张开，沿原路退回起始位")
        backed = (motion.jaw(JAWS["open"]) and motion.move(*SEGMENTS["retreat"])
                  and motion.move(*SEGMENTS["return"], unwind=True) and motion.jaw(start_jaw))
        self.report("已退回起始位" if backed else "没能退回起始位，机器人停在原地")
        return False

    def wait_upright(self, scene, at: list[float] | None = None) -> bool:
        """The head camera looks at the bottle again: one that fell lies about its diameter high instead of its height
        at planning (or is gone from view). Until it stands, the speaker asks someone at the desk to stand it up, then
        looks again. The robot holds still meanwhile; a stop on the teleop side (Esc / its button, which blocks
        following again) or the arm's torque going off ends the wait and fails the step. at: where in the head image
        to look for it (pick-place: where it was put), else where it was chosen. Its height counts from the desk, so one
        standing in a box reads its height plus the floor's."""
        planned = float(np.ptp(scene.placed.mesh.bounds[:, 2]))
        # Following armed without targets: nothing moves, and a stop shows up as follow_blocked.
        self.teleop.arm_follow()
        calls = 0
        perception = self.planning.perception
        chosen = perception.point
        while True:
            try:
                if at is not None:
                    perception.point = tuple(at)
                height = float(np.ptp(self.planning.observe().placed.mesh.bounds[:, 2]))
            except Exception as error:
                logger.warning("没看到瓶子：{}", error)
                height = 0.0
            finally:
                perception.point = chosen
            if height >= AlertConfig.UPRIGHT_HEIGHT_SHARE * planned:
                self.report(f"瓶子立着（高 {height * 100:.1f} cm，规划时 {planned * 100:.1f} cm）"
                            + (f"，叫了 {calls} 次后有人扶起" if calls else ""))
                # Someone's hands were just at the desk: thank them and give them time to step back before moving.
                return not calls or self.clear_robot()
            if self.motion.stopped():
                self.report("等人扶瓶子时被停止")
                return False
            calls += 1
            self.report(f"瓶子倒了（高 {height * 100:.1f} cm，规划时 {planned * 100:.1f} cm），"
                        f"第 {calls} 次语音请人扶起")
            self.speech.say(AlertConfig.BOTTLE_FALLEN, wait=True)
            time.sleep(AlertConfig.RECHECK_PAUSE_S)

    def clear_robot(self) -> bool:
        """After someone stood the bottle up: thanks and a warning to step away, CLEAR_WAIT_S, and a second warning
        right before the robot moves (to its rest pose). A stop meanwhile fails the step."""
        self.report(f"谢谢并提醒远离机器人，{AlertConfig.CLEAR_WAIT_S:.0f} s 后再动")
        self.speech.say(AlertConfig.THANKS_STEP_AWAY, wait=True)
        deadline = time.monotonic() + AlertConfig.CLEAR_WAIT_S
        while time.monotonic() < deadline:
            if self.motion.stopped():
                self.report("等人离开时被停止")
                return False
            time.sleep(0.5)
        self.report("提醒：机器人要动了")
        self.speech.say(AlertConfig.ABOUT_TO_MOVE, wait=True)
        clear = not self.motion.stopped()
        if not clear:
            self.report("等人离开时被停止")
        return clear

    def check_jaw(self) -> bool:
        """The jaw as the head camera sees it against the model at the real joints: the median depth of the fixed jaw
        may differ by APPROACH_CHECK_M (it is the finger that slides past the bottle). The moving jaw is only
        reported: its depth sits ~1.3 cm off the opposite way to the fixed one at every pose (its opening, or its
        model), which no arm or camera error explains. A jaw out of view or without depth is reported, not judged."""
        arm = self.motion.arm
        frame = self.teleop.rgbd()
        joints = self.motion.head_joints(frame)
        _, pixels = self.check.project(joints, frame)
        errors = self.check.depth_errors({name: pixels[name]
                                          for name in (arm.link("Fixed_Jaw"), arm.link("Moving_Jaw"))}, frame)
        checked = [arm.link(link) for link in RealPlanningConfig.APPROACH_CHECK_LINKS]
        judged = {name: measured - model - arm.approach_check_offset_m
                  for name, (count, model, measured) in errors.items()
                  if name in checked and count >= 30 and model is not None}
        self.report(f"到位检查（头部相机看{arm.label}夹爪，实测 - 模型）：" + "，".join(
            f"{name} {count} 点" + (f" {(measured - model) * 100:+.1f} cm" if model is not None else " 无深度")
            for name, (count, model, measured) in errors.items()))
        worst = max((abs(value) for value in judged.values()), default=0.0)
        if worst > RealPlanningConfig.APPROACH_CHECK_M:
            self.report(f"偏差 {worst * 100:.1f} cm 超过 {RealPlanningConfig.APPROACH_CHECK_M * 100:.0f} cm，"
                        f"停在瓶子后方")
            return False
        return True

    def correct_approach(self, scene, grasp) -> tuple[bool, bool]:
        """Behind the bottle: where the jaw really is, by the head camera (RealPlanningConfig.CORRECT_*). A clear offset
        t is taken out: in the model's frame the bottle and the table sit at -t, so the jaw is sent to the planned
        pre-grasp minus t (the real pre-grasp), and grasp and lift are planned again from there; the approach then
        runs on through that move, so going back retraces it. Afterwards the jaw is looked at again. Returns (ok,
        corrected); not ok stops the grasp."""
        motion, arm = self.motion, self.motion.arm
        links = [arm.link("Fixed_Jaw"), arm.link("Wrist_Pitch_Roll")]
        frame = self.teleop.rgbd()
        found = self.check.offset(motion.head_joints(frame), frame, links, scene.scene.table_top_z)
        if found is None:
            self.report(f"头部相机没看清{arm.label}夹爪，不做位置修正")
            return True, False
        t = found["t"]
        self.report(f"预抓取位置实测：{arm.label}夹爪比模型偏 {np.round(t * 100, 1).tolist()} cm（x 前 y 左 z 上；"
                    f"对齐 {found['before_mm']:.0f} → {found['after_mm']:.0f} mm，{found['points']} 点）")
        size = float(np.linalg.norm(t))
        if size < RealPlanningConfig.CORRECT_MIN_M or found["after_mm"] > RealPlanningConfig.CORRECT_FIT_MAX_MM:
            return True, False
        if size > RealPlanningConfig.CORRECT_MAX_M:
            self.report(f"偏 {size * 100:.1f} cm，超过 {RealPlanningConfig.CORRECT_MAX_M * 100:.0f} cm 不修正，"
                        f"停在瓶子后方")
            return False, False
        planner = self.planning.planner
        planner.set_obstacles([(name, extents, self.shifted(pose, -t))
                               for name, extents, pose in self.planning.obstacles])
        target = self.shifted(grasp.tool_pose, -t)
        backed = target.copy()
        backed[:3, 3] -= MotionConfig.APPROACH_DISTANCE * target[:3, 2]
        lifted = target.copy()
        lifted[2, 3] += MotionConfig.LIFT_HEIGHT
        segments = motion.segments
        current = segments["approach"].positions[-1]
        fix = planner.plan_segment(current, backed, touching=False)
        reach_in = planner.plan_segment(fix.positions[-1], target, touching=True) if fix is not None else None
        lift = planner.plan_segment(reach_in.positions[-1], lifted, touching=True) if reach_in is not None else None
        if lift is None or not all(self.planning.roll_ok(segment.positions) for segment in (fix, reach_in, lift)):
            self.report("按实测位置重新规划失败，停在瓶子后方")
            return False, False
        segments["fix"] = fix
        if not motion.move("fix", 1):
            return False, False
        approach = segments["approach"]
        segments["approach"] = JointTrajectory(joint_names=approach.joint_names,
                                               positions=np.vstack([approach.positions, fix.positions[1:]]),
                                               dt=approach.dt)
        segments["grasp"], segments["lift"] = reach_in, lift
        # Look again: the jaw (model + measured offset) against where the plan wanted it.
        wanted = grasp.tool_pose[:3, 3] - MotionConfig.APPROACH_DISTANCE * grasp.tool_pose[:3, 2]
        frame = self.teleop.rgbd()
        again = self.check.offset(motion.head_joints(frame), frame, links, scene.scene.table_top_z)
        if again is None:
            self.report("修正后头部相机没看清夹爪，停在瓶子后方")
            return False, False
        real_tool = planner.tool_pose(fix.positions[-1])[:3, 3] + again["t"]
        residual = real_tool - wanted
        self.report(f"已按实测修正：现在离预抓取点 {np.round(residual * 100, 1).tolist()} cm"
                    f"（{np.linalg.norm(residual) * 100:.1f} cm）")
        if np.linalg.norm(residual) > RealPlanningConfig.APPROACH_CHECK_M:
            self.report(f"修正后还差 {np.linalg.norm(residual) * 100:.1f} cm，"
                        f"超过 {RealPlanningConfig.APPROACH_CHECK_M * 100:.0f} cm，停在瓶子后方")
            return False, False
        return True, True

    @staticmethod
    def shifted(pose: np.ndarray, t: np.ndarray) -> np.ndarray:
        moved = np.array(pose, float).copy()
        moved[:3, 3] += t
        return moved


def demo_task_checks() -> None:
    """Needs the bringup and the robot process; read-only: the head camera's look at the right jaw."""
    from component.grasp_planning.desk_guard import DeskGuard
    from component.grasp_planning.service import RealGraspPlanning
    from util.moveit_helper import MoveItHelper

    teleop = TeleopHelper()
    def report(text):
        return logger.info(text)

    motion = ArmMotion(MoveItHelper(node_name="task_checks_demo"), teleop, DeskGuard(), report)
    checks = TaskChecks(motion, RealGraspPlanning(teleop), CalibrationCheck(teleop), teleop, SpeechHelper(), report)
    logger.info("到位检查：{}", checks.check_jaw())


def main() -> None:
    use_utf8_output()
    demo_task_checks()


if __name__ == "__main__":
    main()
