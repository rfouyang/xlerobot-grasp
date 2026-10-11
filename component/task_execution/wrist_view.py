"""腕部相机看一眼的结果：画面、瓶身左边缘和宽度（像素）、每像素多少米，以及夹爪还要往左移多少。"""

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
class WristView:
    image: np.ndarray
    # Bottle body in the image: left edge column and width (pixels); None when no bottle was found.
    left_px: float | None
    width_px: float | None
    metres_per_px: float | None
    # How far the jaw still has to go left (m; negative: too far left already).
    shift_m: float | None


def demo_wrist_view() -> None:
    logger.info("WristView: {}", [field.name for field in fields(WristView)])


def main() -> None:
    use_utf8_output()
    demo_wrist_view()


if __name__ == "__main__":
    main()
