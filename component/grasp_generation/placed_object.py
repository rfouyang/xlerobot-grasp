"""放到桌上的一个物体：名字、网格（已在 base_link 里）、它的位姿、文件、缩放、颜色和质量。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import trimesh
from loguru import logger

from config.settings import use_utf8_output


@dataclass
class PlacedObject:
    name: str
    label: str
    # Mesh already moved into the robot's base_link frame.
    mesh: trimesh.Trimesh
    # base_link <- mesh file frame: what a simulator needs to put the same object down.
    pose: np.ndarray
    file: Path
    scale: float
    rgba: tuple
    mass: float


def demo_placed_object() -> None:
    logger.info("PlacedObject: {}", [field.name for field in fields(PlacedObject)])


def main() -> None:
    use_utf8_output()
    demo_placed_object()


if __name__ == "__main__":
    main()
