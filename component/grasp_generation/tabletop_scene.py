"""桌面场景：车前的桌子，把饮料竖立放在右臂前方，采样表面点云。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


import numpy as np
import trimesh
import trimesh.transformations as tra
from loguru import logger

from component.grasp_generation.placed_object import PlacedObject
from config.planning import GraspConfig, SceneConfig
from config.settings import use_utf8_output


class TabletopScene:
    def __init__(self, **kwargs) -> None:
        self.table_size = np.array(kwargs.get("table_size", SceneConfig.TABLE_SIZE))
        self.table_top_z = kwargs.get("table_top_z", SceneConfig.TABLE_TOP_Z)
        self.table_front_x = kwargs.get("table_front_x", SceneConfig.TABLE_FRONT_X)
        self.edge_margin = kwargs.get("edge_margin", SceneConfig.EDGE_MARGIN)
        # The desk plane as measured (unit normal, a point on it) when the robot is not square to it (the real robot is
        # pitched ~5.5 deg against its desk, seen both through the right arm and the head camera, 2026-10-09); None: a
        # level desk at table_top_z.
        self.table_plane: tuple[np.ndarray, np.ndarray] | None = kwargs.get("table_plane")

    @property
    def table_pose(self) -> np.ndarray:
        """Centre of the table box in base_link: level at table_top_z, or along table_plane with its top on
        the plane."""
        centre_x = self.table_front_x + self.table_size[0] / 2
        if self.table_plane is None:
            pose = tra.translation_matrix([centre_x, 0.0, self.table_top_z - self.table_size[2] / 2])
            return pose
        normal, point = (np.asarray(v, float) for v in self.table_plane)
        top = point[2] - (normal[0] * (centre_x - point[0]) + normal[1] * (0.0 - point[1])) / normal[2]
        axis = np.cross([0.0, 0.0, 1.0], normal)
        angle = float(np.arccos(np.clip(normal[2], -1.0, 1.0)))
        pose = tra.rotation_matrix(angle, axis) if np.linalg.norm(axis) > 1e-9 else np.eye(4)
        pose[:3, 3] = np.array([centre_x, 0.0, top]) - normal * self.table_size[2] / 2
        return pose

    def objects(self) -> list[dict]:
        catalog = [{"name": name, "label": item["label"]} for name, item in SceneConfig.OBJECTS.items()]
        return catalog

    def place(self, name: str, xy: tuple = SceneConfig.OBJECT_XY, yaw: float = SceneConfig.OBJECT_YAW) -> PlacedObject:
        """Stands the object on its base (the meshes are built upright on z=0) with its axis at xy."""
        item = SceneConfig.OBJECTS[name]
        file = SceneConfig.OBJECT_DIR / item["file"]
        mesh = trimesh.load(file, force="mesh")
        center = mesh.bounds.mean(axis=0)
        turned = tra.rotation_matrix(yaw, [0, 0, 1]) @ tra.translation_matrix([-center[0], -center[1], 0.0])
        near_side = trimesh.transform_points(mesh.vertices, turned)[:, 0].min()
        # Pushed forward when its near side would hang over the table edge into the cart.
        x = max(xy[0], self.table_front_x + self.edge_margin - near_side)
        pose = tra.translation_matrix([x, xy[1], self.table_top_z]) @ turned
        mesh.apply_transform(pose)
        placed = PlacedObject(name=name, label=item["label"], mesh=mesh, pose=pose, file=file, scale=1.0,
                              rgba=item["rgba"], mass=item["mass"])
        return placed

    @staticmethod
    def moved(placed: PlacedObject, pose: np.ndarray) -> PlacedObject:
        """The same object at another pose (base_link <- mesh file frame)."""
        mesh = placed.mesh.copy()
        mesh.apply_transform(pose @ np.linalg.inv(placed.pose))
        moved = PlacedObject(name=placed.name, label=placed.label, mesh=mesh, pose=pose, file=placed.file,
                             scale=placed.scale, rgba=placed.rgba, mass=placed.mass)
        return moved

    @staticmethod
    def surface_points(placed: PlacedObject, count: int = GraspConfig.NUM_SAMPLE_POINTS) -> np.ndarray:
        points = np.asarray(trimesh.sample.sample_surface(placed.mesh, count, seed=0)[0])
        return points

    def below_table(self, points: np.ndarray, margin: float = 0.005) -> bool:
        """True when any of the points dips into the table top (used for gripper meshes at a grasp)."""
        below = bool((points[:, 2] < self.table_top_z + margin).any())
        return below


def demo_tabletop_scene() -> None:
    scene = TabletopScene()
    for item in scene.objects():
        placed = scene.place(item["name"])
        size = placed.mesh.bounds[1] - placed.mesh.bounds[0]
        logger.info("{}：底面 z={:.3f}，尺寸 {} cm，轴心 {}", placed.label, placed.mesh.bounds[0][2],
                    np.round(size * 100, 1), np.round(placed.mesh.bounds.mean(axis=0)[:2], 3))


def main() -> None:
    use_utf8_output()
    demo_tabletop_scene()


if __name__ == "__main__":
    main()
