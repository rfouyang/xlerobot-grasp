"""GraspGenX 给出的一个抓取候选：编号、base_link 里的夹爪位姿、分数。"""

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


@dataclass
class GraspCandidate:
    index: int
    # base_link <- canonical gripper frame (+Z approach, +X closing).
    pose: np.ndarray
    score: float


def demo_grasp_candidate() -> None:
    logger.info("GraspCandidate: {}", [field.name for field in fields(GraspCandidate)])


def main() -> None:
    use_utf8_output()
    demo_grasp_candidate()


if __name__ == "__main__":
    main()
