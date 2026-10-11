"""右臂在桌面上哪里能做水平侧抓：按格子算一张可达图，给点选的物体判断够不够得着、该往哪挪，并投到头部画面上。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import hashlib
import json
import threading
import time

import cv2
import numpy as np
import yourdfpy
from loguru import logger
from scipy.optimize import least_squares

from component.camera_calibration.check import head_camera_pose
from component.grasp_planning.arm import RIGHT, Arm
from config.planning import MotionConfig, ReachMapConfig, RealPlanningConfig
from config.settings import PathConfig, use_utf8_output
from util.joint_mapping_helper import JointMappingHelper


class ReachMap:
    """A grasp here is the planner's: the jaw level (its approach and closing axes horizontal), the approach any
    direction, the tool at the grasp height and again APPROACH_DISTANCE back along the approach (the pre-grasp), the
    real wrist roll inside ROLL_RANGE_DEG. Solved by least squares on the URDF (no collisions, no sag): close to the
    planner's own reachability filter, which is what refused a bottle at (0.278, 0.13) on 2026-10-09 while one at
    (0.287, 0.005) planned. The grid over the desk is solved once per (table height, grasp height, settings) and kept
    on disk; its own URDF copy, so the planner's is never touched from another thread."""

    def __init__(self, arm: Arm = RIGHT, **kwargs) -> None:
        self.arm = arm
        self.urdf = yourdfpy.URDF.load(str(PathConfig.URDF_PATH), load_meshes=False, build_scene_graph=True)
        self.mapping = JointMappingHelper()
        self.limits = np.array([[self.urdf.joint_map[joint].limit.lower, self.urdf.joint_map[joint].limit.upper]
                                for joint in arm.joints])
        # The right arm's planned grasps as IK starts; for the left arm also with the pan mirrored. The roll stays: the
        # left arm is a copy of the right (its fixed finger on the left too), and a level jaw also solves upside down
        # (roll 180, the fixed finger on the right): negating the roll led every left solution there (2026-10-09).
        seeds = [np.array([self.mapping.to_urdf({"right_arm": {**dict(zip(("shoulder_pan", "shoulder_lift",
                                                                           "elbow_flex", "wrist_flex",
                                                                           "wrist_roll"), seed, strict=False)),
                                                               "gripper": 0.0}})[joint]
                           for joint in RIGHT.joints]) for seed in ReachMapConfig.SEEDS_REAL_DEG]
        self.seeds = seeds if arm.side == "right" else [seed * np.array([-1, 1, 1, 1, 1]) for seed in seeds] + seeds
        self.lock = threading.Lock()
        # x, y cell centres and reachable[i, j] for (xs[i], ys[j]); None until built.
        self.xs: np.ndarray | None = None
        self.ys: np.ndarray | None = None
        self.reachable: np.ndarray | None = None
        self.table_z: float | None = None
        # The head-image mask of the reachable desk, by head joints rounded (it only depends on the head pose).
        self.overlays: dict[tuple, np.ndarray] = {}

    # --- solving ---

    def tool(self, joints: np.ndarray) -> np.ndarray:
        self.urdf.update_cfg(dict(zip(self.arm.joints, map(float, joints), strict=False)))
        pose = self.urdf.get_transform(self.arm.tcp, "base_link")
        return pose

    def solve(self, position: np.ndarray, seeds: list[np.ndarray],
              approach: np.ndarray | None = None) -> np.ndarray | None:
        """Joints putting the tool at position with the jaw level (and, given approach, its approach axis along it)."""
        def residual(joints):
            pose = self.tool(joints)
            parts = [pose[:3, 3] - position, [0.3 * pose[2, 2], 0.3 * pose[2, 0]]]
            if approach is not None:
                parts.append(0.3 * (pose[:2, 2] - approach[:2]))
            return np.concatenate(parts)
        for seed in seeds:
            solution = least_squares(residual, np.clip(seed, self.limits[:, 0], self.limits[:, 1]),
                                     bounds=(self.limits[:, 0], self.limits[:, 1]))
            if solution.cost < ReachMapConfig.IK_COST:
                return solution.x
        return None

    def roll_ok(self, joints: np.ndarray) -> bool:
        low, high = self.arm.roll_range_deg
        roll = self.mapping.to_real({self.arm.link("Wrist_Roll"): float(joints[4])})[self.arm.part]["wrist_roll"]
        return low <= roll <= high

    def grasp_ok(self, x: float, y: float, z: float, seeds: list[np.ndarray]) -> np.ndarray | None:
        """The grasp joints if both the grasp and its pre-grasp solve level, with the roll in range; else None."""
        with self.lock:
            grasp = self.solve(np.array([x, y, z]), seeds)
            if grasp is None or not self.roll_ok(grasp):
                return None
            approach = self.tool(grasp)[:3, 2]
            back = np.array([x, y, z]) - MotionConfig.APPROACH_DISTANCE * approach
            pre = self.solve(back, [grasp, *seeds], approach)
            if pre is None or not self.roll_ok(pre):
                return None
        return grasp

    @staticmethod
    def grasp_height(height: float) -> float:
        """Above the table, where a grasp of an object this tall goes: the middle of the grasp band, raised for sag."""
        low, high = RealPlanningConfig.GRASP_BAND
        z = (low + high) / 2 * height + RealPlanningConfig.SAG_RAISE_M
        return float(np.clip(z, *ReachMapConfig.GRASP_Z_RANGE_M))

    # --- the grid ---

    def build(self, table_z: float) -> None:
        """The grid at table_z for the default object height, from disk when solved before with the same settings."""
        z = table_z + self.grasp_height(ReachMapConfig.DEFAULT_HEIGHT_M)
        xs = np.arange(*ReachMapConfig.X_RANGE_M, ReachMapConfig.CELL_M)
        ys = np.arange(*ReachMapConfig.Y_RANGE_M, ReachMapConfig.CELL_M)
        key = json.dumps([self.arm.side, self.arm.roll_range_deg, round(z, 3), xs[[0, -1]].round(3).tolist(),
                          ys[[0, -1]].round(3).tolist(), ReachMapConfig.CELL_M,
                          RealPlanningConfig.ROLL_RANGE_DEG, MotionConfig.APPROACH_DISTANCE, ReachMapConfig.IK_COST,
                          Path(PathConfig.URDF_PATH).stat().st_mtime])
        cache = PathConfig.OUTPUT_DIR / "reach_map" / f"{hashlib.md5(key.encode()).hexdigest()[:12]}.npz"
        if cache.exists():
            reachable = np.load(cache)["reachable"]
        else:
            started = time.time()
            reachable = np.zeros((len(xs), len(ys)), bool)
            for i, x in enumerate(xs):
                previous = None
                for j, y in enumerate(ys):
                    seeds = ([previous] if previous is not None else []) + self.seeds
                    found = self.grasp_ok(float(x), float(y), z, seeds)
                    reachable[i, j] = found is not None
                    previous = found if found is not None else previous
                    # A build takes minutes after every URDF change; between cells the other threads (the pages'
                    # framed head view) get the GIL and the GPU.
                    time.sleep(0.005)
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache, reachable=reachable, xs=xs, ys=ys, z=z)
            logger.info("{}可达图：{} x {} 格（{:.1f} cm），{} 格可抓，用时 {:.0f} s", self.arm.label, len(xs), len(ys),
                        ReachMapConfig.CELL_M * 100, int(reachable.sum()), time.time() - started)
        self.xs, self.ys, self.reachable, self.table_z = xs, ys, reachable, table_z
        self.overlays = {}

    def start(self, table_z: float) -> None:
        threading.Thread(target=self.build, args=(table_z,), name="reach_map", daemon=True).start()

    # --- answers ---

    def advice(self, x: float, y: float, height: float, table_z: float) -> dict:
        """For an object standing at (x, y), this tall: ok, and if not, how far to move it (dx, dy in m, base axes) to
        the nearest reachable cell plus a margin, with a sentence for the page."""
        grasp = self.grasp_ok(x, y, table_z + self.grasp_height(height), self.seeds)
        result = {"ok": grasp is not None, "xy": [round(x, 3), round(y, 3)], "move": None,
                  "text": f"{self.arm.label}够得着"}
        if grasp is not None or self.reachable is None or not self.reachable.any():
            if grasp is None:
                result["text"] = f"{self.arm.label}够不着（可达图还没算好，给不出挪动方向）"
            return result
        cells = np.argwhere(self.reachable)
        centres = np.stack([self.xs[cells[:, 0]], self.ys[cells[:, 1]]], axis=1)
        nearest = centres[np.argmin(np.linalg.norm(centres - [x, y], axis=1))]
        move = nearest - [x, y]
        move += ReachMapConfig.MARGIN_M * move / max(np.linalg.norm(move), 1e-6)
        result["move"] = [round(float(move[0]), 3), round(float(move[1]), 3)]
        words = []
        if abs(move[1]) >= 0.01:
            words.append(f"往{'右' if move[1] < 0 else '左'}挪约 {abs(move[1]) * 100:.0f} cm")
        if abs(move[0]) >= 0.01:
            words.append(f"往{'远处' if move[0] > 0 else '近处（靠机器人）'}挪约 {abs(move[0]) * 100:.0f} cm")
        result["text"] = f"{self.arm.label}够不着：" + ("、".join(words) or "挪一点") + "（按机器人的左右）"
        return result

    def project(self, frame: dict, points: np.ndarray) -> np.ndarray:
        """Base-link points (N, 3) as head-image pixels (N, 2) for this frame's head pose."""
        head = frame["head_joints"]
        with self.lock:
            self.urdf.update_cfg(self.mapping.to_urdf({"head": {"head_motor_1": float(head[0]),
                                                                "head_motor_2": float(head[1])}}))
            camera_from_base = np.linalg.inv(head_camera_pose(self.urdf))
        camera = camera_from_base @ np.c_[points, np.ones(len(points))].T
        pixels = (frame["K"] @ (camera[:3] / camera[2]))[:2].T
        return pixels

    def overlay(self, frame: dict) -> np.ndarray | None:
        """The head image (H, W) mask of the reachable desk at the table height, for this frame's head pose."""
        if self.reachable is None:
            return None
        head = tuple(np.round(frame["head_joints"], 1).tolist())
        if head not in self.overlays:
            with self.lock:
                self.urdf.update_cfg(self.mapping.to_urdf({"head": {"head_motor_1": float(head[0]),
                                                                    "head_motor_2": float(head[1])}}))
                camera_from_base = np.linalg.inv(head_camera_pose(self.urdf))
            height, width = frame["color"].shape[:2]
            mask = np.zeros((height, width), np.uint8)
            half = ReachMapConfig.CELL_M / 2
            for i, j in np.argwhere(self.reachable):
                x, y = self.xs[i], self.ys[j]
                corners = np.array([[x - half, y - half], [x + half, y - half], [x + half, y + half],
                                    [x - half, y + half]])
                points = camera_from_base @ np.c_[corners, np.full(4, self.table_z), np.ones(4)].T
                if (points[2] <= 0.05).any():
                    continue
                pixels = (frame["K"] @ (points[:3] / points[2]))[:2].T
                cv2.fillPoly(mask, [np.round(pixels).astype(np.int32)], 1)
            self.overlays[head] = mask.astype(bool)
        return self.overlays[head]


def demo_reach_map() -> None:
    """Offline: builds the grid (or reads it), then the 2026-10-09 cases."""
    reach = ReachMap()
    reach.build(0.764)
    for name, (x, y, h) in {"mouthwash (refused)": (0.278, 0.13, 0.115),
                            "mouthwash the night before (planned)": (0.234, 0.084, 0.116),
                            "spray bottle (planned)": (0.287, 0.005, 0.141),
                            "box place limit": (0.225, 0.13, 0.14)}.items():
        logger.info("{}: {}", name, reach.advice(x, y, h, 0.764))


def main() -> None:
    use_utf8_output()
    demo_reach_map()


if __name__ == "__main__":
    main()
