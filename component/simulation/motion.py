"""抓取动作：cuRobo 规划 接近 → 抓取 → 抬起，经 MoveIt 下发给 ros2_control 执行，中间开合夹爪。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time
from typing import Callable

import numpy as np
from loguru import logger

from component.planning.reachability import ReachableGrasp
from config.planning import GripperConfig, MotionConfig
from config.settings import use_utf8_output
from util.motion_plan_helper import JointTrajectory, MotionPlanHelper
from util.moveit_helper import MoveItHelper


class GraspMotion:
    def __init__(self, planner: MotionPlanHelper, moveit: MoveItHelper) -> None:
        self.planner = planner
        self.moveit = moveit

    def plan(self, start: np.ndarray, grasp: ReachableGrasp) -> dict[str, JointTrajectory] | None:
        segments = self.planner.plan_grasp(start, grasp.tool_pose, MotionConfig.APPROACH_DISTANCE, MotionConfig.LIFT_HEIGHT)
        return segments

    def execute(self, segments: dict[str, JointTrajectory], report: Callable[[str], None]) -> bool:
        """Approach, open, reach in, close, lift; stops at the first step that fails.
        The jaw opens only at the pre-grasp: opened at the old ready pose its finger swept a mustard bottle off
        its place (MuJoCo). Closed, the jaw stays inside the open jaw cuRobo planned the approach around."""
        steps = [
            ("接近（到抓取点后方）", lambda: self.run(segments["approach"])),
            ("张开夹爪", lambda: self.move_jaw(GripperConfig.JAW_OPEN)),
            ("水平伸到瓶身中段", lambda: self.run(segments["grasp"])),
            ("闭合夹爪", lambda: self.move_jaw(GripperConfig.JAW_CLOSE) and self.settle()),
            ("抬起", lambda: self.run(segments["lift"])),
        ]
        for label, step in steps:
            report(label)
            if not step():
                report(f"{label}失败")
                return False
        return True

    def move_jaw(self, target: float) -> bool:
        """Straight to the target in GripperConfig.JAW_MOVE_S, no planning: at the grasp the jaws sit
        against the object and the table edge, which MoveIt's start-state check calls a collision."""
        current = self.moveit.joint_positions()[GripperConfig.JAW_JOINT]
        steps = int(GripperConfig.JAW_MOVE_S / GripperConfig.JAW_STEP_S) + 1
        positions = np.linspace(current, target, steps)[:, None]
        moved = self.moveit.execute_trajectory(MotionConfig.GRIPPER_GROUP, [GripperConfig.JAW_JOINT], positions,
                                               GripperConfig.JAW_STEP_S)
        return moved

    def run(self, segment: JointTrajectory) -> bool:
        executed = self.moveit.execute_trajectory(MotionConfig.ARM_GROUP, segment.joint_names, segment.positions, segment.dt)
        return executed

    @staticmethod
    def settle() -> bool:
        time.sleep(MotionConfig.GRIP_SETTLE_S)
        settled = True
        return settled

    def go_ready(self) -> bool:
        """Arm to the SRDF 'ready' pose, jaw closed (as after a previous run it may hold anything)."""
        moved = self.moveit.move_to_named_state(MotionConfig.ARM_GROUP, "ready") and self.move_jaw(GripperConfig.JAW_CLOSE)
        return moved

    def arm_joints(self) -> np.ndarray:
        current = self.moveit.joint_positions()
        joints = np.array([current[name] for name in MotionConfig.ARM_JOINTS])
        return joints


def demo_motion() -> None:
    """Needs the bringup launch: plans and executes on the best reachable pudding-box grasp (mock hardware)."""
    from component.planning.reachability import GraspReachability
    from component.grasp_generation.service import GraspGeneration

    generation = GraspGeneration()
    candidates = generation.generate("water_bottle")
    planner = MotionPlanHelper()
    motion = GraspMotion(planner, MoveItHelper(node_name="grasp_motion_demo"))
    start = motion.arm_joints()
    reachable = GraspReachability(planner).filter(candidates, start)
    planned = [(grasp, motion.plan(start, grasp)) for grasp in reachable]
    logger.info("可规划：{}", [grasp.index for grasp, segments in planned if segments is not None])
    grasp, segments = next((grasp, segments) for grasp, segments in planned if segments is not None)
    logger.info("规划 #{}：{}", grasp.index, {name: len(segment.positions) for name, segment in segments.items()})
    done = motion.execute(segments, lambda text: logger.info("→ {}", text))
    logger.info("执行{}，末端关节 {}", "完成" if done else "失败", np.round(motion.arm_joints(), 3))


def main() -> None:
    use_utf8_output()
    demo_motion()


if __name__ == "__main__":
    main()
