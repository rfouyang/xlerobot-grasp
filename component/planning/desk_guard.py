"""桌面安全守卫：任何让手臂动的地方（规划出的轨迹、跟随发给真机的每个目标、标定脚本）都先问它，手臂上所有网格点离桌面
够不够远。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import threading

import numpy as np
from loguru import logger

from component.planning.arm import Arm
from config.planning import DeskGuardConfig
from config.settings import use_utf8_output

# Every link of an arm the desk could meet, in the right arm's names (Arm.link gives the other arm's).
LINKS = ("Lower_Arm", "Wrist_Pitch_Roll", "Fixed_Jaw", "Moving_Jaw", "Right_Arm_Camera", "Right_Camera_Plate")


class DeskGuard:
    """The desk is a plane (the last one measured from the head depth, kept in DeskGuardConfig.PLANE_FILE; flat at
    DEFAULT_Z until then), and it is taken to reach the robot: every mesh point of the arm in front of the robot's centre
    (x > FRONT_X) must stay the arm's margin (ArmConfig.DESK_MARGIN_M) above it. On 2026-10-09 a left-arm calibration
    pose hit the desk: only points beyond x 0.15 had been checked, against a flat desk, and the left arm's model was a
    few cm off. Its own URDF copy and mesh samples; one check at a time."""

    def __init__(self, **kwargs) -> None:
        from component.calibration.check import CalibrationCheck

        check = CalibrationCheck(None, samples=kwargs.get("samples", DeskGuardConfig.SAMPLES))
        self.urdf, self.mapping, self.points = check.urdf, check.mapping, check.points
        self.lock = threading.Lock()
        self.normal, self.origin = np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, DeskGuardConfig.DEFAULT_Z])
        self.load()

    def load(self) -> None:
        if DeskGuardConfig.PLANE_FILE.exists():
            plane = json.loads(DeskGuardConfig.PLANE_FILE.read_text())
            self.normal, self.origin = np.array(plane["normal"]), np.array(plane["origin"])

    def set_plane(self, points: np.ndarray) -> None:
        """The desk plane from desk points (N, 3) in base_link (least squares), kept for every later check and on disk."""
        centre = points.mean(axis=0)
        normal = np.linalg.svd(points - centre, full_matrices=False)[2][-1]
        normal = normal if normal[2] > 0 else -normal
        self.normal, self.origin = normal, centre
        DeskGuardConfig.PLANE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DeskGuardConfig.PLANE_FILE.write_text(json.dumps({"normal": normal.tolist(), "origin": centre.tolist()}))
        logger.info("桌面平面：倾斜 {:.1f}°，x 0.25 处高 {:.3f} m", np.degrees(np.arccos(normal[2])), self.height(0.25, 0.0))

    def height(self, x: float, y: float) -> float:
        z = float(self.origin[2] - (self.normal[0] * (x - self.origin[0]) + self.normal[1] * (y - self.origin[1])) / self.normal[2])
        return z

    def clearance(self, arm: Arm, urdf_joints: dict[str, float]) -> tuple[float, str]:
        """The lowest distance (m) of the arm's mesh points in front of the robot above the desk plane, and which link."""
        with self.lock:
            self.urdf.update_cfg(urdf_joints)
            worst, which = np.inf, ""
            for name in LINKS:
                link = arm.link(name)
                points = (self.urdf.get_transform(link, "base_link") @ self.points[link].T)[:3].T
                points = points[points[:, 0] > DeskGuardConfig.FRONT_X]
                if len(points):
                    lowest = float(((points - self.origin) @ self.normal).min())
                    if lowest < worst:
                        worst, which = lowest, link
        return worst, which

    def check_real(self, arm: Arm, real: dict[str, float]) -> tuple[bool, float, str]:
        """real: that arm's motors (real degrees). ok when the clearance is at least the arm's margin."""
        cfg = self.mapping.to_urdf({arm.part: {**real, "gripper": real.get("gripper", 0.0)}})
        clearance, link = self.clearance(arm, cfg)
        return clearance >= arm.desk_margin_m, clearance, link

    def check_rows(self, arm: Arm, rows: np.ndarray, fixed: dict[str, float]) -> tuple[bool, float, int, str]:
        """rows (T, 5): the arm's URDF joints along a trajectory, densified to steps of at most STEP_RAD; fixed: every
        other joint. ok when every row keeps the margin; also the lowest clearance, its row and link."""
        rows = np.asarray(rows, float)
        dense = [rows[0]]
        for a, b in zip(rows[:-1], rows[1:]):
            n = max(1, int(np.ceil(np.abs(b - a).max() / DeskGuardConfig.STEP_RAD)))
            dense.extend(a + (b - a) * t for t in np.linspace(0, 1, n + 1)[1:])
        worst, at, which = np.inf, 0, ""
        for k, row in enumerate(dense):
            clearance, link = self.clearance(arm, {**fixed, **dict(zip(arm.joints, map(float, row)))})
            if clearance < worst:
                worst, at, which = clearance, k, link
        return worst >= arm.desk_margin_m, worst, at, which


def demo_desk_guard() -> None:
    """Offline: the clearance of the left arm at the 15 board-calibration poses recorded on 2026-10-09 (one hit)."""
    from component.planning.arm import LEFT

    guard = DeskGuard()
    session = BASE_DIR / "data" / "calibration" / "left_board_20261009"
    for path in sorted(session.glob("pose_*_joints.json")):
        real = json.loads(path.read_text())["measured_real"]["left_arm"]
        ok, clearance, link = guard.check_real(LEFT, real)
        logger.info("{}: {} {:.1f} cm（{}）", path.name[:7], "OK" if ok else "拦下", clearance * 100, link)


def main() -> None:
    use_utf8_output()
    demo_desk_guard()


if __name__ == "__main__":
    main()
