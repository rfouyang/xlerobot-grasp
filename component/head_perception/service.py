"""感知：真机头部 RGB-D → YOLOE 检测分割 → 选最近的那个作为目标 → 相机系三维位置，并画到遥操作页的头部画面上。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time

import cv2
import numpy as np
from loguru import logger

from component.head_perception.target import Target
from config.perception import PerceptionConfig
from config.settings import use_utf8_output
from util.depth_refine_helper import DepthRefineHelper
from util.detection_helper import Detection, DetectionHelper
from util.stereo_depth_helper import StereoDepthHelper
from util.teleop_helper import TeleopHelper


class Perception:
    """The prompt finds every bottle; the nearest one is the target (the spray bottle stands in front of the
    water bottle). Depth inside a mask is not the object everywhere — edges catch the background — so the
    distance is the median of the nearest half of the mask's depths."""

    def __init__(self, teleop: TeleopHelper, **kwargs) -> None:
        self.teleop = teleop
        self.detector = kwargs.get("detector") or DetectionHelper()
        self.prompts = tuple(kwargs.get("prompts", PerceptionConfig.PROMPTS))
        # source / refine as PerceptionConfig's DEPTH_SOURCE / DEPTH_REFINE;
        # changeable later when the models were given.
        self.source = kwargs.get("source", PerceptionConfig.DEPTH_SOURCE)
        self.refine = kwargs.get("refine", PerceptionConfig.DEPTH_REFINE)
        self.refiner = kwargs.get("refiner") or (DepthRefineHelper() if self.refine else None)
        # (u, v) in the head image: the detection there is the target instead of the nearest one (a click on the
        # interactive grasp page); None: the nearest.
        self.point: tuple[float, float] | None = None
        # The target's box as the user corrected it in the chat (a part added), or None: SAM then takes the name's box.
        self.box: tuple[float, float, float, float] | None = None
        # With a point, SAM 3.1 segments what is there instead of YOLOE looking for the prompt
        # (util.segment_helper; None: YOLOE always).
        self.segmenter = kwargs.get("segmenter")
        # The target's mask area (px) when last found (set after planning):
        # SAM's mask at the point is checked against it.
        self.point_area: int | None = None
        self.stereo = kwargs.get("stereo") or (StereoDepthHelper() if self.source == "stereo" else None)

    def head_frame(self) -> dict:
        """The head frame with the depth planning uses (see Target.frame); everything after (distance, table, bottle
        cylinder, the box, the upright check) reads this one depth."""
        frame = self.teleop.rgbd(infrared=self.source == "stereo")
        frame = self.with_depth(frame)
        return frame

    def with_depth(self, frame: dict) -> dict:
        sensor = frame["depth"] * frame["depth_scale"]
        started = time.time()
        raw = self.stereo.depth(frame) if self.source == "stereo" else sensor
        stereo_s = time.time() - started
        depth = self.refiner.refine(frame["color"], raw) if self.refine else raw
        logger.info("头部深度（{}{}）：有效 {:.0f}%（传感器 {:.0f}%），双目 {:.2f} s，共 {:.2f} s",
                    "FFS 双目" if self.source == "stereo" else "传感器", " + LingBot" if self.refine else "",
                    100 * (depth > 0).mean(), 100 * (sensor > 0).mean(), stereo_s, time.time() - started)
        frame = {**frame, "depth_sensor": sensor, "depth_raw": raw, "depth": depth, "depth_scale": 1.0}
        return frame

    @staticmethod
    def holding(detections: list, point: tuple[float, float]):
        """The largest detection whose mask holds point (u, v), else the one whose box centre is nearest it within
        PICK_RADIUS_PX; None if none."""
        u, v = int(round(point[0])), int(round(point[1]))
        inside = [d for d in detections if 0 <= v < d.mask.shape[0] and 0 <= u < d.mask.shape[1] and d.mask[v, u]]
        if inside:
            return max(inside, key=lambda d: d.mask.sum())
        def centre(d):
            return float(np.hypot((d.box[0] + d.box[2]) / 2 - u, (d.box[1] + d.box[3]) / 2 - v))

        near = [d for d in detections if centre(d) <= PerceptionConfig.PICK_RADIUS_PX]
        return min(near, key=centre) if near else None

    def locate(self, **kwargs) -> Target | None:
        frame = kwargs.get("frame") or self.head_frame()
        radius = PerceptionConfig.PICK_RADIUS_PX
        if self.point is not None and self.segmenter is not None:
            # The object of that name at the point gives SAM its whole box (the point alone gave a part: on 2026-10-09
            # the lower part of the spray bottle, read 9.4 cm tall instead of 15, every grasp then too low).
            named = self.detector.detect(frame["color"], self.prompts)
            hit = self.holding(named, self.point)
            box = self.box if self.box is not None else (hit.box if hit is not None else None)
            found = self.segmenter.at(frame["color"], self.point, self.prompts[0], box=box)
            share = None if found is None or not self.point_area else found.mask.sum() / self.point_area
            if (found is not None
                    and (share is None
                         or 1 / PerceptionConfig.TRACK_AREA_FACTOR <= share <= PerceptionConfig.TRACK_AREA_FACTOR)):
                detections = [found]
            else:
                logger.info("SAM 在目标位置分割出的不像目标（面积是原来的 {}），按名称 {} 在 {} px 内找",
                            "—" if share is None else f"{share:.2f} 倍", self.prompts[0],
                            PerceptionConfig.TRACK_RADIUS_PX)
                detections = named
                radius = PerceptionConfig.TRACK_RADIUS_PX
        else:
            detections = self.detector.detect(frame["color"], kwargs.get("prompts", self.prompts))
        depth = frame["depth"] * frame["depth_scale"]
        measured = [(detection, self.distance(depth, detection.mask)) for detection in detections]
        measured = [(detection, distance) for detection, distance in measured if distance is not None]
        target = None
        chosen = (self.at_point(measured, radius) if self.point is not None
                  else (min(measured, key=lambda item: item[1]) if measured else None))
        if chosen is not None:
            detection, distance = chosen
            rows, cols = np.nonzero(detection.mask)
            pixel = np.array([cols.mean(), rows.mean(), 1.0])
            point = distance * (np.linalg.inv(frame["K"]) @ pixel)
            target = Target(detection=detection, distance=distance, point_camera=point, frame=frame)
        self.show(measured, target)
        return target

    def at_point(self, measured: list[tuple[Detection, float]],
                 radius: float = PerceptionConfig.PICK_RADIUS_PX) -> tuple[Detection, float] | None:
        """The detection whose mask holds self.point (the smallest such), else the one whose mask comes nearest to it
        within radius (px); None if none does."""
        u, v = self.point
        best = None
        for detection, distance in measured:
            mask = detection.mask
            row, col = int(round(v)), int(round(u))
            if 0 <= row < mask.shape[0] and 0 <= col < mask.shape[1] and mask[row, col]:
                gap = 0.0
            else:
                rows, cols = np.nonzero(mask)
                gap = float(np.min(np.hypot(cols - u, rows - v)))
            key = (gap, int(mask.sum()))
            if gap <= radius and (best is None or key < best[0]):
                best = (key, detection, distance)
        chosen = None if best is None else (best[1], best[2])
        return chosen

    @staticmethod
    def distance(depth: np.ndarray, mask: np.ndarray) -> float | None:
        values = np.sort(depth[mask & (depth > 0)])
        if values.size < 20:
            return None
        near = values[: max(1, int(values.size * PerceptionConfig.NEAR_FRACTION))]
        distance = float(np.median(near))
        return distance

    def show(self, measured: list[tuple[Detection, float]], target: Target | None) -> None:
        """Every detection on the teleop page's head view; the target green."""
        items = []
        for detection, distance in measured:
            contours, _ = cv2.findContours(detection.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            outline = max(contours, key=cv2.contourArea)
            polygon = cv2.approxPolyDP(outline, PerceptionConfig.POLYGON_EPSILON_PX, True).reshape(-1, 2)
            items.append({"label": detection.label, "score": round(detection.score, 3),
                          "box": [round(float(v), 1) for v in detection.box], "polygon": polygon.tolist(),
                          "distance_m": round(distance, 3),
                          "target": target is not None and detection is target.detection})
        self.teleop.overlay("YOLOE " + " / ".join(self.prompts), items)


def demo_perception() -> None:
    """Needs the teleop service (the head should look at the desk: the grasp position). Reads only; the result is
    drawn on the teleop page's head view."""
    perception = Perception(TeleopHelper())
    started = time.time()
    target = perception.locate()
    if target is None:
        logger.warning("没找到目标（提示 {}）", perception.prompts)
        return
    logger.info("目标：{} {:.2f}，框 {}，距离 {:.3f} m，相机系 {} m，头部关节 {}，用时 {:.2f} s",
                target.detection.label, target.detection.score, target.detection.box.astype(int).tolist(),
                target.distance, np.round(target.point_camera, 3),
                np.round(target.frame["head_joints"], 1), time.time() - started)


def main() -> None:
    use_utf8_output()
    demo_perception()


if __name__ == "__main__":
    main()
