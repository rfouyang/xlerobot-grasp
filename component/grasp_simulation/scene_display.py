"""3D 场景显示：viser 里的 XLeRobot 跟随实时关节状态。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import math
import time
from collections.abc import Callable
from threading import Thread

import numpy as np
import trimesh
from loguru import logger

from config.planning import GripperConfig
from config.settings import PathConfig, use_utf8_output
from config.simulation import ViewerConfig
from util.scene_view_helper import SceneViewHelper


class SceneDisplay:
    def __init__(self, **kwargs) -> None:
        self.viewer = SceneViewHelper(kwargs.get("host", ViewerConfig.HOST), kwargs.get("port", ViewerConfig.PORT))
        self.robot_node = kwargs.get("robot_node", ViewerConfig.ROBOT_NODE)
        self.gripper_mesh = trimesh.load(GripperConfig.DIR / "vis_mesh.obj", force="mesh")

    @property
    def port(self) -> int:
        port = self.viewer.port
        return port

    def start(self) -> None:
        self.viewer.start()
        self.viewer.add_robot(self.robot_node, PathConfig.URDF_PATH, PathConfig.ROS_SRC_DIR)
        self.viewer.look_at(ViewerConfig.CAMERA_POSITION, ViewerConfig.CAMERA_TARGET)

    def show_tabletop(self, table_pose: np.ndarray, table_size: tuple, object_mesh: trimesh.Trimesh,
                      object_pose: np.ndarray, rgba: tuple, points: np.ndarray) -> None:
        """object_mesh is in the base frame; it is drawn in its own frame at object_pose so the
        simulator can move it later."""
        local = object_mesh.copy()
        local.apply_transform(np.linalg.inv(object_pose))
        self.viewer.set_box("/scene/table", tuple(table_size), table_pose, ViewerConfig.TABLE_COLOR)
        self.viewer.set_mesh("/scene/object", local, object_pose, color=tuple(int(255 * c) for c in rgba[:3]),
                             opacity=rgba[3])
        self.viewer.set_points("/scene/points", points, ViewerConfig.POINT_COLOR)

    def show_grasps(self, poses: list[np.ndarray], scores: list[float], gripper_config: dict) -> None:
        """Each grasp as the GraspGenX-style fork (stem + two fingers), red (low score) to green (high)."""
        self.viewer.remove("/grasps/selected")
        if len(poses) == 0:
            self.viewer.remove("/grasps/candidates")
            return
        fork = self.gripper_fork(gripper_config)
        segments = np.concatenate([fork @ pose[:3, :3].T + pose[:3, 3] for pose in poses])
        score = np.array(scores)
        level = (score - score.min()) / max(np.ptp(score), 1e-6)
        rgb = np.stack([255 * (1 - level), 200 * level, 60 * np.ones_like(level)], axis=1).astype(np.uint8)
        colors = np.repeat(rgb[:, None, None, :], len(fork), axis=1).reshape(-1, 1, 3).repeat(2, axis=1)
        self.viewer.set_segments("/grasps/candidates", segments, colors)

    def show_selected_grasp(self, pose: np.ndarray) -> None:
        """The open gripper itself, drawn at the chosen grasp."""
        self.viewer.set_mesh("/grasps/selected", self.gripper_mesh, pose, color=ViewerConfig.SELECTED_COLOR)

    @staticmethod
    def gripper_fork(config: dict) -> np.ndarray:
        """(4, 2, 3) segments in the canonical gripper frame, sized from the open sweep box."""
        extents = config["sweep_volume"]["extents"]
        offset = config["sweep_volume"]["offset"]
        cx, half = offset[0], extents[0] / 2
        base_z, tip_z = offset[2] - extents[2] / 2, offset[2] + extents[2] / 2
        fork = np.array([
            [[cx, 0, 0], [cx, 0, base_z]],
            [[cx - half, 0, base_z], [cx + half, 0, base_z]],
            [[cx - half, 0, base_z], [cx - half, 0, tip_z]],
            [[cx + half, 0, base_z], [cx + half, 0, tip_z]],
        ])
        return fork

    def follow(self, joint_source: Callable[[], dict[str, float]],
               object_source: Callable[[], np.ndarray | None] = None,
               interval_s: float = ViewerConfig.FOLLOW_INTERVAL_S) -> None:
        """Keeps the 3D robot on joint_source() (and the object on object_source(), the simulator's pose)
        from a daemon thread."""
        def loop() -> None:
            while True:
                self.viewer.set_robot_joints(self.robot_node, joint_source())
                pose = None if object_source is None else object_source()
                if pose is not None:
                    self.viewer.set_pose("/scene/object", pose)
                time.sleep(interval_s)

        Thread(target=loop, daemon=True).start()


def demo_scene_display() -> None:
    """A fake joint source; open http://localhost:8080, Ctrl+C to stop."""
    display = SceneDisplay()
    display.start()
    start = time.time()
    display.follow(lambda: {"Rotation_2": 0.8 * math.sin(time.time() - start), "Pitch_2": 0.6, "Elbow_2": 1.2})
    logger.info("viser：http://localhost:{}", display.port)
    while True:
        time.sleep(1)


def main() -> None:
    use_utf8_output()
    demo_scene_display()


if __name__ == "__main__":
    main()
