"""选目标：在头部画面上点一下（SAM 3.1 分割，提示词无关的 YOLOE 叫出名字），或者输入名称（YOLOE 按名字找）；
再看它立在桌上哪里，哪只手够得着。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
from typing import Callable

import numpy as np
from loguru import logger

from component.calibration.check import head_camera_pose
from component.planning.arm_choice import ArmChoice
from config.perception import PerceptionConfig
from config.planning import ReachMapConfig
from config.settings import use_utf8_output
from util.joint_mapping_helper import JointMappingHelper
from util.teleop_helper import TeleopHelper

# Every task but the interactive grasp takes the nearest object of the first prompt (a bottle).
DEFAULT_TARGET = {"prompt": PerceptionConfig.PROMPTS[0], "point": None, "label": PerceptionConfig.PROMPTS[0], "score": None}


class TargetSelector:
    """target: prompt (what YOLOE looks for), point (u, v) in the head image or None, label / score as the model named
    it at the click, by (which model), reach (which arm, or how far to move it). mask: the target's mask as last
    segmented (the click, then planning), for the page's head view; None when chosen by name."""

    def __init__(self, planning, teleop: TeleopHelper, choice: ArmChoice, gpu_lock: threading.Lock, report: Callable[[str], None]) -> None:
        self.planning = planning
        self.teleop = teleop
        self.choice = choice
        self.gpu_lock = gpu_lock
        self.report = report
        self.mapping = JointMappingHelper()
        self.target = dict(DEFAULT_TARGET)
        self.mask: np.ndarray | None = None
        # The prompt-free YOLOE that names what a click is on (loaded on the first click), and SAM 3.1 that segments it
        # (also at planning and the later looks while the target has a point).
        self.picker = None
        self.segmenter = None
        if PerceptionConfig.SEGMENT_BY_POINT:
            from util.segment_helper import SegmentHelper

            self.segmenter = SegmentHelper()
        self.planning.perception.segmenter = self.segmenter

    def choose(self, prompt: str | None = None, point: list[float] | None = None, clear: bool = False) -> dict:
        """A click alone (point): the prompt-free YOLOE names what is there and that name is the prompt; a name (prompt),
        with or without a click: YOLOE looks for that name (English: its text encoder is CLIP-like), at the click if one
        is given, else the nearest. clear: back to the default bottle."""
        prompt = (prompt or "").strip()
        if clear:
            self.target = dict(DEFAULT_TARGET)
            self.mask = None
        elif point is not None and not prompt:
            with self.gpu_lock:
                # With the planning depth (Fast-FoundationStereo): where the object stands, for the reach check.
                frame = self.planning.perception.head_frame()
                if self.picker is None:
                    from util.detection_helper import PromptFreeHelper

                    self.picker = PromptFreeHelper()
                segmented = self.segmenter.at(frame["color"], point) if self.segmenter is not None else None
                if segmented is not None and segmented.mask.mean() > PerceptionConfig.PICK_MAX_AREA_SHARE:
                    # A click on the desk or the mat: SAM takes the whole of it (2026-10-09).
                    result = {"ok": False, "error": f"点到的是桌面或垫子（占画面 {segmented.mask.mean() * 100:.0f}%），请点在物体上"}
                    return result
                # SAM's object named by the YOLOE detection overlapping it most; without SAM, YOLOE's own at the click.
                named = self.picker.name_of(frame["color"], segmented.mask) if segmented is not None else self.picker.at(frame["color"], point)
            if segmented is None and named is None:
                result = {"ok": False, "error": "点的位置没认出物体：点在物体中间，或者输入名称"}
                return result
            label = named.label if named is not None else "object"
            self.mask = (segmented or named).mask
            self.target = {"prompt": label, "point": [round(point[0]), round(point[1])], "label": label,
                           "score": round((segmented or named).score, 2), "by": "SAM 3.1" if segmented is not None else "YOLOE"}
            if named is None:
                self.report("SAM 分割出了物体，但 YOLOE 叫不出它的名字：腕部对准会找不到它")
            standing = self.on_desk(frame, self.mask)
            if standing is not None:
                self.target["reach"] = self.choice.advice(*standing, ReachMapConfig.TABLE_Z_M, self.choice.sides("pick_target"))
                if not self.target["reach"]["ok"]:
                    self.report(self.target["reach"]["text"])
        elif prompt:
            self.mask = None
            self.target = {"prompt": prompt, "point": [round(point[0]), round(point[1])] if point is not None else None,
                           "label": prompt, "score": None, "by": "YOLOE"}
        else:
            result = {"ok": False, "error": "要么点一下物体，要么输入名称"}
            return result
        where = f"，位置 {tuple(self.target['point'])}" if self.target["point"] else "，取最近的"
        self.report(f"抓取目标：{self.target['label']}{where}")
        result = {"ok": True, "target": self.target}
        return result

    def choose_side(self, prompt: str, side: str) -> dict:
        """A name and a side of the desk ("left" / "right", the robot's): every detection of the name in the head image,
        the leftmost or rightmost one (the head camera looks forward, so the robot's left is the image's left), chosen
        by name at its centre."""
        with self.gpu_lock:
            rgb = self.teleop.rgbd()["color"]
            detections = self.planning.perception.detector.detect(rgb, (prompt,), conf=PerceptionConfig.CONFIDENCE)
            # A phrase YOLOE does not know ("water bottle") often works as its head noun ("bottle").
            if not detections and " " in prompt.strip():
                prompt = prompt.strip().split()[-1]
                detections = self.planning.perception.detector.detect(rgb, (prompt,), conf=PerceptionConfig.CONFIDENCE)
        if not detections:
            result = {"ok": False, "error": f"头部画面里没找到「{prompt}」"}
            return result
        centre = lambda detection: (detection.box[0] + detection.box[2]) / 2
        picked = min(detections, key=centre) if side == "left" else max(detections, key=centre)
        point = [float(centre(picked)), float((picked.box[1] + picked.box[3]) / 2)]
        self.report(f"{'左' if side == 'left' else '右'}边的「{prompt}」：{len(detections)} 个里选了 u {point[0]:.0f}")
        result = self.choose(prompt, point)
        return result

    def on_desk(self, frame: dict, mask: np.ndarray) -> tuple[float, float, float] | None:
        """Roughly where an object stands (x, y of its axis in base_link) and how tall it is, from its mask: the median of
        its seen points, pushed back by a typical radius away from the camera (only its front is seen)."""
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in status["joints"].items()}
        real["head"] = {"head_motor_1": float(frame["head_joints"][0]), "head_motor_2": float(frame["head_joints"][1])}
        self.planning.urdf.update_cfg(self.mapping.to_urdf(real))
        base_from_camera = head_camera_pose(self.planning.urdf)
        points = self.planning.back_project(frame, base_from_camera, mask)
        points = points[points[:, 2] > ReachMapConfig.TABLE_Z_M + 0.01]
        if len(points) < 50:
            return None
        middle = np.median(points[:, :2], axis=0)
        away = middle - base_from_camera[:2, 3]
        axis = middle + 0.018 * away / max(np.linalg.norm(away), 1e-6)
        height = float(np.percentile(points[:, 2], 98)) - ReachMapConfig.TABLE_Z_M
        return float(axis[0]), float(axis[1]), height


def demo_target_selector() -> None:
    """Needs the robot process (head camera): a click at the image centre, named and placed (nothing moves)."""
    from component.planning.grasp_planning import RealGraspPlanning

    teleop = TeleopHelper()
    selector = TargetSelector(RealGraspPlanning(teleop), teleop, ArmChoice(), threading.Lock(), lambda text: logger.info(text))
    logger.info("{}", selector.choose(point=[320.0, 300.0]))


def main() -> None:
    use_utf8_output()
    demo_target_selector()


if __name__ == "__main__":
    main()
