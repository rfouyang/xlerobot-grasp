"""可达性：5 自由度右臂能到达哪些抓取（cuRobo IK，放开一个转动，见 MotionConfig.POSE_AXES_WEIGHT）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

import numpy as np
import trimesh.transformations as tra
from loguru import logger

from component.grasp_generation.grasp_prediction import GraspCandidate
from config.planning import MotionConfig
from config.settings import use_utf8_output
from util.motion_plan_helper import MotionPlanHelper


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


class GraspReachability:
    def __init__(self, planner: MotionPlanHelper) -> None:
        # Shared with the motion step: one cuRobo planner (and its GPU buffers) per process.
        self.planner = planner
        self.tcp = tra.translation_matrix(MotionConfig.TCP_OFFSET)

    def filter(self, candidates: list[GraspCandidate], start: np.ndarray) -> list[ReachableGrasp]:
        """Keeps the candidates' order (best score first)."""
        reachable = []
        for candidate in candidates:
            joints = self.planner.solve_ik(candidate.pose @ self.tcp, start)
            if joints is not None:
                tool_pose = self.planner.tool_pose(joints)
                reachable.append(ReachableGrasp(index=candidate.index, score=candidate.score, joints=joints,
                                                pose=tool_pose @ np.linalg.inv(self.tcp), tool_pose=tool_pose))
        logger.info("可达 {}/{}", len(reachable), len(candidates))
        return reachable


def demo_reachability() -> None:
    from component.grasp_generation.service import GraspGeneration

    generation = GraspGeneration()
    candidates = generation.generate("water_bottle")
    planner = MotionPlanHelper()
    reachability = GraspReachability(planner)
    reachable = reachability.filter(candidates, np.array([-0.084, 1.43, 0.985, 1.741, 1.66]))
    for grasp in reachable[:3]:
        original = candidates[grasp.index].pose @ reachability.tcp
        logger.info("#{} 分数 {:.3f}：指尖中心偏移 {:.1f} mm，接近方向转了 {:.1f}°", grasp.index, grasp.score,
                    1000 * np.linalg.norm(original[:3, 3] - grasp.tool_pose[:3, 3]),
                    np.degrees(np.arccos(np.clip(original[:3, 2] @ grasp.tool_pose[:3, 2], -1, 1))))


def main() -> None:
    use_utf8_output()
    demo_reachability()


if __name__ == "__main__":
    main()
