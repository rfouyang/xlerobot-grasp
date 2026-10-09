"""MuJoCo 的薄封装：由 URDF 生成 MJCF（位置执行器、可视 / 碰撞分开），加桌面盒子和自由物体，步进、取位姿、离屏渲染。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# First: it sets MUJOCO_GL / PYOPENGL_PLATFORM, which mujoco reads when imported.
from config.settings import PathConfig, use_utf8_output
from config.simulation import SimConfig

import xml.etree.ElementTree as ET

import coacd
import imageio.v3 as iio
import mujoco
import numpy as np
import trimesh
import trimesh.transformations as tra
import yourdfpy
from loguru import logger


def fmt(values) -> str:
    text = " ".join(f"{float(v):.6g}" for v in values)
    return text


class PhysicsSimHelper:
    """Builds one MuJoCo world: robot (fixed base) + static boxes + free mesh objects + cameras.

    Robot geoms collide with the world but never with each other (contype 1 / conaffinity 0): the
    URDF's convex parts overlap at the joints, and the planner already keeps the arm off the cart.
    Every actuated joint gets a position actuator, so the robot follows joint targets the way the
    servos follow ros2_control's commands.
    """

    def __init__(self, urdf_path: Path = PathConfig.URDF_PATH, package_root: Path = PathConfig.ROS_SRC_DIR, **kwargs) -> None:
        self.urdf = yourdfpy.URDF.load(str(urdf_path), load_meshes=False, build_scene_graph=False)
        self.package_root = package_root
        self.cache_dir = kwargs.get("cache_dir", SimConfig.CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.grip_links = set(kwargs.get("grip_links", SimConfig.GRIP_LINKS))
        self.model = None
        self.data = None
        self.renderer = None
        # mesh file (+scale) -> asset name, so a part used twice is loaded once.
        self.meshes: dict[tuple, str] = {}
        self.assets: list[ET.Element] = []

    # --- building ---

    def build(self, boxes: list[dict], objects: list[dict], cameras: dict[str, tuple]) -> None:
        """boxes: {name, size, pose, rgba}; objects: {name, file, scale, pose, mass}; cameras: name -> (position, target)."""
        self.meshes = {}
        self.assets = []
        root = ET.Element("mujoco", model="xlerobot_sim")
        ET.SubElement(root, "compiler", angle="radian", autolimits="true", inertiafromgeom="auto")
        ET.SubElement(root, "option", timestep=str(SimConfig.TIMESTEP), integrator="implicitfast", cone="elliptic",
                      impratio="10")
        visual = ET.SubElement(root, "visual")
        ET.SubElement(visual, "global", offwidth=str(SimConfig.VIDEO_SIZE[0]), offheight=str(SimConfig.VIDEO_SIZE[1]))
        ET.SubElement(visual, "quality", shadowsize="4096")
        # The arm parts are dark grey in the URDF; a little ambient keeps their shape readable on video.
        ET.SubElement(visual, "headlight", ambient="0.35 0.35 0.35", diffuse="0.5 0.5 0.5")
        default = ET.SubElement(root, "default")
        for name, attributes in SimConfig.GEOM_CLASSES.items():
            ET.SubElement(ET.SubElement(default, "default", **{"class": name}), "geom", **attributes)
        asset = ET.SubElement(root, "asset")
        ET.SubElement(asset, "texture", name="floor_tex", type="2d", builtin="checker", rgb1="0.82 0.82 0.8",
                      rgb2="0.72 0.72 0.7", width="512", height="512")
        ET.SubElement(asset, "material", name="floor_mat", texture="floor_tex", texrepeat="8 8")
        ET.SubElement(asset, "texture", name="sky", type="skybox", builtin="gradient", rgb1="0.93 0.95 0.98",
                      rgb2="0.62 0.68 0.76", width="512", height="512")
        world = ET.SubElement(root, "worldbody")
        ET.SubElement(world, "light", pos="0.6 -0.8 2.5", dir="-0.2 0.3 -1", diffuse="0.8 0.8 0.8", castshadow="true")
        ET.SubElement(world, "light", pos="-1 1 2.5", dir="0.4 -0.4 -1", diffuse="0.35 0.35 0.35", castshadow="false")
        ET.SubElement(world, "geom", name="floor", type="plane", size="3 3 0.05", material="floor_mat", **{"class": "world"})
        for box in boxes:
            pose = box["pose"]
            ET.SubElement(world, "geom", name=box["name"], type="box", size=fmt(np.array(box["size"]) / 2),
                          pos=fmt(pose[:3, 3]), quat=fmt(tra.quaternion_from_matrix(pose)), rgba=fmt(box["rgba"]),
                          **{"class": "world"})
        for name, (position, target) in cameras.items():
            ET.SubElement(world, "camera", name=name, pos=fmt(position), xyaxes=fmt(self.look_at(position, target)),
                          fovy=str(SimConfig.CAMERA_FOVY))
        children = {joint.child for joint in self.urdf.robot.joints}
        root_link = next(link.name for link in self.urdf.robot.links if link.name not in children)
        self.add_link(world, root_link)
        for item in objects:
            self.add_object(world, item)
        actuators = ET.SubElement(root, "actuator")
        for joint in self.urdf.robot.joints:
            if joint.type in ("revolute", "prismatic"):
                gains = SimConfig.ACTUATOR_GAINS.get(joint.name, SimConfig.ACTUATOR_GAINS["default"])
                ET.SubElement(actuators, "position", name=joint.name, joint=joint.name, kp=str(gains[0]),
                              dampratio="1", forcerange=f"{-gains[1]} {gains[1]}",
                              ctrlrange=fmt([joint.limit.lower, joint.limit.upper]))
        for element in self.assets:
            asset.append(element)
        xml = ET.tostring(root, encoding="unicode")
        (self.cache_dir / "scene.xml").write_text(xml)
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        self.renderer = None

    def add_link(self, parent: ET.Element, link_name: str) -> None:
        link = self.urdf.link_map[link_name]
        if link.inertial is not None and link.inertial.mass > 0:
            inertia = link.inertial.inertia
            ET.SubElement(parent, "inertial", pos=fmt(link.inertial.origin[:3, 3]), mass=str(link.inertial.mass),
                          fullinertia=fmt([inertia[0, 0], inertia[1, 1], inertia[2, 2], inertia[0, 1], inertia[0, 2], inertia[1, 2]]))
        for visual in link.visuals:
            self.add_geom(parent, visual, "visual", self.visual_color(visual))
        geom_class = "grip" if link_name in self.grip_links else "robot"
        for collision in link.collisions:
            self.add_geom(parent, collision, geom_class, None)
        for joint in self.urdf.robot.joints:
            if joint.parent != link_name:
                continue
            origin = joint.origin if joint.origin is not None else np.eye(4)
            body = ET.SubElement(parent, "body", name=joint.child, pos=fmt(origin[:3, 3]),
                                 quat=fmt(tra.quaternion_from_matrix(origin)))
            if joint.type in ("revolute", "continuous", "prismatic"):
                attributes = {"name": joint.name, "type": "slide" if joint.type == "prismatic" else "hinge",
                              "axis": fmt(joint.axis), "damping": str(SimConfig.JOINT_DAMPING),
                              "armature": str(SimConfig.JOINT_ARMATURE)}
                if joint.type != "continuous":
                    attributes["range"] = fmt([joint.limit.lower, joint.limit.upper])
                ET.SubElement(body, "joint", **attributes)
            self.add_link(body, joint.child)

    def add_geom(self, body: ET.Element, element, geom_class: str, color) -> None:
        geometry = element.geometry
        origin = element.origin if element.origin is not None else np.eye(4)
        attributes = {"pos": fmt(origin[:3, 3]), "quat": fmt(tra.quaternion_from_matrix(origin)), "class": geom_class}
        if color is not None and geom_class == "visual":
            attributes["rgba"] = fmt(color)
        if geometry.mesh is not None:
            scale = tuple(geometry.mesh.scale) if geometry.mesh.scale is not None else (1.0, 1.0, 1.0)
            attributes.update(type="mesh", mesh=self.mesh_asset(geometry.mesh.filename, scale))
        elif geometry.box is not None:
            attributes.update(type="box", size=fmt(np.array(geometry.box.size) / 2))
        elif geometry.cylinder is not None:
            attributes.update(type="cylinder", size=fmt([geometry.cylinder.radius, geometry.cylinder.length / 2]))
        elif geometry.sphere is not None:
            attributes.update(type="sphere", size=str(geometry.sphere.radius))
        ET.SubElement(body, "geom", **attributes)

    def visual_color(self, visual):
        """The XLeRobot URDF declares its materials once at the top and refers to them by name."""
        material = visual.material
        if material is None:
            return None
        if material.color is not None:
            color = material.color.rgba
            return color
        named = {item.name: item.color.rgba for item in self.urdf.robot.materials if item.color is not None}
        color = named.get(material.name)
        return color

    def mesh_asset(self, filename: str, scale: tuple) -> str:
        """MuJoCo reads STL and OBJ; the URDF's PLY collision parts are converted once into the cache."""
        key = (filename, scale)
        if key not in self.meshes:
            path = self.package_root / filename.replace("package://", "")
            if path.suffix.lower() == ".ply":
                converted = self.cache_dir / f"{path.stem}.stl"
                if not converted.exists():
                    trimesh.load(path, force="mesh").export(converted)
                path = converted
            name = f"mesh_{len(self.meshes)}"
            self.assets.append(ET.Element("mesh", name=name, file=str(path), scale=fmt(scale)))
            self.meshes[key] = name
        name = self.meshes[key]
        return name

    def add_object(self, world: ET.Element, item: dict) -> None:
        """A free body: the textured mesh to look at, its convex hull to collide with."""
        name = item["name"]
        mesh = trimesh.load(item["file"], force="mesh")
        mesh.apply_scale(item["scale"])
        mesh_path = self.cache_dir / f"{name}.obj"
        mesh.export(mesh_path)
        mesh_attributes = {"name": f"{name}_mesh", "file": str(mesh_path)}
        geom_attributes = {"type": "mesh", "mesh": f"{name}_mesh", "class": "visual"}
        image = getattr(mesh.visual, "material", None)
        image = getattr(image, "image", None)
        if image is not None:
            texture_path = self.cache_dir / f"{name}.png"
            image.convert("RGB").save(texture_path)
            self.assets.append(ET.Element("texture", name=f"{name}_tex", type="2d", file=str(texture_path)))
            self.assets.append(ET.Element("material", name=f"{name}_mat", texture=f"{name}_tex"))
            geom_attributes["material"] = f"{name}_mat"
        else:
            geom_attributes["rgba"] = fmt(item.get("rgba", (0.9, 0.75, 0.2, 1.0)))
        self.assets.append(ET.Element("mesh", **mesh_attributes))
        pose = item["pose"]
        body = ET.SubElement(world, "body", name=name, pos=fmt(pose[:3, 3]), quat=fmt(tra.quaternion_from_matrix(pose)))
        ET.SubElement(body, "freejoint", name=f"{name}_free")
        ET.SubElement(body, "geom", **geom_attributes)
        parts = self.convex_parts(mesh, f"{Path(item['file']).stem}_{item['scale']:g}")
        volumes = np.array([part.volume for part in parts])
        for index, part in enumerate(parts):
            part_path = self.cache_dir / f"{name}_part{index}.obj"
            part.export(part_path)
            self.assets.append(ET.Element("mesh", name=f"{name}_part{index}", file=str(part_path)))
            ET.SubElement(body, "geom", type="mesh", mesh=f"{name}_part{index}",
                          mass=str(item["mass"] * volumes[index] / volumes.sum()), **{"class": "object"})

    def convex_parts(self, mesh: trimesh.Trimesh, key: str) -> list[trimesh.Trimesh]:
        """MuJoCo collides a mesh as its convex hull, which fills a curved object's hollow; CoACD splits
        the object into convex pieces that follow its shape. Cached per object file and scale."""
        part_dir = self.cache_dir / "convex_parts" / key
        if not part_dir.exists():
            part_dir.mkdir(parents=True)
            pieces = coacd.run_coacd(coacd.Mesh(mesh.vertices, mesh.faces), threshold=SimConfig.CONVEX_THRESHOLD)
            for index, (vertices, faces) in enumerate(pieces):
                trimesh.Trimesh(vertices, faces).export(part_dir / f"part{index}.obj")
        parts = [trimesh.load(path, force="mesh") for path in sorted(part_dir.glob("part*.obj"))]
        return parts

    @staticmethod
    def look_at(position, target) -> np.ndarray:
        """MuJoCo cameras look down their -z with +y up; xyaxes are the camera's x and y in world."""
        forward = np.array(target, float) - np.array(position, float)
        forward /= np.linalg.norm(forward)
        x_axis = np.cross(forward, [0.0, 0.0, 1.0])
        x_axis /= np.linalg.norm(x_axis)
        y_axis = np.cross(x_axis, forward)
        axes = np.concatenate([x_axis, y_axis])
        return axes

    # --- running ---

    def set_joints(self, positions: dict[str, float]) -> None:
        """Teleports joints (and their actuator targets), e.g. to start from the robot's current state."""
        for name, value in positions.items():
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if joint_id >= 0:
                self.data.qpos[self.model.jnt_qposadr[joint_id]] = value
        self.set_targets(positions)
        mujoco.mj_forward(self.model, self.data)

    def set_targets(self, positions: dict[str, float]) -> None:
        for name, value in positions.items():
            actuator_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if actuator_id >= 0:
                self.data.ctrl[actuator_id] = value

    def step(self, count: int = 1) -> None:
        mujoco.mj_step(self.model, self.data, nstep=count)

    def state(self) -> np.ndarray:
        """Positions of everything (robot joints and free objects): enough to draw a frame again."""
        positions = self.data.qpos.copy()
        return positions

    def set_state(self, positions: np.ndarray) -> None:
        self.data.qpos[:] = positions
        mujoco.mj_forward(self.model, self.data)

    def joint_positions(self, names: list[str]) -> dict[str, float]:
        positions = {name: float(self.data.joint(name).qpos[0]) for name in names}
        return positions

    def body_pose(self, name: str) -> np.ndarray:
        pose = tra.quaternion_matrix(self.data.body(name).xquat)
        pose[:3, 3] = self.data.body(name).xpos
        return pose

    def render(self, camera: str) -> np.ndarray:
        """RGB frame; the renderer is created on first use, in the calling thread (EGL contexts are per thread)."""
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, SimConfig.VIDEO_SIZE[1], SimConfig.VIDEO_SIZE[0])
        self.renderer.update_scene(self.data, camera=camera)
        frame = self.renderer.render()
        return frame


