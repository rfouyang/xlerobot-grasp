"""Shared by the tests: a desk without a robot or models. Two bottles (the left one only the left arm reaches), two
boxes the right arm reaches, the real PlaceChoice over a stand-in selector, and a runner that chooses as TaskRunner
does (it refuses while running, with TaskBusyError)."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from component.place_choice.service import PlaceChoice
from component.task_execution.busy_error import TaskBusyError

BOTTLES = [{"point": [200, 300], "box": [180, 240, 220, 360], "score": 0.8, "distance": 0.55},
           {"point": [420, 300], "box": [400, 240, 440, 360], "score": 0.7, "distance": 0.45}]
BOXES = [{"point": [300, 200], "box": [260, 170, 340, 230], "score": 0.6, "right_reach": True,
          "aim": {"xy": [0.33, 0.02], "uv": [300, 210]}},
         {"point": [560, 260], "box": [520, 220, 600, 300], "score": 0.5, "right_reach": True,
          "aim": {"xy": [0.28, -0.08], "uv": [560, 270]}}]
DEFAULT = {"label": "bottle", "prompt": "bottle", "point": None, "default": True}


class FakeSelector:
    def __init__(self, boxes: list[dict] | None = None) -> None:
        self.boxes_seen = BOXES if boxes is None else boxes
        # Set to a CameraStaleError: the head camera gives no frames.
        self.camera_error: Exception | None = None

    def candidates(self, prompt: str) -> tuple[str, list[dict]]:
        found = ("bottle", BOTTLES if "bottle" in prompt else [])
        return found

    def boxes(self) -> list[dict]:
        if self.camera_error:
            raise self.camera_error
        return self.boxes_seen

    def box_at(self, uv: list[float]) -> dict:
        around = [box for box in self.boxes()
                  if box["box"][0] <= uv[0] <= box["box"][2] and box["box"][1] <= uv[1] <= box["box"][3]]
        if not around:
            raise ValueError("那里没看到盒子，点一下画面里的盒子")
        return around[0]

    def desk_point(self, uv: list[float]) -> tuple[float, float] | None:
        """Rows from 100 down are the desk; above them (the wall behind) no desk point."""
        if self.camera_error:
            raise self.camera_error
        xy = None if uv[1] < 100 else (round(0.9 - uv[1] / 1000, 3), round((320 - uv[0]) / 1000, 3))
        return xy


class FakeTeleop:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> dict:
        self.stops += 1
        return {"stopped": True}

    def rgbd(self) -> dict:
        import numpy as np

        return {"color": np.zeros((480, 640, 3), np.uint8)}


class FakeRunner:
    """TaskRunner's choosing (set_target, include_part, set_place, set_arm) and status on the stand-in desk."""

    def __init__(self, boxes: list[dict] | None = None) -> None:
        self.running = self.auto = False
        self.target = dict(DEFAULT)
        self.selector = FakeSelector(boxes)
        self.teleop = FakeTeleop()
        self.place = PlaceChoice(self.selector, lambda text: None)
        self.arm_override = None
        self.started: list[str] = []

    def ensure_idle(self) -> None:
        if self.running or self.auto:
            raise TaskBusyError("正在执行，等这一轮结束再说")

    def set_target(self, prompt=None, point=None, clear=False) -> dict:
        self.ensure_idle()
        if clear:
            self.target = dict(DEFAULT)
        else:
            if point is None:
                raise ValueError(f"头部画面里没找到「{prompt}」")
            arms = ["left"] if point[0] < 320 else ["right", "left"]
            self.target = {"label": prompt or "bottle", "prompt": prompt or "bottle", "point": point, "score": 0.8,
                           "by": "SAM 3.1", "reach": {"ok": True, "arms": arms, "text": ""}}
        result = {"target": self.target}
        return result

    def include_part(self, part) -> dict:
        self.ensure_idle()
        self.target = {**self.target, "parts": [part or "缺的部分"]}
        result = {"target": self.target}
        return result

    def set_place(self, kind, point=None, by="click", box=None) -> dict:
        self.ensure_idle()
        place = self.place
        if box is not None:
            status = place.box_by_words(box)
        elif point is None:
            status = place.choose(kind)
        elif kind == "spot":
            status = place.click_spot(point)
        else:
            status = place.click_box(point) if by == "click" else place.box_by_words_at(point)
        return status

    def set_arm(self, side: str) -> dict:
        self.ensure_idle()
        self.arm_override = None if side == "auto" else side
        result = {"arm": side}
        return result

    def status(self) -> dict:
        status = {"stages": [{"name": "prepare", "label": "准备"}], "next": 0, "running": self.running,
                  "auto": self.auto, "failed": False, "progress": [], "action": "pick_target", "target": self.target,
                  "arm_override": self.arm_override or "auto", **self.place.status()}
        return status

    def run(self, name: str) -> dict:
        self.ensure_idle()
        if name != "prepare":
            raise ValueError("下一步应是 prepare")
        result = {"done": True, "next": 1}
        return result

    def start(self, action: str, ignore_left: bool = False) -> dict:
        self.ensure_idle()
        self.started.append(action)
        result = {"started": True}
        return result


class FakeDialog:
    """VoiceDialog's look at the desk, without GPT."""

    chat = None

    def describe(self, jpeg: bytes) -> str:
        seen = "桌上左边和右边各有一个瓶子，中间后面和右边各有一个纸盒。"
        return seen
