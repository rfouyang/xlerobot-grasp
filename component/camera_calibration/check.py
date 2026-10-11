"""标定检查：把 URDF 放到真机关节角上，把手臂末端几个连杆投影到头部相机画面里，看是否落在真实的手臂上。不动机器人。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import cv2
import numpy as np
import trimesh
import yourdfpy
from loguru import logger
from scipy.spatial import cKDTree

from config.perception import HeadCameraConfig
from config.settings import PathConfig, use_utf8_output
from util.joint_mapping_helper import JointMappingHelper
from util.teleop_helper import TeleopHelper

CAMERA_FRAME = "head_camera_rgb_optical_frame"


def head_camera_pose(urdf: yourdfpy.URDF) -> np.ndarray:
    """base_link <- head camera optical frame as calibrated (HeadCameraConfig), for the URDF at its current joints:
    the URDF's, corrected in the camera frame, then trimmed in base_link."""
    trim = np.eye(4)
    trim[:3, 3] = HeadCameraConfig.BASE_TRIM_M
    pose = trim @ urdf.get_transform(CAMERA_FRAME, "base_link") @ np.asarray(HeadCameraConfig.CORRECTION)
    return pose
# Links to draw, and their colour (BGR): what the head camera sees of each arm at the grasp start pose.
LINKS = {
    "Lower_Arm": (60, 60, 255), "Wrist_Pitch_Roll": (0, 120, 255), "Fixed_Jaw": (0, 200, 255),
    "Moving_Jaw": (0, 255, 255), "Right_Arm_Camera": (0, 0, 255),
    "Lower_Arm_2": (255, 80, 60), "Wrist_Pitch_Roll_2": (255, 160, 0), "Fixed_Jaw_2": (255, 220, 0),
    "Moving_Jaw_2": (255, 255, 120), "Left_Arm_Camera": (255, 0, 255),
    # The wrist cameras' mounting plates (boxes, added to the URDF on 2026-10-09).
    "Right_Camera_Plate": (40, 40, 200), "Left_Camera_Plate": (200, 40, 200),
}


