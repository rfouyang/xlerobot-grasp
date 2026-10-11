"""viser 3D 场景的薄封装：URDF 机器人、点云、网格、相机。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time
from threading import RLock

import numpy as np
import trimesh
import trimesh.transformations as tra
import viser
import yourdfpy
from loguru import logger
from viser.extras import ViserUrdf

from config.settings import PathConfig, use_utf8_output
from config.simulation import ViewerConfig


class SceneViewHelper:
    def __init__(self, host: str = ViewerConfig.HOST, port: int = ViewerConfig.PORT, **kwargs) -> None:
        self.host = host
        self.port = port
        self.server = None
        # Node name -> ViserUrdf, so joint updates find their robot.
        self.robots = {}
        # Node name -> viser handle, so a redraw replaces the node instead of stacking a second one.
        self.handles = {}
        # The page's requests redraw nodes while a follow thread moves them; a move on a node removed in the
        # meantime raised in viser and killed that thread (the 3D view froze).
        self.lock = RLock()

    def start(self) -> None:
        self.server = viser.ViserServer(host=self.host, port=self.port, verbose=False)
        self.server.scene.set_up_direction("+z")
        # The page carries the controls, so viser's own panel stays as small as it goes.
        self.server.gui.configure_theme(control_layout="floating", control_width="small",
                                        show_logo=False, show_share_button=False)
        self.server.scene.add_grid("/grid", width=4.0, height=4.0, cell_size=0.1)

    def add_robot(self, name: str, urdf_path: Path, package_root: Path) -> None:
        """package://<pkg>/... in the URDF resolves to package_root/<pkg>/..."""
        urdf = yourdfpy.URDF.load(str(urdf_path),
                                  filename_handler=lambda fname: fname.replace("package://", f"{package_root}/"))
        self.robots[name] = ViserUrdf(self.server, urdf, root_node_name=name)

    def set_robot_joints(self, name: str, positions: dict[str, float]) -> None:
        """Joints missing from positions stay at 0."""
        robot = self.robots[name]
        configuration = np.array([positions.get(joint, 0.0) for joint in robot.get_actuated_joint_names()])
        robot.update_cfg(configuration)

    def remove(self, name: str) -> None:
        with self.lock:
            handle = self.handles.pop(name, None)
            if handle is not None:
                handle.remove()

    def set_box(self, name: str, dimensions: tuple, pose: np.ndarray, color: tuple) -> None:
        with self.lock:
            self.remove(name)
            self.handles[name] = self.server.scene.add_box(
                name, color=color, dimensions=dimensions, position=pose[:3, 3], wxyz=tra.quaternion_from_matrix(pose))

    def set_mesh(self, name: str, mesh: trimesh.Trimesh, pose: np.ndarray = None, color: tuple = None,
                 **kwargs) -> None:
        """Without a color the mesh keeps its own texture / vertex colors."""
        pose = np.eye(4) if pose is None else pose
        with self.lock:
            self.remove(name)
            if color is None:
                self.handles[name] = self.server.scene.add_mesh_trimesh(
                    name, mesh, position=pose[:3, 3], wxyz=tra.quaternion_from_matrix(pose))
                return
            self.handles[name] = self.server.scene.add_mesh_simple(
                name, mesh.vertices, mesh.faces, color=color, opacity=kwargs.get("opacity"),
                position=pose[:3, 3], wxyz=tra.quaternion_from_matrix(pose))

    def set_pose(self, name: str, pose: np.ndarray) -> None:
        """Moves an existing node without resending its geometry; a name not shown (yet) is a no-op."""
        with self.lock:
            handle = self.handles.get(name)
            if handle is not None:
                handle.position = pose[:3, 3]
                handle.wxyz = tra.quaternion_from_matrix(pose)

    def set_points(self, name: str, points: np.ndarray, color: tuple, point_size: float = 0.003) -> None:
        with self.lock:
            self.remove(name)
            self.handles[name] = self.server.scene.add_point_cloud(
                name, points.astype(np.float32), colors=color, point_size=point_size, point_shape="rounded")

    def set_segments(self, name: str, segments: np.ndarray, colors: np.ndarray, line_width: float = 2.0) -> None:
        """segments (N, 2, 3), colors (N, 2, 3) uint8."""
        with self.lock:
            self.remove(name)
            self.handles[name] = self.server.scene.add_line_segments(name, segments, colors, line_width=line_width)

    def look_at(self, position: tuple, target: tuple, fov_deg: float = ViewerConfig.CAMERA_FOV_DEG) -> None:
        """New clients start here; connected ones are moved now. viser's default 75° field of view made the
        table look far away even from a close camera position."""
        self.server.initial_camera.fov = np.radians(fov_deg)
        self.server.initial_camera.position = position
        self.server.initial_camera.look_at = target
        for client in self.server.get_clients().values():
            client.camera.position = position
            client.camera.look_at = target


def demo_scene_view() -> None:
    """XLeRobot waving its right arm; open http://localhost:8080, Ctrl+C to stop."""
    viewer = SceneViewHelper()
    viewer.start()
    viewer.add_robot("/xlerobot", PathConfig.URDF_PATH, PathConfig.ROS_SRC_DIR)
    viewer.look_at(ViewerConfig.CAMERA_POSITION, ViewerConfig.CAMERA_TARGET)
    logger.info("viser 已启动：http://localhost:{}", viewer.port)
    start = time.time()
    while True:
        phase = time.time() - start
        viewer.set_robot_joints("/xlerobot", {"Rotation": 0.6 * np.sin(phase), "Pitch": 0.6, "Elbow": 1.2,
                                              "Wrist_Pitch": 0.6, "Jaw": 0.5 + 0.5 * np.sin(2 * phase)})
        time.sleep(0.05)


def main() -> None:
    use_utf8_output()
    demo_scene_view()


if __name__ == "__main__":
    main()
