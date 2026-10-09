"""机器人操作：读关节状态，把手臂 / 夹爪移到命名位姿。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.settings import MoveItConfig, use_utf8_output
from util.moveit_helper import MoveItHelper


class RobotOperation:
    def __init__(self, moveit: MoveItHelper) -> None:
        # Shared with grasp execution: one MoveItPy per process.
        self.moveit = moveit

    def joint_state(self) -> dict[str, float]:
        state = self.moveit.joint_positions()
        return state

    def poses(self) -> dict[str, dict]:
        """{"right": {"label": "右臂", "arm": [...], "gripper": [...]}, "left": {...}}"""
        poses = {}
        for arm, groups in MoveItConfig.ARMS.items():
            poses[arm] = {
                "label": MoveItConfig.ARM_LABELS[arm],
                "arm": self.moveit.named_states.get(groups["arm"], []),
                "gripper": self.moveit.named_states.get(groups["gripper"], []),
            }
        return poses

    def move(self, arm: str, part: str, pose: str) -> bool:
        """part is "arm" or "gripper"; blocks until the motion is done."""
        group = MoveItConfig.ARMS[arm][part]
        moved = self.moveit.move_to_named_state(group, pose)
        logger.info("{} {} -> {}：{}", arm, part, pose, "完成" if moved else "规划失败")
        return moved


def demo_robot_operation() -> None:
    """Needs the bringup launch running."""
    operation = RobotOperation(MoveItHelper(node_name="robot_operation_demo"))
    logger.info("位姿：{}", operation.poses())
    for arm, part, pose in (("right", "arm", "ready"), ("right", "gripper", "open"),
                            ("right", "gripper", "close"), ("right", "arm", "zero")):
        operation.move(arm, part, pose)
    logger.info("Jaw = {:.3f}", operation.joint_state()["Jaw"])


def main() -> None:
    use_utf8_output()
    demo_robot_operation()


if __name__ == "__main__":
    main()
