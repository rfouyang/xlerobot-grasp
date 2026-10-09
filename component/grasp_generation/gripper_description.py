"""SO-101 夹爪的 GraspGenX 描述：从机器人 URDF 截出夹爪，转到 GraspGenX 规范坐标系，算扫掠体。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
import trimesh.transformations as tra
import yourdfpy
from loguru import logger

from config.planning import GripperConfig

from config.settings import PathConfig, use_utf8_output


class GripperDescription:
    """Writes gripper.urdf, meshes/, config.json, vis_mesh.obj and coll_mesh.obj into GripperConfig.DIR.

    gripper.urdf is already in the canonical frame (its root joint carries the rotation), so the
    wizard-compatible config.json keeps base_rotation at identity.
    """

    def __init__(self, out_dir: Path = GripperConfig.DIR, **kwargs) -> None:
        self.out_dir = out_dir
        self.robot_urdf = kwargs.get("robot_urdf", PathConfig.URDF_PATH)
        self.package_root = kwargs.get("package_root", PathConfig.ROS_SRC_DIR)
        rotation = np.array(GripperConfig.CANONICAL_AXES)
        # canonical <- Fixed_Jaw: rows of `rotation` are the canonical axes written in Fixed_Jaw coordinates.
        self.canonical_from_jaw = np.eye(4)
        self.canonical_from_jaw[:3, :3] = rotation
        self.canonical_from_jaw[:3, 3] = -rotation @ np.array(GripperConfig.CANONICAL_ORIGIN)

    @property
    def jaw_from_canonical(self) -> np.ndarray:
        """Where the grasp frame sits on Fixed_Jaw; the robot needs it to turn a grasp into a wrist target."""
        transform = np.linalg.inv(self.canonical_from_jaw)
        return transform

    def build(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.write_urdf()
        gripper = yourdfpy.URDF.load(str(self.out_dir / "gripper.urdf"), load_collision_meshes=True,
                                     build_collision_scene_graph=True)
        open_cfg = {GripperConfig.JAW_JOINT: GripperConfig.JAW_OPEN}
        gripper.update_cfg(open_cfg)
        vis_mesh = gripper.scene.to_geometry()
        vis_mesh.export(self.out_dir / "vis_mesh.obj")
        gripper.collision_scene.to_geometry().export(self.out_dir / "coll_mesh.obj")

        sweep_open = self.sweep_box(gripper, GripperConfig.JAW_OPEN)
        sweep_half = self.sweep_box(gripper, (GripperConfig.JAW_OPEN + GripperConfig.JAW_CLOSE) / 2)
        config = {
            "open": open_cfg,
            "close": {GripperConfig.JAW_JOINT: GripperConfig.JAW_CLOSE},
            "fingertip": sweep_open["offset"],
            "sweep_volume": {"extents": sweep_open["extents"], "offset": sweep_open["offset"],
                             "extents2": sweep_half["extents"], "offset2": sweep_half["offset"]},
            "links": ["gripper_base", *GripperConfig.LINKS],
            "standoff": [0.0, GripperConfig.SWEEP_DEPTH / 2],
            # One fixed and one moving jaw: a grasp turned 180° about the approach axis is a different grasp.
            "symmetric": False,
            "type": GripperConfig.TYPE,
            "bbox": vis_mesh.bounds.tolist(),
            "base_rotation": np.eye(4).tolist(),
        }
        (self.out_dir / "config.json").write_text(json.dumps(config, indent=4))
        logger.info("夹爪描述已写入 {}：张开扫掠体 {}，半开 {}", self.out_dir, sweep_open, sweep_half)
        return self.out_dir

    def write_urdf(self) -> None:
        """The gripper links and the joints between them, under a gripper_base in the canonical frame."""
        source = ET.parse(self.robot_urdf).getroot()
        robot = ET.Element("robot", name=GripperConfig.NAME)
        ET.SubElement(robot, "link", name="gripper_base")
        root_joint = ET.SubElement(robot, "joint", name="gripper_base_joint", type="fixed")
        ET.SubElement(root_joint, "parent", link="gripper_base")
        ET.SubElement(root_joint, "child", link=GripperConfig.ROOT_LINK)
        rpy = tra.euler_from_matrix(self.canonical_from_jaw, "sxyz")
        ET.SubElement(root_joint, "origin", xyz=" ".join(f"{v:.6f}" for v in self.canonical_from_jaw[:3, 3]),
                      rpy=" ".join(f"{v:.6f}" for v in rpy))

        mesh_dir = self.out_dir / "meshes"
        mesh_dir.mkdir(exist_ok=True)
        for link in source.findall("link"):
            if link.get("name") in GripperConfig.LINKS:
                for mesh in link.iter("mesh"):
                    package_path = mesh.get("filename").replace("package://", "")
                    shutil.copy2(self.package_root / package_path, mesh_dir)
                    mesh.set("filename", f"meshes/{Path(package_path).name}")
                robot.append(link)
        for joint in source.findall("joint"):
            if joint.find("parent").get("link") in GripperConfig.LINKS and joint.find("child").get("link") in GripperConfig.LINKS:
                robot.append(joint)
        tree = ET.ElementTree(robot)
        ET.indent(tree, "  ")
        tree.write(self.out_dir / "gripper.urdf", xml_declaration=True, encoding="utf-8")

    @staticmethod
    def sweep_box(gripper: yourdfpy.URDF, jaw: float) -> dict:
        """The box between the two tips at this opening: X spans tip to tip, Z reaches back from the tips."""
        gripper.update_cfg({GripperConfig.JAW_JOINT: jaw})
        fixed_tip = gripper.get_transform(GripperConfig.FIXED_TIP_LINK, "gripper_base")[:3, 3]
        moving_tip = gripper.get_transform(GripperConfig.MOVING_TIP_LINK, "gripper_base")[:3, 3]
        tip_z = (fixed_tip[2] + moving_tip[2]) / 2
        box = {
            "extents": [round(abs(fixed_tip[0] - moving_tip[0]), 4), GripperConfig.SWEEP_WIDTH, GripperConfig.SWEEP_DEPTH],
            "offset": [round((fixed_tip[0] + moving_tip[0]) / 2, 4), 0.0, round(tip_z - GripperConfig.SWEEP_DEPTH / 2, 4)],
        }
        return box

    def load_config(self) -> dict:
        config = json.loads((self.out_dir / "config.json").read_text())
        return config


def demo_gripper_description() -> None:
    """Builds the real asset (it is generated, never hand-edited) and prints the canonical tips."""
    description = GripperDescription()
    out_dir = description.build()
    gripper = yourdfpy.URDF.load(str(out_dir / "gripper.urdf"))
    for jaw in (GripperConfig.JAW_CLOSE, GripperConfig.JAW_OPEN):
        gripper.update_cfg({GripperConfig.JAW_JOINT: jaw})
        fixed_tip = gripper.get_transform(GripperConfig.FIXED_TIP_LINK, "gripper_base")[:3, 3]
        moving_tip = gripper.get_transform(GripperConfig.MOVING_TIP_LINK, "gripper_base")[:3, 3]
        logger.info("Jaw={}：固定指尖 {} 活动指尖 {}", jaw, np.round(fixed_tip, 4), np.round(moving_tip, 4))
    mesh = trimesh.load(out_dir / "vis_mesh.obj", force="mesh")
    logger.info("vis_mesh 包围盒 {}（+Z 应为接近方向）", np.round(mesh.bounds, 3))
    logger.info("Fixed_Jaw <- 抓取坐标系：\n{}", np.round(description.jaw_from_canonical, 4))


def main() -> None:
    use_utf8_output()
    demo_gripper_description()


if __name__ == "__main__":
    main()
