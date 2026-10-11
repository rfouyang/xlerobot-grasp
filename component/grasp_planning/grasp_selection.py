"""抓取挑选：从 GraspGenX 的抓取里留下从侧面抓瓶身中段的，转到正对右肩、把瓶身放进夹爪的兜里，越靠中间越先试。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import replace

import numpy as np
import trimesh.transformations as tra
from loguru import logger

from component.grasp_generation.grasp_candidate import GraspCandidate
from component.grasp_generation.placed_object import PlacedObject
from component.grasp_planning.reachable_grasp import ReachableGrasp
from config.planning import GripperConfig, SelectionConfig
from config.settings import use_utf8_output


class GraspSelection:
    """The user asked for grasps on the middle of a standing drink: level, from the side, at mid-height.

    GraspGenX proposes the side grasps: their height and which side the fixed finger goes. Two things it gets
    wrong for this arm and jaw:
    - A 5-DOF arm can only approach along the vertical plane through its shoulder's yaw axis. A drink looks
      the same from every side, so each grasp is turned about the drink's axis to face the shoulder, and
      levelled (a pitched jaw no longer holds an upright body in its pocket).
    - It seats the drink's axis ~10 cm into the jaw, past the moving finger's tip (6.6 cm), so the closing
      finger pushes the drink out. The grasp is slid so the body sits in the jaw's pocket instead: against
      the fixed finger, just past the palm, where the moving finger wraps round its far side.
    """

    def __init__(self) -> None:
        self.max_tilt = np.sin(np.radians(SelectionConfig.MAX_TILT_DEG))

    def seat(self, candidates: list[GraspCandidate], placed: PlacedObject, **kwargs) -> list[GraspCandidate]:
        """Level, mid-height grasps, turned and seated; the most central first.

        side_gap: room between the drink and the fixed finger, which slides straight past it on the way in (the
        real robot needs more than the simulation: its drink is where the depth says to ~1 cm). raise_m: lifts every
        grasp after it is chosen (the real arm sags this much under its own weight). back_m: moves it back along
        the approach (the real jaw reaches further than the model, whose wrist camera mount has no collision shape).
        band: the share of the drink's height the grasp centre must lie in (default MIDDLE_BAND); highest_first:
        the highest grasps first instead of the most central (on the real robot the wrist ran along the table)."""
        bottom, top = placed.mesh.bounds[:, 2]
        axis_xy = placed.mesh.bounds.mean(axis=0)[:2]
        radius = (placed.mesh.bounds[1, 0] - placed.mesh.bounds[0, 0]) / 2
        # The drink's axis in the grasp frame (+X closing towards the fixed finger, +Z approach).
        pocket = np.array([GripperConfig.POCKET_FIXED_FACE_X - radius
                           - kwargs.get("side_gap", GripperConfig.POCKET_GAP), 0.0,
                           GripperConfig.POCKET_PALM_Z + radius + GripperConfig.POCKET_GAP, 1.0])
        toward = axis_xy - np.array(kwargs.get("shoulder_xy", SelectionConfig.SHOULDER_XY))
        low, high = kwargs.get("band", SelectionConfig.MIDDLE_BAND)
        preferred = top if kwargs.get("highest_first") else (bottom + top) / 2
        kept = []
        for candidate in candidates:
            # Tool z is the approach and tool x the closing axis: both must lie (near) level.
            if abs(candidate.pose[2, 2]) > self.max_tilt or abs(candidate.pose[2, 0]) > self.max_tilt:
                continue
            yaw = np.arctan2(toward[1], toward[0]) - np.arctan2(candidate.pose[1, 2], candidate.pose[0, 2])
            pose = tra.rotation_matrix(yaw, [0, 0, 1], [*axis_xy, 0.0]) @ candidate.pose
            # Then level: approach straight at the axis, closing axis flat, the fixed finger on GraspGenX's side.
            approach = np.array([*toward / np.linalg.norm(toward), 0.0])
            side = np.cross([0.0, 0.0, 1.0], approach)
            closing = side * np.sign(pose[:3, 0] @ side)
            pose[:3, :3] = np.column_stack([closing, np.cross(approach, closing), approach])
            center = pose @ pocket
            pose[:2, 3] += axis_xy - center[:2]
            if low <= (center[2] - bottom) / (top - bottom) <= high:
                pose[2, 3] += kwargs.get("raise_m", 0.0)
                pose[:3, 3] -= approach * kwargs.get("back_m", 0.0)
                kept.append((abs(center[2] - preferred), replace(candidate, pose=pose)))
        kept.sort(key=lambda item: item[0])
        seated = [candidate for _, candidate in kept]
        logger.info("从侧面抓中段的 {}/{} 个", len(seated), len(candidates))
        return seated

    def level(self, reachable: list[ReachableGrasp]) -> list[ReachableGrasp]:
        """What the IK reached must still be level: a pitched jaw no longer holds an upright drink in its pocket."""
        kept = [grasp for grasp in reachable if abs(grasp.tool_pose[2, 2]) <= self.max_tilt]
        return kept


def demo_grasp_selection() -> None:
    from component.grasp_generation.service import GraspGeneration
    from component.grasp_planning.reachability import GraspReachability
    from util.motion_plan_helper import MotionPlanHelper

    generation = GraspGeneration()
    reachability = GraspReachability(MotionPlanHelper())
    selection = GraspSelection()
    for name in ("water_bottle", "coke_can", "sprite_bottle"):
        seated = selection.seat(generation.generate(name), generation.placed)
        ordered = selection.level(reachability.filter(seated, np.array([-0.084, 1.43, 0.985, 1.741, 1.66])))
        logger.info("{}：可试 {} 个（编号, 指尖中心高度, 接近方向 z）{}", name, len(ordered), [
            (grasp.index, round(float(grasp.tool_pose[2, 3]), 3), round(float(grasp.tool_pose[2, 2]), 2))
            for grasp in ordered[:5]])


def main() -> None:
    use_utf8_output()
    demo_grasp_selection()


if __name__ == "__main__":
    main()
