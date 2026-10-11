"""规划时看到的真机场景：目标、瓶子的点云和拟合的圆柱、桌面，以及机器人此刻的关节。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, fields

import numpy as np
from loguru import logger

from component.grasp_generation.placed_object import PlacedObject
from component.grasp_generation.tabletop_scene import TabletopScene
from component.head_perception.target import Target
from config.settings import use_utf8_output


@dataclass
class RealScene:
    target: Target
    # Bottle points in base_link (the camera's side of it), and the cylinder fitted to them.
    points: np.ndarray
    placed: PlacedObject
    scene: TabletopScene
    # Every joint of the robot as it is now, in URDF radians.
    urdf_joints: dict[str, float]


def demo_real_scene() -> None:
    logger.info("RealScene: {}", [field.name for field in fields(RealScene)])


def main() -> None:
    use_utf8_output()
    demo_real_scene()


if __name__ == "__main__":
    main()
