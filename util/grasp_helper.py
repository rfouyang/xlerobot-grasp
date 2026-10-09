"""GraspGenX 推理的薄封装：只靠扫掠体描述的夹爪，对物体点云生成 6-DoF 抓取。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import trimesh
from loguru import logger

from config.settings import GraspConfig, PathConfig, use_utf8_output
from graspgenx.grasp_server import GraspGenXSampler
from graspgenx.utils.checkpoint_io import load_model_cfg
from graspgenx.x_grippers import make_sweep_volume_gripper_info

GRIPPER_TYPES = {"parallel_2f": 0, "revolute_2f": 1, "revolute_3f": 2}


class GraspHelper:
    """The released model conditions on sweep volumes only (gripper_backbone sweep_volume_v2),
    so a gripper needs no GraspGenX asset folder: its config.json-style sweep boxes are enough."""

    def __init__(self, gripper_config: dict, checkpoint_dir: Path = GraspConfig.CHECKPOINT_DIR, **kwargs) -> None:
        sweep = gripper_config["sweep_volume"]
        gripper_info = make_sweep_volume_gripper_info(
            sweep["extents"], sweep["offset"], sweep["extents2"], sweep["offset2"],
            gripper_type=GRIPPER_TYPES[gripper_config["type"]],
            fingertip_depth=gripper_config["fingertip"][2],
            name=kwargs.get("name", "gripper"))
        model_cfg = load_model_cfg(str(checkpoint_dir / "gen"), str(checkpoint_dir / "dis"))
        self.sampler = GraspGenXSampler(model_cfg, gripper_info=gripper_info)
        self.num_grasps = kwargs.get("num_grasps", GraspConfig.NUM_GRASPS)
        self.topk_grasps = kwargs.get("topk_grasps", GraspConfig.TOPK_GRASPS)
        self.threshold = kwargs.get("threshold", GraspConfig.THRESHOLD)

    def predict(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """points (N, 3) in any frame -> grasp poses (M, 4, 4) in that frame and scores (M,), best first."""
        # The model expects a cloud centred at the origin.
        center = points.mean(axis=0)
        grasps, scores = GraspGenXSampler.run_inference(
            points - center, self.sampler, grasp_threshold=self.threshold, num_grasps=self.num_grasps,
            topk_num_grasps=self.topk_grasps, remove_outliers=False)
        grasps = grasps.cpu().numpy()
        scores = scores.cpu().numpy()
        grasps[:, :3, 3] += center
        order = np.argsort(-scores)
        grasps = grasps[order]
        scores = scores[order]
        return grasps, scores


def demo_grasp_helper() -> None:
    """Grasps on the water bottle with the SO-101 gripper description (run gripper_description.py first)."""
    import json

    config = json.loads((BASE_DIR / "asset" / "so101_gripper" / "config.json").read_text())
    helper = GraspHelper(config, name="xlerobot_so101")
    mesh = trimesh.load(BASE_DIR / "asset" / "objects" / "water_bottle.obj", force="mesh")
    points = np.asarray(trimesh.sample.sample_surface(mesh, GraspConfig.NUM_SAMPLE_POINTS, seed=0)[0])
    grasps, scores = helper.predict(points)
    approach = grasps[:, :3, 2]
    logger.info("{} 个抓取，分数 {:.3f}–{:.3f}；最好的接近方向 {}，位置 {}", len(grasps), scores.min(), scores.max(),
                np.round(approach[0], 2), np.round(grasps[0][:3, 3], 3))
    out_dir = PathConfig.DEMO_DIR / "grasp_helper"
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez(out_dir / "water_bottle_grasps.npz", grasps=grasps, scores=scores)


def main() -> None:
    use_utf8_output()
    demo_grasp_helper()


if __name__ == "__main__":
    main()
