"""YOLOE 的无提示词模型：不用提示词，用它自己的词表；at() 说出某个像素上是什么物体。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import contextlib
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from config.perception import PerceptionConfig
from config.settings import use_utf8_output
from util.detection_helper import Detection


class PromptFreeHelper:
    """YOLOE's prompt-free model (PerceptionConfig.PICK_WEIGHTS): no prompts, its own vocabulary. at() names the object
    under a pixel."""

    def __init__(self, model_dir: Path = PerceptionConfig.MODEL_DIR, **kwargs) -> None:
        from ultralytics import YOLOE

        with contextlib.chdir(model_dir):
            self.model = YOLOE(kwargs.get("weights", PerceptionConfig.PICK_WEIGHTS))

    def candidates(self, rgb: np.ndarray) -> list[Detection]:
        """Every confident detection not larger than PICK_MAX_AREA_SHARE of the image (the desk, the mat and the room
        are found too)."""
        result = self.model.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), conf=PerceptionConfig.PICK_CONFIDENCE,
                                    retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return []
        masks = result.masks.data.cpu().numpy() > 0.5
        limit = PerceptionConfig.PICK_MAX_AREA_SHARE * masks.shape[1] * masks.shape[2]
        found = [Detection(label=self.model.names[int(cls)], score=float(score), box=box, mask=mask)
                 for box, score, cls, mask in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                                  result.boxes.cls.cpu().numpy(), masks, strict=False)
                 if mask.sum() <= limit]
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
                                                 result.boxes.cls.cpu().numpy(), masks, strict=False)
                if mask[v, u] and mask.sum() <= limit]
        found = min(hits, key=lambda detection: detection.mask.sum()) if hits else None
        return found


def demo_prompt_free_helper() -> None:
    import numpy as np

    helper = PromptFreeHelper()
    rgb = np.zeros((480, 640, 3), np.uint8)
    logger.info("空白画面：{} 个物体", len(helper.candidates(rgb)))


def main() -> None:
    use_utf8_output()
    demo_prompt_free_helper()


if __name__ == "__main__":
    main()
