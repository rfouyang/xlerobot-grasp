"""腕部相机对准：到瓶子后方之后，看右腕相机里瓶子左边缘离定指内侧面多远，算出整只夹爪要左移多少（肩部 pan）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from config.perception import PerceptionConfig

from config.settings import PathConfig, use_utf8_output

from config.task import WristAlignConfig
from util.detection_helper import DetectionHelper
from util.teleop_helper import TeleopHelper


@dataclass
class WristView:
    image: np.ndarray
    # Bottle body in the image: left edge column and width (pixels); None when no bottle was found.
    left_px: float | None
    width_px: float | None
    metres_per_px: float | None
    # How far the jaw still has to go left (m; negative: too far left already).
    shift_m: float | None


class WristAlignment:
    """The fixed finger's inner face runs forward along CONTACT_COLUMN_PX in the wrist image; the bottle's left edge
    must be SIDE_GAP_M right of it. The bottle's own width in the image (its diameter, from the head camera's fit)
    turns pixels into metres at the bottle's distance."""

    def __init__(self, teleop: TeleopHelper, detector: DetectionHelper, **kwargs) -> None:
        self.teleop = teleop
        self.detector = detector
        self.prompts = tuple(kwargs.get("prompts", PerceptionConfig.PROMPTS))
        # The grasping arm's wrist camera and its fixed finger's inner face (column, px); set for each grasp.
        self.camera = WristAlignConfig.CAMERA
        self.contact_column = WristAlignConfig.CONTACT_COLUMN_PX

    def look(self, radius: float) -> WristView:
        image = self.teleop.camera(self.camera)
        detections = self.detector.detect(image, self.prompts)
        if not detections:
            view = WristView(image=image, left_px=None, width_px=None, metres_per_px=None, shift_m=None)
            return view
        # Close up, the bottle is the biggest thing found.
        mask = max(detections, key=lambda detection: detection.mask.sum()).mask
        rows = np.nonzero(mask.any(axis=1))[0]
        lefts = np.array([np.argmax(mask[row]) for row in rows])
        rights = np.array([mask.shape[1] - 1 - np.argmax(mask[row][::-1]) for row in rows])
        widths = rights - lefts + 1
        body = widths >= WristAlignConfig.BODY_WIDTH_SHARE * widths.max()
        left_px = float(np.median(lefts[body]))
        width_px = float(np.median(widths[body]))
        metres_per_px = 2 * radius / width_px
        gap = (left_px - self.contact_column) * metres_per_px
        view = WristView(image=image, left_px=left_px, width_px=width_px, metres_per_px=metres_per_px,
                         shift_m=WristAlignConfig.SIDE_GAP_M - gap)
        return view

    @staticmethod
    def draw(view: WristView, path: Path) -> Path:
        """Red: the fixed finger's face; green: where the bottle's left edge should be; yellow: where it is."""
        image = cv2.cvtColor(view.image, cv2.COLOR_RGB2BGR)
        height = image.shape[0]
        contact = WristAlignConfig.CONTACT_COLUMN_PX
        cv2.line(image, (contact, 0), (contact, height), (0, 0, 255), 2)
        if view.left_px is not None:
            want = contact + WristAlignConfig.SIDE_GAP_M / view.metres_per_px
            cv2.line(image, (int(round(want)), 0), (int(round(want)), height), (0, 200, 0), 2)
            cv2.line(image, (int(round(view.left_px)), 0), (int(round(view.left_px)), height), (0, 220, 255), 2)
            cv2.putText(image, f"left {view.shift_m * 100:+.1f} cm", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), image)
        return path


def demo_wrist_alignment() -> None:
    """Needs the teleop service; only looks (the right arm can be anywhere the wrist camera sees the bottle)."""
    alignment = WristAlignment(TeleopHelper(), DetectionHelper())
    view = alignment.look(radius=0.017)
    path = alignment.draw(view, PathConfig.DEMO_DIR / "wrist_alignment" / "view.jpg")
    logger.info("瓶子左边缘 {} px，宽 {} px，还要左移 {}，图 {}", view.left_px, view.width_px,
                None if view.shift_m is None else f"{view.shift_m * 100:+.1f} cm", path)


def main() -> None:
    use_utf8_output()
    demo_wrist_alignment()


if __name__ == "__main__":
    main()
