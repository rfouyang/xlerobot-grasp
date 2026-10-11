"""LingBot-Depth 的薄封装：RGB + 传感器原始深度（米）→ 补全、修正后的深度（米）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

import numpy as np
import torch
from loguru import logger

from config.perception import PerceptionConfig
from config.settings import use_utf8_output


class DepthRefineHelper:
    """The repo runs from its checkout (PerceptionConfig.LINGBOT_DIR), not installed: its package pins torch and pulls
    in xformers, which this environment leaves out. Without xformers its transformer blocks refuse the list of token
    sequences it builds (one per image); one image is one sequence, so the list is joined into a tensor first."""

    def __init__(self, weights: Path = PerceptionConfig.LINGBOT_WEIGHTS, **kwargs) -> None:
        if str(PerceptionConfig.LINGBOT_DIR) not in sys.path:
            sys.path.insert(0, str(PerceptionConfig.LINGBOT_DIR))
        from mdm.model.dinov2_rgbd.models.vision_transformer import DinoVisionTransformer
        from mdm.model.v2 import MDMModel

        self.join_single_sequence(DinoVisionTransformer)
        self.device = torch.device(kwargs.get("device", "cuda"))
        self.model = MDMModel.from_pretrained(str(weights)).to(self.device).eval()
        self.level = kwargs.get("resolution_level", PerceptionConfig.LINGBOT_RESOLUTION_LEVEL)
        # Detection and the live preview share the model from two threads: one inference at a time.
        self.lock = threading.Lock()

    @staticmethod
    def join_single_sequence(transformer: type) -> None:
        prepare = transformer.prepare_tokens_with_masks
        if getattr(prepare, "joined", False):
            return

        def joined(self, *args, **kwargs):
            tokens = prepare(self, *args, **kwargs)
            sequence = torch.cat(tokens, dim=0) if isinstance(tokens, list) and len(tokens) == 1 else tokens
            return sequence

        joined.joined = True
        transformer.prepare_tokens_with_masks = joined

    def refine(self, color: np.ndarray, depth: np.ndarray) -> np.ndarray:
        """color (H, W, 3) RGB uint8, depth (H, W) m with 0 where the sensor has none -> depth (H, W) float32 m, 0 where
        the model gives no answer."""
        image = torch.from_numpy(color).to(self.device).permute(2, 0, 1)[None].float() / 255.0
        depth_in = torch.from_numpy(depth.astype(np.float32)).to(self.device)[None]
        with self.lock:
            output = self.model.infer(image, depth_in=depth_in, resolution_level=self.level)
            refined = output["depth"][0].float().cpu().numpy()
        refined[~np.isfinite(refined)] = 0.0
        return refined


def demo_depth_refine_helper() -> None:
    """Needs the teleop service (the head should look at the desk: the grasp pose); reads only."""
    from util.teleop_helper import TeleopHelper

    helper = DepthRefineHelper()
    frame = TeleopHelper().rgbd()
    raw = frame["depth"] * frame["depth_scale"]
    started = time.time()
    refined = helper.refine(frame["color"], raw)
    logger.info("深度有效 {:.0f}% → {:.0f}%，用时 {:.2f} s（第一次含预热）", 100 * (raw > 0).mean(),
                100 * (refined > 0).mean(), time.time() - started)


def main() -> None:
    use_utf8_output()
    demo_depth_refine_helper()


if __name__ == "__main__":
    main()
