"""一轮真机任务里步骤之间传的东西：规划看到的场景和抓取、放到哪、对准的结果、每步用时。重置时换一份新的。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, field

import numpy as np
from loguru import logger

from component.grasp_planning.reachable_grasp import ReachableGrasp
from component.grasp_planning.real_scene import RealScene
from config.settings import use_utf8_output


@dataclass
class RunState:
    """Filled by the plan step (plan_step.py) and the alignment (align_step.py); read by later steps and the page."""

    scene: RealScene | None = None
    grasp: ReachableGrasp | None = None
    # The jaw as it was before the run (URDF), where return and home put it back.
    start_jaw: float = 0.0
    # Between the bottle and the fixed finger at the alignment's last look (m).
    align_gap: float = 0.0
    # Set when the old right-arm "grasp" stopped at planning because the bottle stands too far left: the bottle's
    # axis y and how far the move would take it right (m).
    too_left: dict | None = None
    # How far the shift step turns the bottle right (m).
    shift_m: float = 0.0
    # Where the plan puts the bottle, in the head image (the upright check looks there afterwards).
    placed_uv: list[int] | None = None
    plan_image: Path | None = None
    align_image: Path | None = None
    # Each finished step's duration (s), and the wrist camera's last look at the alignment (cm), for the page.
    stage_seconds: dict[str, float] = field(default_factory=dict)
    align_info: dict | None = None

    def radius(self) -> float:
        """The planned object's radius (m)."""
        bounds = self.scene.placed.mesh.bounds
        radius = float((bounds[1, 0] - bounds[0, 0]) / 2)
        return radius

    def reach(self, shoulder_xy: tuple[float, float]) -> float:
        """From the shoulder's yaw axis to the bottle's axis (m): a pan turn moves the jaw sideways by this much
        per radian."""
        axis = self.scene.placed.mesh.bounds.mean(axis=0)[:2]
        reach = float(np.linalg.norm(axis - np.array(shoulder_xy)))
        return reach


def demo_run_state() -> None:
    """A fresh run's state (nothing planned yet)."""
    state = RunState()
    logger.info("新的一轮：{}", state)


def main() -> None:
    use_utf8_output()
    demo_run_state()


if __name__ == "__main__":
    main()
