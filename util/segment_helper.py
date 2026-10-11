"""SAM 3.1 的薄封装（经 Ultralytics）：头部图像上的一个点 → 那个物体的掩膜。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

import cv2
import numpy as np
from loguru import logger

from config.perception import PerceptionConfig
from config.settings import use_utf8_output
from util.detection_helper import Detection


class SegmentHelper:
    """Meta's own SAM 3 code wants torch 2.7+, this environment has 2.6; Ultralytics' port runs here and reads the
    SAM 3.1 checkpoint (its point-prompt head sits beside the multiplex tracker). The model (~2.4 GB on the GPU) loads
    on the first call; one inference at a time (the page's click and planning may come from two threads)."""

    def __init__(self, weights: Path = PerceptionConfig.SAM_WEIGHTS, **kwargs) -> None:
        self.weights = weights
        self.model = None
        self.lock = threading.Lock()

    def at(self, rgb: np.ndarray, point: tuple[float, float], label: str = "object", box=None) -> Detection | None:
        """The object under point (u, v): its mask, the mask's box and SAM's score, named label; None if SAM finds
        nothing there. box (x1, y1, x2, y2): the whole object's box as well, so SAM takes the whole of it, not the part
        the point is on (a cap, a label, a glint)."""
        with self.lock:
            if self.model is None:
                from ultralytics import SAM

                started = time.time()
                self.model = SAM(str(self.weights))
                logger.info("SAM 3.1 加载完：{:.1f} s", time.time() - started)
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            prompts = {"points": [[float(point[0]), float(point[1])]], "labels": [1]}
            if box is not None:
                prompts = {"bboxes": [[float(value) for value in box]], **prompts}
            try:
                result = self.model(image, **prompts, imgsz=PerceptionConfig.SAM_IMGSZ, verbose=False)[0]
            except Exception as error:
                if box is None:
                    raise
                # A model that takes a box or points but not both: the box alone (the whole object).
                logger.info("SAM 不收框加点（{}），只用框", error)
                result = self.model(image, bboxes=[[float(value) for value in box]], imgsz=PerceptionConfig.SAM_IMGSZ,
                                    verbose=False)[0]
        if result.masks is None or len(result.masks.data) == 0:
            return None
        mask = result.masks.data[0].cpu().numpy() > 0.5
        if not mask.any():
            return None
        rows, cols = np.nonzero(mask)
        score = float(result.boxes.conf[0]) if result.boxes is not None and len(result.boxes) else 1.0
        found = Detection(label=label, score=score,
                          box=np.array([cols.min(), rows.min(), cols.max(), rows.max()], float), mask=mask)
        return found


def demo_segment_helper() -> None:
    """Needs the teleop service; reads only. Segments what is at the head image's centre."""
    from util.teleop_helper import TeleopHelper

    rgb = TeleopHelper().rgbd()["color"]
    helper = SegmentHelper()
    point = (rgb.shape[1] / 2, rgb.shape[0] / 2)
    for _ in range(2):
        started = time.time()
        found = helper.at(rgb, point)
        seconds = time.time() - started
    logger.info("中心点 {}：{}，用时 {:.2f} s", point, "没分割出东西" if found is None else
                (f"掩膜 {int(found.mask.sum())} 像素，框 {found.box.astype(int).tolist()}，"
                 f"分数 {found.score:.2f}"), seconds)


def main() -> None:
    use_utf8_output()
    demo_segment_helper()


if __name__ == "__main__":
    main()
