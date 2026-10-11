"""桌上一个开口朝上的盒子（或当作没有盒壁的桌面点）：底面在 base_link 里的矩形，以及盒沿、盒底和桌面的高度。"""

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
class OpenBox:
    # The walls' footprint in base_link: centre (x, y), unit long axis (x, y), half sizes (along, across), outside.
    centre: np.ndarray
    long_axis: np.ndarray
    half: np.ndarray
    # Absolute heights (m): the rim, the floor inside, the table it stands on.
    rim_z: float
    floor_z: float
    table_z: float


def demo_open_box() -> None:
    logger.info("OpenBox: {}", [field.name for field in fields(OpenBox)])


def main() -> None:
    use_utf8_output()
    demo_open_box()


if __name__ == "__main__":
    main()
