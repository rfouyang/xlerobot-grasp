"""选目标：在头部画面上点一下（SAM 3.1 分割，提示词无关的 YOLOE 叫出名字），或者说出名称（YOLOE 按名字找）；再看它
立在桌上哪里、哪只手够得着。也给抓取摆放找桌上的盒子，以及头部画面和桌面之间的换算。
选不成时抛 ValueError（说为什么）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from component.camera_calibration.check import head_camera_pose
from component.grasp_planning.arm_choice import ArmChoice
from config.perception import PerceptionConfig
from config.planning import ReachMapConfig, SelectionConfig
from config.settings import use_utf8_output
from config.task import PlaceConfig
from util.joint_mapping_helper import JointMappingHelper
from util.teleop_helper import TeleopHelper

# Nothing chosen on the page: tasks that take a default look for the nearest object of the first prompt (a bottle).
DEFAULT_TARGET = {"prompt": PerceptionConfig.PROMPTS[0], "point": None, "label": PerceptionConfig.PROMPTS[0],
                  "score": None, "default": True}


class TargetSelector:
    """target: prompt (what YOLOE looks for), point (u, v) in the head image or None, label / score as the model named
    it, by (which model), reach (which arm, or how far to move it). mask: the target's mask as last segmented (the
    click, then planning), for the page's head view."""

    def __init__(self, planning, teleop: TeleopHelper, choice: ArmChoice, gpu_lock: threading.Lock,
                 report: Callable[[str], None]) -> None:
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
        # The depth preview's fresh head frame (DepthPreview.fresh_frame, set by the application), or None.
        self.latest_frame: Callable[[], dict | None] | None = None

    # --- the head camera ---

    def head_frame(self) -> dict:
        """The head frame with planning's depth: the depth preview's newest when fresh (the task pages keep it going;
        this saves the ~2.4 s stereo), else a new one."""
        fresh = self.latest_frame() if self.latest_frame is not None else None
        frame = fresh if fresh is not None else self.planning.perception.head_frame()
        return frame

    def camera_pose(self, frame: dict) -> np.ndarray:
        """base_link from the head camera when frame was taken: the URDF at the arms' joints now and the head's in
        frame."""
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in motors.items()}
                for part, motors in status["joints"].items()}
        real["head"] = {"head_motor_1": float(frame["head_joints"][0]),
                        "head_motor_2": float(frame["head_joints"][1])}
        self.planning.urdf.update_cfg(self.mapping.to_urdf(real))
        pose = head_camera_pose(self.planning.urdf)
        return pose

    @staticmethod
    def project(base_from_camera: np.ndarray, frame: dict, xyz) -> list[int]:
        """A point in base_link -> its pixel (u, v) in frame."""
        camera_from_base = np.linalg.inv(base_from_camera)
        seen = frame["K"] @ (camera_from_base[:3, :3] @ np.asarray(xyz, float) + camera_from_base[:3, 3])
        uv = [round(float(seen[0] / seen[2])), round(float(seen[1] / seen[2]))]
        return uv

    def desk_point(self, uv: list[float]) -> tuple[float, float] | None:
        """A head-image pixel on the desk -> (x, y) in base_link: the camera ray through it meets the desk plane the
        desk guard measured (flat at DeskGuardConfig.DEFAULT_Z before the first plan); None when it does not."""
        frame = self.teleop.rgbd()
        base_from_camera = self.camera_pose(frame)
        ray = base_from_camera[:3, :3] @ (np.linalg.inv(frame["K"]) @ np.array([uv[0], uv[1], 1.0]))
        origin = base_from_camera[:3, 3]
        guard = self.planning.guard
        along = float(ray @ guard.normal)
        t = float((guard.origin - origin) @ guard.normal) / along if abs(along) > 1e-6 else -1.0
        if t <= 0:
            return None
        point = origin + t * ray
        xy = (float(point[0]), float(point[1]))
        return xy

    def image_point(self, xyz) -> list[int]:
        """A point in base_link -> its head-image pixel (u, v), at the head's pose now."""
        frame = self.teleop.rgbd()
        uv = self.project(self.camera_pose(frame), frame, xyz)
        return uv

    def on_desk(self, frame: dict, mask: np.ndarray) -> tuple[float, float, float] | None:
        """Roughly where an object stands (x, y of its axis in base_link) and how tall it is, from its mask: the median
        of its seen points, pushed back by a typical radius away from the camera (only its front is seen)."""
        base_from_camera = self.camera_pose(frame)
        points = self.planning.back_project(frame, base_from_camera, mask)
        points = points[points[:, 2] > ReachMapConfig.TABLE_Z_M + 0.01]
        if len(points) < 50:
            return None
        middle = np.median(points[:, :2], axis=0)
        away = middle - base_from_camera[:2, 3]
        axis = middle + 0.018 * away / max(np.linalg.norm(away), 1e-6)
        height = float(np.percentile(points[:, 2], 98)) - ReachMapConfig.TABLE_Z_M
        standing = (float(axis[0]), float(axis[1]), height)
        return standing

    def detect(self, rgb: np.ndarray, prompt: str, confidence: float = PerceptionConfig.CONFIDENCE) -> tuple[str, list]:
        """(prompt, detections) of a name; a phrase YOLOE does not know ("water bottle") is tried as its head noun
        ("bottle"), and the prompt is then that noun."""
        detector = self.planning.perception.detector
        detections = detector.detect(rgb, (prompt,), conf=confidence)
        if not detections and " " in prompt.strip():
            prompt = prompt.strip().split()[-1]
            detections = detector.detect(rgb, (prompt,), conf=confidence)
        found = (prompt, detections)
        return found

    def warm_up(self) -> None:
        """Loads the click's models (the prompt-free YOLOE, SAM 3.1) and runs each once, so the first click does not
        wait for them (~15 s). In the background at the service's start; a failure only means the first click loads
        them."""
        started = time.time()
        try:
            rgb = self.teleop.rgbd()["color"]
            with self.gpu_lock:
                self.prompt_free().candidates(rgb)
                if self.segmenter is not None:
                    self.segmenter.at(rgb, (rgb.shape[1] / 2, rgb.shape[0] / 2))
            logger.info("点选用的模型已预热（YOLOE + SAM）：{:.1f} s", time.time() - started)
        except Exception as error:
            logger.warning("点选模型预热没做成（第一次点选时再加载）：{}", error)

    def prompt_free(self):
        if self.picker is None:
            from util.prompt_free_helper import PromptFreeHelper

            self.picker = PromptFreeHelper()
        return self.picker

    # --- the object to grasp ---

    def clear(self) -> dict:
        """Back to no choice (the default bottle)."""
        self.target = dict(DEFAULT_TARGET)
        self.mask = None
        self.report("抓取目标：清除")
        return self.target

    def choose_at(self, point: list[float]) -> dict:
        """A click: the whole object under it (the prompt-free YOLOE, every object), segmented by SAM with that box and
        named by the YOLOE detection overlapping it most; that name is the prompt planning looks for."""
        with self.gpu_lock:
            # With the planning depth (Fast-FoundationStereo): where the object stands, for the reach check.
            frame = self.head_frame()
            candidates = self.prompt_free().candidates(frame["color"])
            hit = self.object_at(candidates, point)
            if hit is not None and not hit.mask[int(point[1]), int(point[0])]:
                # A click just beside an object: on to it (its mask's middle).
                rows, cols = np.nonzero(hit.mask)
                point = [float(cols.mean()), float(rows.mean())]
            segmented = None
            if self.segmenter is not None:
                segmented = self.segmenter.at(frame["color"], point, box=hit.box if hit is not None else None)
        if segmented is not None and segmented.mask.mean() > PerceptionConfig.PICK_MAX_AREA_SHARE:
            # A click on the desk or the mat: SAM takes the whole of it (2026-10-09).
            raise ValueError(f"点到的是桌面或垫子（占画面 {segmented.mask.mean() * 100:.0f}%），请点在物体上")
        named = (hit or self.name_of(candidates, segmented.mask)) if segmented is not None else hit
        if segmented is None and named is None:
            raise ValueError("点的位置没认出物体：点在物体中间，或者说出它的名字")
        found = segmented or named
        label = named.label if named is not None else "object"
        if named is None:
            self.report("SAM 分割出了物体，但 YOLOE 叫不出它的名字：腕部对准会找不到它")
        self.mask = found.mask
        self.target = {"prompt": label, "point": [round(point[0]), round(point[1])], "label": label,
                       "score": round(found.score, 2), "by": "SAM 3.1" if segmented is not None else "YOLOE"}
        self.add_reach(frame)
        self.report(f"抓取目标：{label}，位置 {tuple(self.target['point'])}")
        return self.target

    def choose_named(self, prompt: str, point: list[float] | None = None) -> dict:
        """A name (English for YOLOE, its text encoder is CLIP-like): the detection at point when one is given, else the
        nearest by depth (as planning takes it), found now so the page shows which one."""
        with self.gpu_lock:
            frame = self.head_frame()
            prompt, detection = self.find(frame, prompt.strip(), point)
        if detection is None:
            raise ValueError(f"头部画面里没找到「{prompt}」：换个说法，或者在画面里点它")
        rows, cols = np.nonzero(detection.mask)
        self.mask = detection.mask
        self.target = {"prompt": prompt, "point": [round(float(cols.mean())), round(float(rows.mean()))],
                       "label": prompt, "score": round(float(detection.score), 2), "by": "YOLOE"}
        self.add_reach(frame)
        self.report(f"抓取目标：{prompt}，位置 {tuple(self.target['point'])}")
        return self.target

    def add_reach(self, frame: dict) -> None:
        """Which arm reaches the target where its mask stands on the desk (said when none does)."""
        standing = self.on_desk(frame, self.mask)
        if standing is None:
            return
        self.target["reach"] = self.choice.advice(*standing, ReachMapConfig.TABLE_Z_M, self.choice.sides("pick_target"))
        if not self.target["reach"]["ok"]:
            self.report(self.target["reach"]["text"])

    @staticmethod
    def object_at(candidates: list, point: list[float]):
        """The object a click is on: the largest mask holding it (the whole bottle, not its cap, when both are found),
        else the one whose mask comes nearest, within PICK_RADIUS_PX; None if none is that close."""
        u, v = int(round(point[0])), int(round(point[1]))
        holding = [d for d in candidates if 0 <= v < d.mask.shape[0] and 0 <= u < d.mask.shape[1] and d.mask[v, u]]
        if holding:
            found = max(holding, key=lambda d: d.mask.sum())
            return found
        found, best_gap = None, PerceptionConfig.PICK_RADIUS_PX
        for detection in candidates:
            rows, cols = np.nonzero(detection.mask)
            if len(rows) == 0:
                continue
            gap = float(np.min(np.hypot(cols - u, rows - v)))
            if gap <= best_gap:
                found, best_gap = detection, gap
        return found

    @staticmethod
    def name_of(candidates: list, mask: np.ndarray):
        """The candidate overlapping mask most, by IoU, if at least PICK_NAME_IOU (what to call SAM's object)."""
        found, best_iou = None, PerceptionConfig.PICK_NAME_IOU
        for detection in candidates:
            iou = float((detection.mask & mask).sum() / max((detection.mask | mask).sum(), 1))
            if iou >= best_iou:
                found, best_iou = detection, iou
        return found

    def find(self, frame: dict, prompt: str, point: list[float] | None):
        """(prompt, detection) of a name: the one at point if given (else the one nearest it), else the nearest by
        depth; detection None when there is none."""
        prompt, detections = self.detect(frame["color"], prompt)
        if not detections:
            found = (prompt, None)
            return found
        if point is not None:
            u, v = int(point[0]), int(point[1])
            inside = [d for d in detections
                      if 0 <= v < d.mask.shape[0] and 0 <= u < d.mask.shape[1] and d.mask[v, u]]

            def centre(d):
                return np.hypot((d.box[0] + d.box[2]) / 2 - point[0], (d.box[1] + d.box[3]) / 2 - point[1])

            found = (prompt, min(inside, key=lambda d: d.mask.sum()) if inside else min(detections, key=centre))
            return found
        depth = frame["depth"] * frame["depth_scale"]
        measured = [(d, self.planning.perception.distance(depth, d.mask)) for d in detections]
        measured = [(d, distance) for d, distance in measured if distance is not None]
        if len(detections) > 1:
            self.report(f"「{prompt}」有 {len(detections)} 个，选了最近的")
        found = (prompt, min(measured, key=lambda item: item[1])[0] if measured else detections[0])
        return found

    def include(self, part: str | None = None) -> dict:
        """The chosen target misses a part of its object (the user: 瓶子上的盖子也是目标的一部分): SAM again with the
        target's box widened, upwards most (where a cap or lid is), the new mask joined to the old one, and that box
        kept for planning (perception.box). Refused when it would swallow the desk (more than PICK_MAX_AREA_SHARE)."""
        if self.mask is None or self.target.get("default"):
            raise ValueError("还没有选目标：先点一下物体，或者说出它的名字")
        part = part or "缺的部分"
        rows, cols = np.nonzero(self.mask)
        x1, y1, x2, y2 = float(cols.min()), float(rows.min()), float(cols.max()), float(rows.max())
        width, height = x2 - x1, y2 - y1
        with self.gpu_lock:
            frame = self.head_frame()
            image_w = frame["color"].shape[1]
            box = [max(0.0, x1 - PerceptionConfig.INCLUDE_GROW_SIDE * width),
                   max(0.0, y1 - PerceptionConfig.INCLUDE_GROW_UP * height),
                   min(image_w - 1.0, x2 + PerceptionConfig.INCLUDE_GROW_SIDE * width), y2]
            point = [float(cols.mean()), float(rows.mean())]
            found = self.segmenter.at(frame["color"], point, box=box) if self.segmenter is not None else None
        if found is None:
            raise ValueError("没有分割出更多的部分")
        joined = self.mask | found.mask
        if joined.mean() > PerceptionConfig.PICK_MAX_AREA_SHARE:
            raise ValueError("再扩大就把桌面也算进去了：在画面里点一下它缺的那部分试试")
        before, after = int(self.mask.sum()), int(joined.sum())
        rows, cols = np.nonzero(joined)
        self.mask = joined
        self.target["box"] = [round(float(cols.min())), round(float(rows.min())), round(float(cols.max())),
                              round(float(rows.max()))]
        self.target["parts"] = [*(self.target.get("parts") or []), part]
        self.add_reach(frame)
        self.report(f"目标加上了{part}：掩膜 {before} → {after} 像素，框 {self.target['box']}")
        return self.target

    def candidates(self, prompt: str) -> tuple[str, list[dict]]:
        """(prompt, objects): every object of the name in the head image now, left to right (the robot's left is the
        image's left), each {"point" (its mask's middle), "box", "score", "distance" (m, None if unknown)}; the prompt
        as YOLOE took it."""
        with self.gpu_lock:
            frame = self.head_frame()
            prompt, detections = self.detect(frame["color"], prompt)
        depth = frame["depth"] * frame["depth_scale"]
        objects = [{**self.item(detection), "distance": self.planning.perception.distance(depth, detection.mask)}
                   for detection in detections if detection.mask.any()]
        objects.sort(key=lambda item: item["point"][0])
        found = (prompt, objects)
        return found

    @staticmethod
    def item(detection) -> dict:
        rows, cols = np.nonzero(detection.mask)
        item = {"point": [round(float(cols.mean())), round(float(rows.mean()))],
                "box": [round(float(v)) for v in detection.box], "score": round(float(detection.score), 2)}
        return item

    # --- the boxes to put it in (抓取摆放) ---

    def boxes(self) -> list[dict]:
        """Every box (PlaceConfig.BOX_PROMPTS) in the head image now, left to right, each {"point", "box", "score",
        "right_reach" (its middle near enough the right shoulder, the only arm placing; None unknown), "aim" (the
        middle of its reachable part, {"xy", "uv"}, or None)}."""
        with self.gpu_lock:
            frame = self.head_frame()
            detections = self.planning.perception.detector.detect(frame["color"], PlaceConfig.BOX_PROMPTS,
                                                                  conf=PlaceConfig.BOX_CONFIDENCE)
        boxes = []
        shoulder = SelectionConfig.SHOULDER_XY
        for detection in detections:
            if not detection.mask.any():
                continue
            item = self.item(detection)
            standing = self.on_desk(frame, detection.mask)
            item["right_reach"] = None if standing is None else bool(
                np.hypot(standing[0] - shoulder[0], standing[1] - shoulder[1]) <= PlaceConfig.BOX_MAX_REACH_M)
            item["aim"] = self.box_aim(frame, detection.mask) if item["right_reach"] else None
            logger.info("盒子候选 {}（{:.2f}）：站在 {}，右手{}", item["point"], item["score"],
                        None if standing is None else tuple(round(v, 3) for v in standing),
                        {True: "够得到", False: "够不着", None: "不知道"}[item["right_reach"]])
            boxes.append(item)
        boxes.sort(key=lambda item: item["point"][0])
        return boxes

    def box_at(self, uv: list[float]) -> dict:
        """The box a click is in (the smallest frame around it), one the right arm reaches."""
        around = [box for box in self.boxes()
                  if box["box"][0] <= uv[0] <= box["box"][2] and box["box"][1] <= uv[1] <= box["box"][3]]
        if not around:
            raise ValueError("那里没看到盒子，点一下画面里的盒子")
        box = min(around, key=lambda box: (box["box"][2] - box["box"][0]) * (box["box"][3] - box["box"][1]))
        if box["right_reach"] is False:
            raise ValueError("这个盒子右手够不着，换一个，或者把它挪到机器人右前方近一点")
        return box

    def box_aim(self, frame: dict, mask: np.ndarray) -> dict | None:
        """The middle of the part of a box the right arm reaches, roughly (its seen points between REACH_BAND_M from
        the right shoulder), as {"xy", "uv"}: where the + goes for a box chosen by words; planning then takes the middle
        of the place points that really reach. None when no part is in the band."""
        base_from_camera = self.camera_pose(frame)
        points = self.planning.back_project(frame, base_from_camera, mask)
        points = points[points[:, 2] > ReachMapConfig.TABLE_Z_M - 0.02]
        near, far = PlaceConfig.REACH_BAND_M
        distance = np.hypot(points[:, 0] - SelectionConfig.SHOULDER_XY[0],
                            points[:, 1] - SelectionConfig.SHOULDER_XY[1])
        reached = points[(distance >= near) & (distance <= far)]
        if len(reached) < 50:
            return None
        xy = np.median(reached[:, :2], axis=0)
        floor = float(np.percentile(reached[:, 2], 10))
        aim = {"xy": [round(float(xy[0]), 3), round(float(xy[1]), 3)],
               "uv": self.project(base_from_camera, frame, [xy[0], xy[1], floor])}
        return aim


def demo_target_selector() -> None:
    """Needs the robot process (head camera): a click at the image centre, named and placed; the boxes (nothing
    moves)."""
    from component.grasp_planning.service import RealGraspPlanning

    teleop = TeleopHelper()
    selector = TargetSelector(RealGraspPlanning(teleop), teleop, ArmChoice(), threading.Lock(), logger.info)
    logger.info("{}", selector.choose_at([320.0, 300.0]))
    logger.info("{}", selector.boxes())


def main() -> None:
    use_utf8_output()
    demo_target_selector()


if __name__ == "__main__":
    main()
