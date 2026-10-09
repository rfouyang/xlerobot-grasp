"""真机抓取规划（不动机器人）：头部 RGB-D 里的瓶子 → 点云和圆柱 → GraspGenX → 侧抓瓶身中段 → cuRobo 可达与轨迹。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

import cv2
import numpy as np
import trimesh
import trimesh.transformations as tra
import yaml
import yourdfpy
from loguru import logger

from component.planning.grasp_selection import GraspSelection
from component.planning.reachability import GraspReachability, ReachableGrasp
from component.grasp_generation.grasp_prediction import GraspPrediction
from component.grasp_generation.tabletop_scene import PlacedObject, TabletopScene
from component.perception.service import Perception, Target
from component.planning.arm import RIGHT, Arm
from component.calibration.check import CalibrationCheck, head_camera_pose
from config.planning import GripperConfig, MotionConfig, RealPlanningConfig
from config.settings import PathConfig, use_utf8_output
from util.joint_mapping_helper import JointMappingHelper
from util.motion_plan_helper import JointTrajectory, MotionPlanHelper
from util.teleop_helper import TeleopHelper

RIGHT_ARM = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll")


@dataclass
class RealScene:
    target: Target
    # Bottle points in base_link (the camera's side of it), and the cylinder fitted to them.
    points: np.ndarray
    placed: PlacedObject
    scene: TabletopScene
    # Every joint of the robot as it is now, in URDF radians.
    urdf_joints: dict[str, float]


class RealGraspPlanning:
    """The simulation pipeline on the real robot's data. Differences: the object comes from YOLOE's mask and the
    depth (only the side facing the camera), fitted with an upright cylinder for selection and collision; the table
    height and front edge come from the depth; cuRobo plans with the left arm and the head locked where they really
    are (the shared config locks them at 0: the left arm straight forward, 13 cm from the bottle)."""

    def __init__(self, teleop: TeleopHelper, **kwargs) -> None:
        self.teleop = teleop
        self.mapping = JointMappingHelper()
        self.perception = kwargs.get("perception") or Perception(teleop)
        # GraspGenX is large; the simulation's predictor can be shared.
        self.prediction = kwargs.get("prediction") or GraspPrediction()
        self.selection = GraspSelection()
        from util.demo_preferences import DemoPreferences
        self.preferences = DemoPreferences()
        self.urdf = yourdfpy.URDF.load(str(PathConfig.URDF_PATH), load_meshes=False, build_scene_graph=True)
        self.planner: MotionPlanHelper | None = None
        # Every arm motion is checked against the desk (component/real_robot/desk_guard.py); its plane is measured here.
        from component.planning.desk_guard import DeskGuard

        self.guard = DeskGuard()
        # The arm the last plan was for (the planner, to_real and draw follow it).
        self.arm: Arm = RIGHT
        self.reachability: GraspReachability | None = None

    # --- the scene ---

    def observe(self) -> RealScene:
        target = self.perception.locate()
        if target is None:
            where = f"（点选位置 {tuple(round(c) for c in self.perception.point)} 附近）" if self.perception.point is not None else ""
            raise RuntimeError(f"头部画面里没找到 {' / '.join(self.perception.prompts)}{where}（头是否在抓取位、物体是否在视野里）")
        frame = target.frame
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in status["joints"].items()}
        real["head"] = {"head_motor_1": float(frame["head_joints"][0]), "head_motor_2": float(frame["head_joints"][1])}
        urdf_joints = self.mapping.to_urdf(real)
        self.urdf.update_cfg(urdf_joints)
        base_from_camera = head_camera_pose(self.urdf)
        all_points = self.back_project(frame, base_from_camera)
        table_z, table_front_x = self.table(all_points, target)
        desk = all_points[(np.abs(all_points[:, 2] - table_z) < 0.008) & (all_points[:, 0] > 0.1) & (all_points[:, 0] < 0.6) & (np.abs(all_points[:, 1]) < 0.4)]
        table_plane = None
        if len(desk) > 2000:
            self.guard.set_plane(desk[:: max(1, len(desk) // 20000)])
            table_plane = (self.guard.normal, self.guard.origin)
        bottle = self.back_project(frame, base_from_camera, target.detection.mask)
        bottle = bottle[bottle[:, 2] > table_z + RealPlanningConfig.ABOVE_TABLE_M]
        placed = self.cylinder(bottle, table_z, base_from_camera[:3, 3])
        # Strays off the bottle (LingBot-Depth's fill at the mask's edge, between the bottle and what is behind it) go,
        # and the cylinder is fitted again on what is left.
        axis_xy = placed.mesh.bounds.mean(axis=0)[:2]
        radius = (placed.mesh.bounds[1, 0] - placed.mesh.bounds[0, 0]) / 2
        on_bottle = np.linalg.norm(bottle[:, :2] - axis_xy, axis=1) < RealPlanningConfig.STRAY_RADIUS_SHARE * radius
        if not on_bottle.all():
            logger.info("去掉离瓶轴超过 {} 倍半径的散点 {} 个", RealPlanningConfig.STRAY_RADIUS_SHARE, int((~on_bottle).sum()))
            bottle = bottle[on_bottle]
            placed = self.cylinder(bottle, table_z, base_from_camera[:3, 3])
        scene = TabletopScene(table_top_z=table_z, table_front_x=table_front_x, table_size=RealPlanningConfig.TABLE_SIZE, table_plane=table_plane)
        logger.info("瓶子 {} 点，圆柱轴 xy {}，半径 {:.3f} m，高 {:.3f} m；桌面 z {:.3f}，前沿 x {:.3f}", len(bottle),
                    np.round(placed.mesh.bounds.mean(axis=0)[:2], 3), (placed.mesh.bounds[1, 0] - placed.mesh.bounds[0, 0]) / 2,
                    placed.mesh.bounds[1, 2] - table_z, table_z, table_front_x)
        real_scene = RealScene(target=target, points=bottle, placed=placed, scene=scene, urdf_joints=urdf_joints)
        return real_scene

    @staticmethod
    def back_project(frame: dict, base_from_camera: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
        depth = frame["depth"] * frame["depth_scale"]
        valid = depth > 0 if mask is None else mask & (depth > 0)
        rows, cols = np.nonzero(valid)
        z = depth[rows, cols]
        camera = (np.linalg.inv(frame["K"]) @ np.stack([cols, rows, np.ones_like(cols)])) * z
        points = (base_from_camera @ np.vstack([camera, np.ones(len(z))]))[:3].T
        return points

    @staticmethod
    def table(points: np.ndarray, target: Target) -> tuple[float, float]:
        """The table top: the most common height among points in front of the robot below the bottle's top; its front
        edge: where those points start along x, but in front of the cart (MIN_TABLE_FRONT_X)."""
        candidates = points[(points[:, 0] > 0.1) & (points[:, 2] > 0.5) & (points[:, 2] < 1.0)]
        heights, edges = np.histogram(candidates[:, 2], bins=np.arange(0.5, 1.0, 0.004))
        table_z = float(edges[np.argmax(heights)] + 0.002)
        on_table = candidates[np.abs(candidates[:, 2] - table_z) < 0.006]
        front_x = max(float(np.percentile(on_table[:, 0], 1)), RealPlanningConfig.MIN_TABLE_FRONT_X)
        return table_z, front_x

    @staticmethod
    def cylinder(points: np.ndarray, table_z: float, camera_position: np.ndarray) -> PlacedObject:
        """An upright cylinder from the camera's view of the bottle: its width across the view gives the radius,
        its nearest surface plus that radius the axis."""
        towards = points[:, :2].mean(axis=0) - camera_position[:2]
        towards /= np.linalg.norm(towards)
        across = np.array([-towards[1], towards[0]])
        side = points[:, :2] @ across
        low, high = np.percentile(side, [2, 98])
        radius = (high - low) / 2
        middle = np.abs(side - (low + high) / 2) < radius / 2
        front = float(np.percentile(points[middle, :2] @ towards, 10))
        axis_xy = (low + high) / 2 * across + (front + radius) * towards
        top_z = float(np.percentile(points[:, 2], 98))
        mesh = trimesh.creation.cylinder(radius=radius, height=top_z - table_z, sections=48)
        mesh.apply_translation([axis_xy[0], axis_xy[1], (top_z + table_z) / 2])
        placed = PlacedObject(name="bottle", label="瓶子", mesh=mesh, pose=tra.translation_matrix([*axis_xy, table_z]),
                              file=Path(), scale=1.0, rgba=(0.6, 0.8, 0.9, 0.8), mass=0.1)
        return placed

    # --- planning ---

    def prepare_planner(self, urdf_joints: dict[str, float], arm: Arm = RIGHT) -> None:
        """A copy of the shared cuRobo config with the joints it does not plan locked at their real values.

        The self-collision matrix was sampled with those joints moving, so it does not ignore pairs that touch in
        the real pose (the folded left arm, the lowered head): cuRobo then sees a collision in every state and no
        IK succeeds (0/137, against 137/137 with them at 0). Parts that cannot move relative to each other while
        planning are ignored against each other; the right arm keeps every check against them."""
        config = yaml.safe_load(arm.robot_config.read_text())
        kinematics = config["kinematics"]
        lock = kinematics["lock_joints"]
        for joint in lock:
            if joint in urdf_joints and joint != arm.jaw:
                limits = self.urdf.joint_map[joint].limit
                lock[joint] = float(min(max(urdf_joints[joint], limits.lower), limits.upper))
        moving = {arm.link(link) for link in RealPlanningConfig.MOVING_LINKS}
        static = [link for link in kinematics["collision_link_names"] if link not in moving]
        ignore = kinematics.setdefault("self_collision_ignore", {})
        for index, link in enumerate(static):
            others = ignore.setdefault(link, [])
            others.extend(other for other in static[index + 1:] if other not in others)
        for link, other in arm.ignore_pairs:
            ignore.setdefault(link, []).append(other)
        arm.real_config.write_text(yaml.safe_dump(config, sort_keys=False))
        self.planner = MotionPlanHelper(robot_config=arm.real_config, tool_frame=arm.tcp,
                                        contact_links=[arm.link("Fixed_Jaw"), arm.link("Moving_Jaw")])
        self.reachability = GraspReachability(self.planner)
        logger.info("cuRobo 锁定关节（真机当前）：{}", {joint: round(value, 3) for joint, value in lock.items()})

    def plan(self, real_scene: RealScene, arm: Arm = RIGHT) -> tuple[ReachableGrasp, dict[str, JointTrajectory]] | None:
        self.arm = arm
        self.urdf.update_cfg(real_scene.urdf_joints)
        self.preferences.prepare(real_scene, np.linalg.inv(head_camera_pose(self.urdf)))
        self.prepare_planner(real_scene.urdf_joints, arm)
        candidates = self.prediction.predict(real_scene.points, real_scene.scene)
        box = real_scene.placed.mesh.bounding_box_oriented
        table_pose = real_scene.scene.table_pose.copy()
        table_pose[2, 3] += arm.table_clearance_m
        # Kept: the pre-grasp correction moves them into the model's frame and plans again.
        self.obstacles = [("table", tuple(real_scene.scene.table_size), table_pose),
                          ("object", tuple(box.primitive.extents), box.primitive.transform)]
        self.planner.set_obstacles(self.obstacles)
        start = self.within_limits(arm, np.array([real_scene.urdf_joints[joint] for joint in arm.joints]))
        # Thicker drinks go deeper into the jaw: closing on a wide body near the fingertips squeezed it out.
        radius = (real_scene.placed.mesh.bounds[1, 0] - real_scene.placed.mesh.bounds[0, 0]) / 2
        (small_r, small_back), (large_r, large_back) = RealPlanningConfig.DEPTH_BACK_BY_RADIUS
        back_m = float(np.interp(radius, [small_r, large_r], [small_back, large_back])) - arm.reach_extra_m
        logger.info("瓶子半径 {:.1f} cm：抓取点往回收 {:.1f} cm", radius * 100, back_m * 100)
        seated = self.selection.seat(candidates, real_scene.placed, side_gap=RealPlanningConfig.SIDE_GAP_M,
                                     raise_m=RealPlanningConfig.SAG_RAISE_M, back_m=back_m,
                                     band=RealPlanningConfig.GRASP_BAND, highest_first=True, shoulder_xy=arm.shoulder_xy)
        level = self.selection.level(self.reachability.filter(seated, start))
        ordered = [grasp for grasp in level if self.roll_ok(np.array([grasp.joints]))]
        logger.info("候选 {} → 侧抓中段 {} → {}能水平够到 {} → 腕部转动在真机范围内 {}", len(candidates), len(seated), arm.label, len(level), len(ordered))
        import json
        gripper = json.loads((GripperConfig.DIR / "config.json").read_text())
        ordered = self.preferences.order(ordered, gripper)
        for grasp in ordered[: RealPlanningConfig.MAX_PLAN_TRIES]:
            segments = self.planner.plan_grasp(start, grasp.tool_pose, MotionConfig.APPROACH_DISTANCE, MotionConfig.LIFT_HEIGHT)
            if segments is not None and all(self.roll_ok(segment.positions) for segment in segments.values()):
                clear, rejection = self.guard.check_segments(arm, segments, real_scene.urdf_joints,
                                                             (GripperConfig.JAW_CLOSE, GripperConfig.JAW_OPEN))
                if not clear:
                    self.preferences.report.setdefault("desk_rejections", []).append({"candidate": grasp.index, **rejection})
                    logger.info("跳过抓取 #{}：{} / {} 离桌面 {:.1f} cm，小于 {:.1f} cm", grasp.index,
                                rejection["segment"], rejection["link"], rejection["clearance_m"] * 100,
                                rejection["required_m"] * 100)
                    continue
                self.preferences.report["selected"] = {"candidate": grasp.index, "score": float(grasp.score)}
                if self.preferences.report.get("ranked"):
                    self.preferences.report["selected"].update(next((r for r in self.preferences.report["ranked"] if r.get("candidate") == grasp.index), {}))
                return grasp, segments
        return None

    def within_limits(self, arm: Arm, joints: np.ndarray) -> np.ndarray:
        """joints clipped into the URDF limits: cuRobo refuses a start outside them, and the folded rest pose sits on the
        elbow's (the left arm's, 3.33 against 3.30 rad after its calibration, planned nothing; 2026-10-09). The real
        arm starts within LOADED_ARRIVED_DEG of the clipped start, which each step checks anyway."""
        limits = np.array([[self.urdf.joint_map[joint].limit.lower, self.urdf.joint_map[joint].limit.upper] for joint in arm.joints])
        clipped = np.clip(joints, limits[:, 0] + 1e-3, limits[:, 1] - 1e-3)
        return clipped

    def roll_ok(self, rows: np.ndarray) -> bool:
        """Every row's real wrist roll inside ROLL_RANGE_DEG (rows: right-arm URDF joints)."""
        low, high = self.arm.roll_range_deg
        rolls = [self.mapping.to_real({self.arm.link("Wrist_Roll"): float(row[4])})[self.arm.part]["wrist_roll"] for row in rows]
        inside = all(low <= roll <= high for roll in rolls)
        return inside

    def to_real(self, segments: dict[str, JointTrajectory]) -> dict[str, np.ndarray]:
        """Each segment's right-arm rows in real degrees (shoulder_pan .. wrist_roll)."""
        real = {}
        for name, segment in segments.items():
            rows = []
            for row in segment.positions:
                arm = self.mapping.to_real(dict(zip(self.arm.joints, map(float, row))))[self.arm.part]
                rows.append([arm[motor] for motor in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")])
            real[name] = np.array(rows)
        return real

    def draw(self, real_scene: RealScene, grasp: ReachableGrasp, segments: dict[str, JointTrajectory], check: CalibrationCheck) -> Path:
        """The gripper at the grasp, and the tool path, projected into the head image."""
        frame = real_scene.target.frame
        real = self.mapping.to_real({**real_scene.urdf_joints, **dict(zip(self.arm.joints, map(float, grasp.joints)))})
        image, _ = check.project(real, frame)
        self.urdf.update_cfg(real_scene.urdf_joints)
        camera_from_base = np.linalg.inv(head_camera_pose(self.urdf))
        colors = {"approach": (255, 200, 0), "grasp": (0, 220, 0), "lift": (0, 140, 255)}
        for name, segment in segments.items():
            tips = np.array([self.planner.tool_pose(row)[:3, 3] for row in segment.positions[:: max(1, len(segment.positions) // 30)]])
            camera = (camera_from_base @ np.hstack([tips, np.ones((len(tips), 1))]).T)[:3]
            uv = (frame["K"] @ camera)[:2] / camera[2]
            cv2.polylines(image, [uv.T.astype(np.int32)], False, colors[name], 2)
        report = self.preferences.report
        pixel = report.get("prior", {}).get("pixel")
        if pixel:
            centre = tuple(np.round(pixel).astype(int))
            cv2.drawMarker(image, centre, (255, 0, 255), cv2.MARKER_CROSS, 28, 3)
            cv2.putText(image, "GraspMolmo", (centre[0]+12, centre[1]-12), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 0, 255), 2)
        selected_pixel = report.get("selected", {}).get("pixel")
        if selected_pixel:
            centre = tuple(np.round(selected_pixel).astype(int))
            cv2.circle(image, centre, 9, (0, 255, 255), 2)
            if pixel:
                cv2.line(image, tuple(np.round(pixel).astype(int)), centre, (0, 255, 255), 1)
        out = PathConfig.DEMO_DIR / "real_grasp_planning"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "plan.jpg"
        cv2.imwrite(str(path), image)
        return path


def demo_real_grasp_planning() -> None:
    """Needs the teleop service, the robot at the grasp start pose. Nothing moves: the plan is drawn and printed."""
    teleop = TeleopHelper()
    planning = RealGraspPlanning(teleop)
    real_scene = planning.observe()
    result = planning.plan(real_scene)
    if result is None:
        logger.warning("没有规划出右臂的抓取")
        return
    grasp, segments = result
    tcp = grasp.tool_pose[:3, 3]
    logger.info("选中抓取 #{}（分数 {:.3f}）：指尖中心 {}，接近方向 {}", grasp.index, grasp.score, np.round(tcp, 3), np.round(grasp.tool_pose[:3, 2], 2))
    real = planning.to_real(segments)
    for name, rows in real.items():
        logger.info("{:8s} {:.1f} s，终点（真机度）pan/lift/elbow/flex/roll {}", name, len(rows) * segments[name].dt, np.round(rows[-1], 1))
    span = np.vstack(list(real.values()))
    logger.info("整段真机关节范围：最小 {}，最大 {}", np.round(span.min(axis=0), 1), np.round(span.max(axis=0), 1))
    path = planning.draw(real_scene, grasp, segments, CalibrationCheck(teleop))
    logger.info("投影图：{}", path)


def main() -> None:
    use_utf8_output()
    demo_real_grasp_planning()


if __name__ == "__main__":
    main()
