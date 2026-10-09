"""抓取生成：把物体放上桌，采点云，出抓取候选；记住当前物体和候选供执行使用。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
from loguru import logger

from component.grasp_generation.grasp_prediction import GraspCandidate, GraspPrediction
from component.grasp_generation.tabletop_scene import PlacedObject, TabletopScene
from config.settings import use_utf8_output


class GraspGeneration:
    def __init__(self) -> None:
        self.scene = TabletopScene()
        self.prediction = GraspPrediction()
        self.placed: PlacedObject | None = None
        self.points = None
        self.candidates: list[GraspCandidate] = []

    def objects(self) -> list[dict]:
        catalog = self.scene.objects()
        return catalog

    def place(self, name: str) -> PlacedObject:
        self.placed = self.scene.place(name)
        return self.placed

    def settle(self, pose: np.ndarray) -> None:
        """Where the object actually came to rest (the simulator's pose): a bottle lying on its side rolls a
        little, and grasps computed for the dropped pose then miss it."""
        self.placed = self.scene.moved(self.placed, pose)

    def predict(self) -> list[GraspCandidate]:
        self.points = self.scene.surface_points(self.placed)
        self.candidates = self.prediction.predict(self.points, self.scene)
        return self.candidates

    def generate(self, name: str) -> list[GraspCandidate]:
        """Place and predict, for callers without a simulator."""
        self.place(name)
        candidates = self.predict()
        return candidates


def demo_grasp_generation() -> None:
    generation = GraspGeneration()
    candidates = generation.generate("water_bottle")
    logger.info("{}：{} 个候选，前 3 分数 {}", generation.placed.label, len(candidates),
                [round(c.score, 3) for c in candidates[:3]])


def main() -> None:
    use_utf8_output()
    demo_grasp_generation()


if __name__ == "__main__":
    main()
