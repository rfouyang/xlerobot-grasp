"""Fast-FoundationStereo 的薄封装：头部 D415 的左右红外图（已校正）→ 视差 → 深度（米），再对齐到彩色图。"""

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

from config.perception import HeadCameraConfig, PerceptionConfig

from config.settings import use_utf8_output


class StereoDepthHelper:
    """The repo runs from its checkout (PerceptionConfig.FFS_DIR), like LingBot-Depth's: its checkpoint is the whole
    pickled model, which needs the repo's core package to load. The D415's infrared pair is rectified in the sensor and
    shares one set of intrinsics; the emitter's dots stay on (they texture blank surfaces for the stereo too). Depth is
    computed in the left infrared camera and moved into the colour camera, where detection's masks are."""

    def __init__(self, weights: Path = PerceptionConfig.FFS_WEIGHTS, **kwargs) -> None:
        if str(PerceptionConfig.FFS_DIR) not in sys.path:
            sys.path.insert(0, str(PerceptionConfig.FFS_DIR))
        from core.utils.utils import InputPadder

        self.padder = InputPadder
        self.device = torch.device(kwargs.get("device", "cuda"))
        self.iters = kwargs.get("valid_iters", PerceptionConfig.FFS_VALID_ITERS)
        self.model = torch.load(str(weights), map_location="cpu", weights_only=False)
        self.model.args.valid_iters = self.iters
        self.model.args.max_disp = kwargs.get("max_disp", PerceptionConfig.FFS_MAX_DISP)
        self.model = self.model.to(self.device).eval()
        # Detection and the live preview share the model from two threads: one inference at a time.
        self.lock = threading.Lock()

    def disparity(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """left, right (H, W) uint8 infrared -> disparity (H, W) float32 px, in the left image."""
        height, width = left.shape
        images = [torch.from_numpy(np.repeat(image[..., None], 3, axis=2)).to(self.device).float()[None].permute(0, 3, 1, 2)
                  for image in (left, right)]
        padder = self.padder(images[0].shape, divis_by=32, force_square=False)
        images = padder.pad(*images)
        with self.lock, torch.no_grad(), torch.amp.autocast("cuda", enabled=True, dtype=torch.float16):
            disparity = self.model.forward(*images, iters=self.iters, test_mode=True, optimize_build_volume="pytorch1")
        disparity = padder.unpad(disparity.float()).cpu().numpy().reshape(height, width).clip(0, None)
        return disparity

    def depth(self, frame: dict) -> np.ndarray:
        """A head frame with the infrared pair (TeleopHelper.rgbd(infrared=True)) -> depth (H, W) float32 m aligned to
        its colour image, 0 where there is none (seen by the left camera only, or out of STEREO_RANGE_M)."""
        disparity = self.disparity(frame["ir_left"], frame["ir_right"])
        columns = np.arange(disparity.shape[1])[None, :]
        # A pixel whose match would fall off the right image's left edge has no partner.
        valid = (disparity > 0) & (columns - disparity >= 0)
        depth_ir = np.zeros_like(disparity)
        depth_ir[valid] = frame["K_ir"][0, 0] * float(frame["baseline_m"]) / disparity[valid] / HeadCameraConfig.STEREO_DEPTH_SCALE
        near, far = PerceptionConfig.STEREO_RANGE_M
        depth_ir[(depth_ir < near) | (depth_ir > far)] = 0.0
        depth = self.to_color(depth_ir, frame["K_ir"], frame["color_from_ir"], frame["K"], frame["color"].shape[:2])
        return depth

    @staticmethod
    def to_color(depth_ir: np.ndarray, k_ir: np.ndarray, color_from_ir: np.ndarray, k_color: np.ndarray, shape: tuple) -> np.ndarray:
        """Each infrared pixel's point into the colour camera, the nearest kept where several land on one pixel. Each
        point fills the four pixels around where it lands: the two cameras see the desk at about the same scale, so a
        single pixel each would leave a sieve of holes."""
        rows, cols = np.nonzero(depth_ir > 0)
        z = depth_ir[rows, cols]
        points = (np.linalg.inv(k_ir) @ np.stack([cols, rows, np.ones_like(cols)])) * z
        points = color_from_ir[:3, :3] @ points + color_from_ir[:3, 3:4]
        projected = k_color @ points
        u, v, z = projected[0] / projected[2], projected[1] / projected[2], points[2]
        height, width = shape
        nearest = np.full(height * width, np.inf, dtype=np.float32)
        for column in (np.floor(u), np.ceil(u)):
            for row in (np.floor(v), np.ceil(v)):
                inside = (column >= 0) & (column < width) & (row >= 0) & (row < height) & (z > 0)
                np.minimum.at(nearest, (row[inside] * width + column[inside]).astype(np.int64), z[inside].astype(np.float32))
        nearest[~np.isfinite(nearest)] = 0.0
        depth = nearest.reshape(height, width)
        return depth


def demo_stereo_depth_helper() -> None:
    """Needs the teleop service with the head's infrared pair (CameraStreamConfig.HEAD_INFRARED); reads only."""
    from util.teleop_helper import TeleopHelper

    helper = StereoDepthHelper()
    frame = TeleopHelper().rgbd(infrared=True)
    sensor = frame["depth"] * frame["depth_scale"]
    for _ in range(3):
        started = time.time()
        depth = helper.depth(frame)
        seconds = time.time() - started
    both = (sensor > 0) & (depth > 0)
    logger.info("FFS 深度有效 {:.0f}%（传感器 {:.0f}%），两者都有处差值中位 {:+.1f} mm，用时 {:.3f} s（预热后）", 100 * (depth > 0).mean(),
                100 * (sensor > 0).mean(), 1000 * float(np.median(depth[both] - sensor[both])), seconds)


def main() -> None:
    use_utf8_output()
    demo_stereo_depth_helper()


if __name__ == "__main__":
    main()
