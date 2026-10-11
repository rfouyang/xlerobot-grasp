"""「腕部相机对准」这一步：夹爪闭着停在瓶子后方，转肩部 pan，直到腕部相机看到瓶子左边缘在定指右边 SIDE_GAP_M 处。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from component.task_execution.motion import ArmMotion
from component.task_execution.run_state import RunState
from component.task_execution.wrist_alignment import WristAlignment
from component.task_execution.wrist_view import WristView
from config.settings import PathConfig, use_utf8_output
from config.task import WristAlignConfig


class AlignStep:
    """Turning the shoulder by a moves the jaw sideways by a times the distance from the shoulder's yaw axis to the
    bottle (RunState.reach)."""

    def __init__(self, alignment: WristAlignment, motion: ArmMotion, gpu_lock: threading.Lock,
                 report: Callable[[str], None]) -> None:
        self.alignment = alignment
        self.motion = motion
        self.gpu_lock = gpu_lock
        self.report = report

    def run(self, state: RunState) -> bool:
        arm = self.motion.arm
        radius = state.radius()
        reach = state.reach(arm.shoulder_xy)
        for attempt in range(WristAlignConfig.MAX_TRIES + 1):
            view = self.look(radius)
            state.align_image = self.alignment.draw(view, PathConfig.OUTPUT_DIR / "real_grasp" / "align.jpg")
            if view.shift_m is None:
                self.report(f"{arm.label[0]}腕相机里没找到「{self.alignment.prompts[0]}」"
                            f"（看了 {WristAlignConfig.LOOKS} 次）")
                return False
            gap = WristAlignConfig.SIDE_GAP_M - view.shift_m
            state.align_info = {"gap_cm": round(gap * 100, 1), "shift_cm": round(view.shift_m * 100, 1)}
            self.report(f"{arm.label[0]}腕相机：瓶子左边缘离定指 {gap * 100:+.1f} cm，"
                        f"还要左移 {view.shift_m * 100:+.1f} cm")
            if abs(view.shift_m) <= WristAlignConfig.TOLERANCE_M:
                state.align_gap = gap
                self.report(f"已对准（肩部 pan 共修正 {np.degrees(self.motion.pan_offset):+.1f}°）")
                return True
            if attempt == WristAlignConfig.MAX_TRIES:
                break
            # Left is a smaller pan (real and URDF agree in sign).
            turn = -view.shift_m / reach
            if abs(np.degrees(self.motion.pan_offset + turn)) > WristAlignConfig.MAX_PAN_DEG:
                self.report(f"要修正 {np.degrees(self.motion.pan_offset + turn):+.1f}°，"
                            f"超过 {WristAlignConfig.MAX_PAN_DEG:.0f}°，停下")
                return False
            if not self.motion.turn(turn):
                return False
            time.sleep(WristAlignConfig.SETTLE_S)
        self.report(f"{WristAlignConfig.MAX_TRIES} 次没对准，停在瓶子后方")
        return False

    def look(self, radius: float) -> WristView:
        """The wrist camera's look, up to LOOKS times while it finds nothing: on 2026-10-09 it missed a bottle that the
        same picture showed at 0.84 when looked at again. Each look holds the GPU lock."""
        for look in range(WristAlignConfig.LOOKS):
            with self.gpu_lock:
                view = self.alignment.look(radius)
            if view.shift_m is not None:
                break
            logger.info("{}腕相机第 {} 次没找到 {}", self.motion.arm.label[0], look + 1, self.alignment.prompts)
            time.sleep(WristAlignConfig.LOOK_PAUSE_S)
        return view


def demo_align_step() -> None:
    """Needs the robot process and the GPU models (do not run it beside app/main.py): one look of the right wrist
    camera for a bottle of 3 cm radius; nothing moves."""
    from component.task_execution.service import TaskRunner
    from util.moveit_helper import MoveItHelper
    from util.teleop_helper import TeleopHelper

    runner = TaskRunner(MoveItHelper(node_name="align_step_demo"), TeleopHelper())
    view = runner.aligner.look(0.03)
    logger.info("还要左移：{}", view.shift_m)


def main() -> None:
    use_utf8_output()
    demo_align_step()


if __name__ == "__main__":
    main()
