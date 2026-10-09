"""饮料模型：矿泉水瓶、可乐罐、雪碧瓶，按真实尺寸由半径轮廓旋转生成，竖立、底面在 z=0。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import trimesh
from loguru import logger

from config.planning import SceneConfig

from config.settings import use_utf8_output

# (radius, height) outlines in metres, bottom centre to top centre.
PROFILES = {
    # 550 ml water bottle: 6.5 cm body, shoulder from 15 cm, 2.8 cm cap.
    "water_bottle": [(0.0, 0.0), (0.030, 0.0), (0.0325, 0.006), (0.0325, 0.150), (0.030, 0.163), (0.017, 0.183),
                     (0.014, 0.188), (0.014, 0.210), (0.0, 0.210)],
    # 330 ml can: 6.6 cm body, 12.2 cm tall, necked in at both ends.
    "coke_can": [(0.0, 0.0), (0.026, 0.0), (0.033, 0.008), (0.033, 0.110), (0.027, 0.120), (0.026, 0.122), (0.0, 0.122)],
    # 500 ml PET bottle: 6.6 cm body, longer shoulder, 22.5 cm tall.
    "sprite_bottle": [(0.0, 0.0), (0.028, 0.0), (0.033, 0.008), (0.033, 0.140), (0.031, 0.155), (0.018, 0.190),
                      (0.0145, 0.198), (0.0145, 0.225), (0.0, 0.225)],
}


class DrinkModels:
    def __init__(self, out_dir: Path = SceneConfig.OBJECT_DIR, **kwargs) -> None:
        self.out_dir = out_dir
        self.sections = kwargs.get("sections", 48)

    def build(self) -> list[Path]:
        paths = []
        for name, profile in PROFILES.items():
            mesh = trimesh.creation.revolve(np.array(profile), sections=self.sections)
            path = self.out_dir / f"{name}.obj"
            mesh.export(path)
            paths.append(path)
        return paths


def demo_drink_models() -> None:
    """Writes the real assets (generated, never hand-edited)."""
    for path in DrinkModels().build():
        mesh = trimesh.load(path, force="mesh")
        size = mesh.bounds[1] - mesh.bounds[0]
        logger.info("{}：{} cm，水密 {}，体积 {:.0f} ml", path.name, np.round(size * 100, 1), mesh.is_watertight,
                    mesh.volume * 1e6)


def main() -> None:
    use_utf8_output()
    demo_drink_models()


if __name__ == "__main__":
    main()
