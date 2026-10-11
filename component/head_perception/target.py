"""头部画面里找到的目标：检测结果、距离、相机坐标系里的中心点，以及它来自的那一帧 RGB-D。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, fields

import numpy as np
from loguru import logger

from config.settings import use_utf8_output
from util.detection_helper import Detection


@dataclass
class Target:
    detection: Detection
    # Median depth of the near half of the mask (m), and the mask's centre at that depth in the camera frame
    # (x right, y down, z forward, m).
    distance: float
    point_camera: np.ndarray
    # The head RGB-D frame it came from (color, depth, K, depth_scale, head_joints, time). Its depth is what planning
    # uses (float m, depth_scale 1.0): DEPTH_SOURCE's, refined by LingBot-Depth with DEPTH_REFINE; depth_raw is
    # DEPTH_SOURCE's before refining, depth_sensor the D415's own.
    frame: dict


def demo_target() -> None:
    logger.info("Target: {}", [field.name for field in fields(Target)])


def main() -> None:
    use_utf8_output()
    demo_target()


if __name__ == "__main__":
    main()
