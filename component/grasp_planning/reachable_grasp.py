"""一个够得到的抓取：候选的编号和分数、手臂的 IK 解，以及手臂实际到达的抓取位姿
（显示用的夹爪坐标系和规划用的工具坐标系）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, fields

import numpy as np
from loguru import logger

from config.settings import use_utf8_output


@dataclass
class ReachableGrasp:
    index: int
    score: float
    # IK solution for the arm joints.
    joints: np.ndarray
    # The grasp the arm actually reaches: the candidate turned about the free axis through the
    # fingertips, so the contacts stay where they were. Canonical gripper frame, for display.
    pose: np.ndarray
    # The same, as the planner's tool frame (between the fingertips): what motion planning targets.
    tool_pose: np.ndarray


def demo_reachable_grasp() -> None:
    logger.info("ReachableGrasp: {}", [field.name for field in fields(ReachableGrasp)])


def main() -> None:
    use_utf8_output()
    demo_reachable_grasp()


if __name__ == "__main__":
    main()
