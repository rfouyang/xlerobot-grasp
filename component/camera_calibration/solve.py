"""腕部相机标定求解（标定页「计算标定」）：一次标定会话里，手臂摆过的每个姿态都有腕部相机看到的桌面 ChArUco 板、头部
RGB 和关节读数。
1. 腕部相机内参（各视角的板子）。
2. 板子在 base 里的位置：头部相机（已标定的位姿）在每个姿态下 PnP，取平均（板子一直没动）。
3. 这只手臂的 5 个关节偏置 d 和腕部相机在定指上的位姿 X：最小化所有腕部图像里板子角点的重投影误差，
   base_T_jaw(关节 + 当前修正 + d) @ X 要看到板子在它该在的地方；每个姿态留一验证。
移植自 output/scratch_20261006/left_board_solve.py（2026-10-09 左臂标定），左右手通用。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json

import cv2
import numpy as np
import yourdfpy
from loguru import logger
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from component.camera_calibration.check import head_camera_pose
from component.grasp_planning.arm import ARMS, Arm
from config.charuco import CharucoConfig
from config.settings import PathConfig, use_utf8_output
from util.charuco_helper import CharucoHelper
from util.joint_mapping_helper import JointMappingHelper

MOTORS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


def to_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, np.ravel(t)
    return T


class WristSolve:
    def __init__(self, session: Path, arm: Arm, **kwargs) -> None:
        self.session, self.arm = Path(session), arm
        self.helper = CharucoHelper()
        self.urdf = yourdfpy.URDF.load(str(PathConfig.URDF_PATH), load_meshes=False, build_scene_graph=True)
        # corrections: the ones the joints are mapped with (default: what the robot uses now); the result's
        # correction_deg is these plus the offsets found, ready to replace them.
        self.mapping = JointMappingHelper(**({"corrections": kwargs["corrections"]} if "corrections" in kwargs else {}))
        self.base_correction = {motor: self.mapping.corrections.get(arm.part, {}).get(motor, 0.0) for motor in MOTORS}
        right = json.loads((BASE_DIR / "config" / "calibration" / "head_and_wrist_20261006.json").read_text())
        self.X0 = np.array(right["wrist_hand_eye"]["fixed_jaw_T_camera_optical"])
        self.views: list[dict] = []
        self.K = self.dist = None

    # --- 1 ---

    def load(self) -> None:
        for path in sorted(self.session.glob("pose_*_wrist.png")):
            index = int(path.name[5:7])
            rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
            view = self.helper.detect(rgb)
            if view.count < CharucoConfig.MIN_CORNERS:
                logger.info("姿态 {:02d}：腕部 {} 个角点，跳过", index, view.count)
                continue
            corners = view.corners.reshape(-1, 1, 2).astype(np.float32)
            obj, img = self.helper.board.matchImagePoints(corners, view.ids.reshape(-1, 1))
            joints = json.loads((self.session / f"pose_{index:02d}_joints.json").read_text())["measured_real"]
            self.views.append({"i": index, "obj": obj.reshape(-1, 3), "img": img.reshape(-1, 2), "joints": joints,
                               "size": rgb.shape[1::-1]})

    def intrinsics(self) -> float:
        rms, self.K, self.dist, _, _ = cv2.calibrateCamera([v["obj"].astype(np.float32) for v in self.views],
                                                           [v["img"].astype(np.float32) for v in self.views],
                                                           self.views[0]["size"], None, None)
        for view in self.views:
            view["undist"] = cv2.undistortPoints(view["img"].reshape(-1, 1, 2).astype(np.float64), self.K,
                                                 self.dist).reshape(-1, 2)
        return float(rms)

    # --- 2 ---

    def board_in_base(self, given: np.ndarray | None = None) -> np.ndarray:
        """The head's PnP at every pose (its calibrated pose, the head joints of that frame); the mean position, the
        first rotation (the board never moved). given: a board pose measured otherwise (the regression)."""
        poses = [] if given is None else [np.asarray(given)]
        for view in self.views if given is None else []:
            head = np.load(self.session / f"pose_{view['i']:02d}_head.npz")
            seen = self.helper.detect(head["color"])
            if seen.count < 20:
                continue
            obj, img = self.helper.board.matchImagePoints(seen.corners.reshape(-1, 1, 2).astype(np.float32),
                                                          seen.ids.reshape(-1, 1))
            ok, rvec, tvec = cv2.solvePnP(obj, img, head["K"], None)
            if not ok:
                continue
            joints = {**view["joints"], "head": {"head_motor_1": float(head["head_joints"][0]),
                                                 "head_motor_2": float(head["head_joints"][1])}}
            self.urdf.update_cfg(self.mapping.to_urdf(joints))
            poses.append(head_camera_pose(self.urdf) @ to_T(cv2.Rodrigues(rvec)[0], tvec))
        if not poses:
            raise RuntimeError("头部相机在这次会话里没看清标定板")
        board = poses[0].copy()
        board[:3, 3] = np.mean([pose[:3, 3] for pose in poses], axis=0)
        self.board_spread_mm = float(max(np.linalg.norm(pose[:3, 3] - board[:3, 3]) for pose in poses) * 1000)
        for view in self.views:
            view["board_base"] = (board @ np.c_[view["obj"], np.ones(len(view["obj"]))].T)[:3].T
            cfg = self.mapping.to_urdf(view["joints"])
            view["cfg"], view["q0"] = cfg, np.array([cfg[joint] for joint in self.arm.joints])
        return board

    # --- 3 ---

    def unpack(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return x[:5], to_T(Rotation.from_rotvec(x[5:8]).as_matrix(), x[8:11])

    def project(self, view: dict, x: np.ndarray) -> np.ndarray:
        d, X = self.unpack(x)
        self.urdf.update_cfg({**view["cfg"], **dict(zip(self.arm.joints, view["q0"] + d, strict=True))})
        camera_T_base = np.linalg.inv(self.urdf.get_transform(self.arm.link("Fixed_Jaw"), "base_link") @ X)
        p = (camera_T_base @ np.c_[view["board_base"], np.ones(len(view["board_base"]))].T)[:3]
        return (p[:2] / p[2]).T

    def residual(self, x: np.ndarray, subset: list[dict]) -> np.ndarray:
        return np.concatenate([((self.project(view, x) - view["undist"]) * self.K[0, 0]).ravel()
                               for view in subset])

    def per_view(self, x: np.ndarray, subset: list[dict]) -> list[float]:
        return [float(np.sqrt(np.mean(np.sum(((self.project(v, x) - v["undist"]) * self.K[0, 0]) ** 2, axis=1))))
                for v in subset]

    def fit(self, subset: list[dict], x0: np.ndarray) -> np.ndarray:
        solution = least_squares(self.residual, x0, args=(subset,), loss="soft_l1", f_scale=3.0,
                                 x_scale=np.r_[[0.05] * 5, [0.05] * 3, [0.01] * 3])
        return solution.x

    def run(self, board: np.ndarray | None = None) -> dict:
        self.load()
        if len(self.views) < 6:
            raise RuntimeError(f"腕部相机只有 {len(self.views)} 个姿态看清了标定板，至少要 6 个")
        rms = self.intrinsics()
        board = self.board_in_base(board)
        x0 = np.r_[np.zeros(5), Rotation.from_matrix(self.X0[:3, :3]).as_rotvec(), self.X0[:3, 3]]
        before = self.per_view(x0, self.views)
        x = self.fit(self.views, x0)
        after = self.per_view(x, self.views)
        d, X = self.unpack(x)
        loo, held_out = [], []
        for k, view in enumerate(self.views):
            xk = self.fit(self.views[:k] + self.views[k + 1:], x)
            loo.append(np.degrees(self.unpack(xk)[0] - d))
            held_out.append(self.per_view(xk, [view])[0])
        offsets = {motor: round(float(np.degrees(value)), 2) for motor, value in zip(MOTORS, d, strict=True)}
        # The roll is badly constrained by board views (2026-10-09: -4.6, unstable to 7.6 deg): kept as it was.
        correction = {motor: round(self.base_correction[motor] + (offsets[motor] if motor != "wrist_roll" else 0.0), 2)
                      for motor in MOTORS}
        result = {"arm": self.arm.part,
                  "session": str(self.session.relative_to(BASE_DIR) if self.session.is_relative_to(BASE_DIR)
                                 else self.session), "views": [v["i"] for v in self.views],
                  "K": self.K.tolist(), "dist": self.dist.ravel().tolist(), "rms_px": rms,
                  "board_in_base": board.tolist(), "board_spread_mm": round(self.board_spread_mm, 1),
                  "joint_offset_deg": [offsets[motor] for motor in MOTORS], "joints": list(self.arm.joints),
                  "fixed_jaw_T_camera_optical": X.tolist(),
                  "loo_max_deg": np.abs(np.array(loo)).max(axis=0).round(2).tolist(),
                  "reprojection_px": {"start": round(float(np.median(before)), 2),
                                      "fitted": round(float(np.median(after)), 2),
                                      "held_out": round(float(np.median(held_out)), 2)},
                  "base_correction_deg": self.base_correction, "correction_deg": correction}
        return result


def demo_wrist_solve() -> None:
    """Offline regression: the 2026-10-09 left-arm session, solved with the bare mapping and the board pose measured
    then, against its saved result (the page solves with the board from the session's own head frames)."""
    session = BASE_DIR / "data" / "calibration" / "left_board_20261009"
    result = WristSolve(session, ARMS["left"], corrections={}).run(np.load(session / "base_T_board_left.npy"))
    saved = json.loads((session / "left_board.json").read_text())
    logger.info("内参 RMS {:.3f} px（当时 {:.3f}）；视角 {}", result["rms_px"], saved["rms_px"], len(result["views"]))
    logger.info("关节偏置 {}（当时 {}）", result["joint_offset_deg"], np.round(saved["joint_offset_deg"], 2).tolist())
    lens, lens0 = (np.array(result["fixed_jaw_T_camera_optical"])[:3, 3] * 1000,
                   np.array(saved["fixed_jaw_T_camera_optical"])[:3, 3] * 1000)
    logger.info("镜头在定指上 {} mm（当时 {}）；重投影 {}；留一 {}；板子散布 {} mm", lens.round(1).tolist(),
                lens0.round(1).tolist(), result["reprojection_px"], result["loo_max_deg"], result["board_spread_mm"])


def main() -> None:
    use_utf8_output()
    demo_wrist_solve()


if __name__ == "__main__":
    main()
