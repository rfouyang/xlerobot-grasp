"""抓取预测：GraspGenX 对物体点云出抓取，去掉夹爪会插进桌面的那些。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

import numpy as np
import trimesh
from loguru import logger

from component.grasp_generation.gripper_description import GripperDescription
from component.grasp_generation.tabletop_scene import TabletopScene
from config.planning import GripperConfig
from config.settings import use_utf8_output
from util.grasp_helper import GraspHelper


@dataclass
class GraspCandidate:
    index: int
    # base_link <- canonical gripper frame (+Z approach, +X closing).
    pose: np.ndarray
    score: float


class GraspPrediction:
    def __init__(self, **kwargs) -> None:
        self.gripper = GripperDescription()
        self.gripper_config = self.gripper.load_config()
        self.helper = GraspHelper(self.gripper_config, name=GripperConfig.NAME)
        # A few hundred vertices of the open gripper are enough to tell whether it dips into the table.
        coll_mesh = trimesh.load(GripperConfig.DIR / "coll_mesh.obj", force="mesh")
        self.gripper_points = np.asarray(trimesh.sample.sample_surface(coll_mesh, kwargs.get("gripper_points", 400), seed=0)[0])

    def predict(self, points: np.ndarray, scene: TabletopScene) -> list[GraspCandidate]:
        """points in base_link -> candidates in base_link, best first, none of them through the table."""
        poses, scores = self.helper.predict(points)
        candidates = []
        for pose, score in zip(poses, scores):
            gripper_points = self.gripper_points @ pose[:3, :3].T + pose[:3, 3]
            if not scene.below_table(gripper_points):
                candidates.append(GraspCandidate(index=len(candidates), pose=pose, score=float(score)))
        logger.info("GraspGenX {} 个抓取，去掉穿桌的后剩 {} 个", len(poses), len(candidates))
        return candidates


def demo_grasp_prediction() -> None:
    scene = TabletopScene()
    prediction = GraspPrediction()
    for name in ("water_bottle", "coke_can"):
        placed = scene.place(name)
        candidates = prediction.predict(scene.surface_points(placed), scene)
        approach_z = np.array([c.pose[2, 2] for c in candidates])
        logger.info("{}：{} 个，最高分 {:.3f}；接近方向朝下（z<-0.5）的 {} 个", placed.label, len(candidates),
                    candidates[0].score, int((approach_z < -0.5).sum()))


def main() -> None:
    use_utf8_output()
    demo_grasp_prediction()


if __name__ == "__main__":
    main()