class CalibrationCheck:
    """If the projected links land on the real arms, both the joint mapping (zeros and directions) and the head
    camera's pose in xlerobot.urdf hold, so a point seen by the camera is where the arm will go."""

    def __init__(self, teleop: TeleopHelper, **kwargs) -> None:
        self.teleop = teleop
        self.mapping = JointMappingHelper()
        package_root = kwargs.get("package_root", PathConfig.ROS_SRC_DIR)
        self.urdf = yourdfpy.URDF.load(str(kwargs.get("urdf_path", PathConfig.URDF_PATH)), load_meshes=False,
                                       build_scene_graph=True)
        # link -> (N, 4) surface points in the link frame
        self.points = {}
        for name in LINKS:
            meshes = []
            for visual in self.urdf.link_map[name].visuals:
                if visual.geometry.box is not None:
                    mesh = trimesh.creation.box(extents=visual.geometry.box.size)
                elif visual.geometry.mesh is not None:
                    mesh = trimesh.load(visual.geometry.mesh.filename.replace("package://", f"{package_root}/"),
                                        force="mesh")
                    if visual.geometry.mesh.scale is not None:
                        mesh.apply_scale(visual.geometry.mesh.scale)
                else:
                    continue
                if visual.origin is not None:
                    mesh.apply_transform(visual.origin)
                meshes.append(mesh)
            samples = np.asarray(trimesh.sample.sample_surface(trimesh.util.concatenate(meshes),
                                                               kwargs.get("samples", 1500), seed=0)[0])
            self.points[name] = np.hstack([samples, np.ones((len(samples), 1))])

    def project(self, real_joints: dict, frame: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """real_joints: part -> motor -> value. Returns the colour image with the links drawn, and link -> pixels."""
        self.urdf.update_cfg(self.mapping.to_urdf(real_joints))
        # The calibrated head camera (as planning sees it), so the jaw check and the plan measure alike.
        camera_from_base = np.linalg.inv(head_camera_pose(self.urdf))
        image = cv2.cvtColor(frame["color"], cv2.COLOR_RGB2BGR)
        pixels = {}
        for name, color in LINKS.items():
            in_camera = (camera_from_base @ self.urdf.get_transform(name, "base_link") @ self.points[name].T).T[:, :3]
            in_front = in_camera[in_camera[:, 2] > 0.02]
            uv = (frame["K"] @ in_front.T).T
            uv = uv[:, :2] / uv[:, 2:3]
            pixels[name] = np.hstack([uv, in_front[:, 2:3]])
            for u, v in uv.astype(int):
                if 0 <= u < image.shape[1] and 0 <= v < image.shape[0]:
                    cv2.circle(image, (int(u), int(v)), 1, color, -1)
        return image, pixels

    def offset(self, real_joints: dict, frame: dict, links: list[str], table_z: float) -> dict | None:
        """Where links really are against their model at real_joints: the translation (base_link, m) that moves the
        model's surface points onto the head depth around them (nearest points within a shrinking threshold), with the
        median distance before / after (mm) and the depth points used; None with fewer than 300 points."""
        self.urdf.update_cfg(self.mapping.to_urdf(real_joints))
        base_from_camera = head_camera_pose(self.urdf)
        model = np.vstack([(self.urdf.get_transform(link, "base_link") @ self.points[link].T).T[:, :3]
                           for link in links])
        camera = (np.linalg.inv(base_from_camera) @ np.c_[model, np.ones(len(model))].T)[:3]
        depth = frame["depth"] * frame["depth_scale"]
        uv = (frame["K"] @ camera)[:2] / camera[2]
        u0, v0 = np.clip(uv.min(axis=1) - 40, 0, None).astype(int)
        u1, v1 = np.minimum(uv.max(axis=1) + 40, [depth.shape[1] - 1, depth.shape[0] - 1]).astype(int)
        if u1 <= u0 or v1 <= v0:
            return None
        rows, cols = np.mgrid[v0:v1 + 1, u0:u1 + 1]
        z = depth[v0:v1 + 1, u0:u1 + 1]
        keep = (z > camera[2].min() - 0.08) & (z < camera[2].max() + 0.05)
        points = (np.linalg.inv(frame["K"]) @ np.vstack([cols[keep], rows[keep], np.ones(keep.sum())])) * z[keep]
        points = (base_from_camera @ np.vstack([points, np.ones(points.shape[1])]))[:3].T
        points = points[points[:, 2] > table_z + 0.02]
        if len(points) < 300:
            return None
        shift = np.zeros(3)
        for threshold in [0.06] * 4 + [0.03] * 4 + [0.015] * 4 + [0.01] * 4:
            distance, index = cKDTree(model + shift).query(points)
            near = distance < threshold
            if near.sum() < 100:
                return None
            shift = (points[near] - model[index[near]]).mean(axis=0)
        before, _ = cKDTree(model).query(points)
        after, _ = cKDTree(model + shift).query(points)
        found = {"t": shift, "before_mm": 1000 * float(np.median(before[before < 0.06])),
                 "after_mm": 1000 * float(np.median(after[after < 0.06])), "points": int(len(points))}
        return found

    @staticmethod
    def depth_errors(pixels: dict[str, np.ndarray], frame: dict) -> dict[str, tuple[int, float | None, float | None]]:
        """link -> (pixels in the image, median model depth, median measured depth) over the pixels where the camera
        sees that link's nearest model point and has a depth."""
        depth = frame["depth"] * frame["depth_scale"]
        errors = {}
        for name, uvz in pixels.items():
            inside = uvz[(uvz[:, 0] >= 0) & (uvz[:, 0] < depth.shape[1]) & (uvz[:, 1] >= 0) &
                         (uvz[:, 1] < depth.shape[0])]
            nearest = {}
            for u, v, z in inside:
                key = (int(v), int(u))
                nearest[key] = min(z, nearest.get(key, np.inf))
            pairs = np.array([(z, depth[key]) for key, z in nearest.items() if depth[key] > 0])
            errors[name] = (len(nearest), float(np.median(pairs[:, 0])) if len(pairs) else None,
                            float(np.median(pairs[:, 1])) if len(pairs) else None)
        return errors

    def run(self) -> Path:
        frame = self.teleop.rgbd()
        joints = {part: {motor: values[0] for motor, values in motors.items()}
                  for part, motors in self.teleop.status()["joints"].items()}
        # The head as it was for this image.
        joints["head"] = {"head_motor_1": float(frame["head_joints"][0]),
                          "head_motor_2": float(frame["head_joints"][1])}
        image, pixels = self.project(joints, frame)
        for name, (count, model, measured) in self.depth_errors(pixels, frame).items():
            summary = "不在画面里" if count == 0 else "无深度" if model is None else (
                f"{count} 点在画面里，模型 {model:.3f} m / 实测 {measured:.3f} m，"
                f"差中位 {(measured - model) * 100:+.1f} cm")
            logger.info("{:20s} {}", name, summary)
        out_dir = PathConfig.DEMO_DIR / "calibration_check"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "projection.jpg"
        original = cv2.cvtColor(frame["color"], cv2.COLOR_RGB2BGR)
        # The arms show at the bottom of the image at the grasp start pose: that strip, twice the size, below.
        strip = np.hstack([original[300:], image[300:]])
        cv2.imwrite(str(path), np.vstack([np.hstack([original, image]), cv2.resize(strip, None, fx=1, fy=1)]))
        return path


def demo_calibration_check() -> None:
    """Needs the teleop service, the head at the grasp start pose. Reads only."""
    path = CalibrationCheck(TeleopHelper()).run()
    logger.info("左：相机原图；右：投影（右臂红/橙/黄，左臂蓝/青）→ {}", path)


def main() -> None:
    use_utf8_output()
    demo_calibration_check()


if __name__ == "__main__":
    main()