def demo_physics_sim() -> None:
    """Pudding box on the table, right arm swinging down over it; writes a short clip and a still."""
    sim = PhysicsSimHelper()
    table = tra.translation_matrix([0.45, 0.0, 0.72])
    box_pose = tra.translation_matrix([0.256, -0.13, 0.741])
    sim.build(boxes=[{"name": "table", "size": (0.5, 0.8, 0.04), "pose": table, "rgba": (0.8, 0.7, 0.55, 1)}],
              objects=[{"name": "object", "file": BASE_DIR / "asset" / "objects" / "ChocolatePudding.obj",
                        "scale": 0.001, "pose": box_pose @ tra.rotation_matrix(np.pi / 2, [1, 0, 0]), "mass": 0.1}],
              cameras={"observer": SimConfig.OBSERVER_CAMERA})
    sim.set_joints({"Pitch": 0.3, "Elbow": 0.3, "Wrist_Pitch": 0.4})
    out_dir = PathConfig.DEMO_DIR / "physics_sim"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    steps_per_frame = int(1 / (SimConfig.VIDEO_FPS * SimConfig.TIMESTEP))
    for frame_index in range(60):
        sim.set_targets({"Pitch": 0.3 + 0.4 * frame_index / 60, "Jaw": 0.8})
        sim.step(steps_per_frame)
        frames.append(sim.render("observer"))
    iio.imwrite(out_dir / "swing.mp4", frames, fps=SimConfig.VIDEO_FPS)
    iio.imwrite(out_dir / "last.png", frames[-1])
    logger.info("物体位姿 z={:.3f}；Pitch={:.3f}；视频 {}", sim.body_pose("object")[2, 3],
                sim.joint_positions(["Pitch"])["Pitch"], out_dir / "swing.mp4")


def main() -> None:
    use_utf8_output()
    demo_physics_sim()


if __name__ == "__main__":
    main()
