"""YOLOE 开放词汇检测 + 分割的薄封装：文字提示 → 框、分数、掩膜。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import contextlib
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from config.perception import PerceptionConfig

from config.settings import PathConfig, use_utf8_output


@dataclass
class Detection:
    label: str
    score: float
    # x1, y1, x2, y2 in image pixels
    box: np.ndarray
    # (H, W) bool, image size
    mask: np.ndarray


class DetectionHelper:
    """ultralytics looks for the weights and fetches the text encoder (mobileclip2_b.ts) in the working directory,
    so loading and prompting run inside MODEL_DIR: everything stays in the project's model/yoloe."""

    def __init__(self, model_dir: Path = PerceptionConfig.MODEL_DIR, **kwargs) -> None:
        from ultralytics import YOLOE

        self.model_dir = model_dir
        with contextlib.chdir(model_dir):
            self.model = YOLOE(kwargs.get("weights", PerceptionConfig.WEIGHTS))
        self.prompts: tuple[str, ...] = ()

    def set_prompts(self, prompts: tuple[str, ...]) -> None:
        if tuple(prompts) != self.prompts:
            with contextlib.chdir(self.model_dir):
                self.model.set_classes(list(prompts), self.model.get_text_pe(list(prompts)))
            self.prompts = tuple(prompts)

    def detect(self, rgb: np.ndarray, prompts: tuple[str, ...] = PerceptionConfig.PROMPTS, **kwargs) -> list[Detection]:
        """rgb (H, W, 3) uint8; best first."""
        self.set_prompts(prompts)
        result = self.model.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), conf=kwargs.get("conf", PerceptionConfig.CONFIDENCE),
                                    retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return []
        masks = result.masks.data.cpu().numpy() > 0.5
        detections = [Detection(label=self.prompts[int(cls)], score=float(score), box=box, mask=mask)
                      for box, score, cls, mask in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                                       result.boxes.cls.cpu().numpy(), masks)]
        detections.sort(key=lambda detection: -detection.score)
        return detections


class PromptFreeHelper:
    """YOLOE's prompt-free model (PerceptionConfig.PICK_WEIGHTS): no prompts, its own vocabulary. at() names the object
    under a pixel."""

    def __init__(self, model_dir: Path = PerceptionConfig.MODEL_DIR, **kwargs) -> None:
        from ultralytics import YOLOE

        with contextlib.chdir(model_dir):
            self.model = YOLOE(kwargs.get("weights", PerceptionConfig.PICK_WEIGHTS))

    def candidates(self, rgb: np.ndarray) -> list[Detection]:
        """Every confident detection not larger than PICK_MAX_AREA_SHARE of the image (the desk, the mat and the room are
        found too)."""
        result = self.model.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), conf=PerceptionConfig.PICK_CONFIDENCE,
                                    retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return []
        masks = result.masks.data.cpu().numpy() > 0.5
        limit = PerceptionConfig.PICK_MAX_AREA_SHARE * masks.shape[1] * masks.shape[2]
        found = [Detection(label=self.model.names[int(cls)], score=float(score), box=box, mask=mask)
                 for box, score, cls, mask in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                                  result.boxes.cls.cpu().numpy(), masks) if mask.sum() <= limit]
        return found

    def name_of(self, rgb: np.ndarray, mask: np.ndarray) -> Detection | None:
        """The detection overlapping mask most, by IoU, if at least PICK_NAME_IOU (what to call SAM's object)."""
        best, best_iou = None, PerceptionConfig.PICK_NAME_IOU
        for detection in self.candidates(rgb):
            iou = float((detection.mask & mask).sum() / max((detection.mask | mask).sum(), 1))
            if iou >= best_iou:
                best, best_iou = detection, iou
        return best

    def at(self, rgb: np.ndarray, point: tuple[float, float]) -> Detection | None:
        """The smallest mask holding point (u, v) among the confident ones not larger than PICK_MAX_AREA_SHARE of the
        image (the desk, the mat and the room are found too), or None."""
        result = self.model.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), conf=PerceptionConfig.PICK_CONFIDENCE,
                                    retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return None
        u, v = int(round(point[0])), int(round(point[1]))
        masks = result.masks.data.cpu().numpy() > 0.5
        if not (0 <= v < masks.shape[1] and 0 <= u < masks.shape[2]):
            return None
        limit = PerceptionConfig.PICK_MAX_AREA_SHARE * masks.shape[1] * masks.shape[2]
        hits = [Detection(label=self.model.names[int(cls)], score=float(score), box=box, mask=mask)
                for box, score, cls, mask in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                                 result.boxes.cls.cpu().numpy(), masks)
                if mask[v, u] and mask.sum() <= limit]
        found = min(hits, key=lambda detection: detection.mask.sum()) if hits else None
        return found


def demo_detection_helper() -> None:
    """On a saved head image if one is given, else on the live head camera (needs the teleop service)."""
    if len(sys.argv) > 1:
        rgb = cv2.cvtColor(cv2.imread(sys.argv[1]), cv2.COLOR_BGR2RGB)
    else:
        from util.teleop_helper import TeleopHelper

        rgb = TeleopHelper().rgbd()["color"]
    helper = DetectionHelper()
    detections = helper.detect(rgb)
    out_dir = PathConfig.DEMO_DIR / "detection_helper"
    out_dir.mkdir(parents=True, exist_ok=True)
    view = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    for detection in detections:
        x1, y1, x2, y2 = detection.box.astype(int)
        view[detection.mask] = (0.5 * view[detection.mask] + (0, 110, 0)).astype(np.uint8)
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 220, 0), 2)
        logger.info("{} {:.2f} 框 {} 掩膜 {} 像素", detection.label, detection.score, detection.box.astype(int).tolist(), int(detection.mask.sum()))
    cv2.imwrite(str(out_dir / "detections.jpg"), view)
    logger.info("{} 个，图 {}", len(detections), out_dir / "detections.jpg")


def main() -> None:
    use_utf8_output()
    demo_detection_helper()


if __name__ == "__main__":
    main()
