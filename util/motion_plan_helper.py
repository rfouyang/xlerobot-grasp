"""cuRobo v2 的薄封装：从 URDF 生成机器人配置、批量 IK 可达性、抓取轨迹（接近 → 抓取 → 抬起）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import logging
from dataclasses import dataclass

import numpy as np
import torch
import trimesh.transformations as tra
import yaml
from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
from curobo.robot_builder import RobotBuilder
from curobo.scene import Cuboid, Scene
from curobo.types import GoalToolPose, JointState, Pose, ToolPoseCriteria
from loguru import logger

from config.planning import MotionConfig
from config.settings import PathConfig, use_utf8_output

# cuRobo logs every kernel compile and buffer swap at INFO.
logging.getLogger("curobo").setLevel(logging.WARNING)


@dataclass
class JointTrajectory:
    joint_names: list[str]
    # (T, n) joint positions, one row every dt seconds.
    positions: np.ndarray
    dt: float


class MotionPlanHelper:
    """Plans for one tool frame of a robot config built by build_robot_config().

    The free axis: rotation about the tool frame's own x axis carries no cost. For a 5-DOF arm whose tool
    x axis is the jaw's closing direction, that is the one rotation an antipodal grasp tolerates.
    """

    def __init__(self, robot_config: Path = MotionConfig.ROBOT_CONFIG, tool_frame: str = MotionConfig.TOOL_FRAME,
                 **kwargs) -> None:
        config = MotionPlannerCfg.create(robot=str(robot_config), scene_model={"cuboid": {}},
                                         collision_cache={"cuboid": kwargs.get("max_cuboids", 8)},
                                         num_ik_seeds=kwargs.get("num_ik_seeds", 64))
        self.planner = MotionPlanner(config)
        self.tool_frame = tool_frame
        self.contact_links = kwargs.get("contact_links", MotionConfig.CONTACT_LINKS)
        self.relaxed = ToolPoseCriteria(terminal_pose_axes_weight_factor=list(MotionConfig.POSE_AXES_WEIGHT),
                                        project_distance_to_goal=True)

    @property
    def joint_names(self) -> list[str]:
        names = list(self.planner.joint_names)
        return names

    def set_obstacles(self, cuboids: list[tuple[str, tuple, np.ndarray]]) -> None:
        """[(name, (dx, dy, dz), pose 4x4)] in the robot base frame, replacing the previous ones."""
        boxes = [Cuboid(name=name, dims=[float(d) for d in dims],
                        pose=[*map(float, pose[:3, 3]), *map(float, tra.quaternion_from_matrix(pose))])
                 for name, dims, pose in cuboids]
        self.planner.update_world(Scene(cuboid=boxes))

    def solve_ik(self, pose: np.ndarray, start: np.ndarray) -> np.ndarray | None:
        """Joint solution for the tool frame at pose (rotation about tool x free), or None.
        The contact links may touch the object here, as they will when the jaw closes."""
        self.planner.update_tool_pose_criteria({self.tool_frame: self.relaxed})
        self.planner.disable_link_collision(self.contact_links)
        result = self.planner.ik_solver.solve_pose(self.goal([pose]), current_state=self.joint_state(start))
        self.planner.enable_link_collision(self.contact_links)
        self.planner.update_tool_pose_criteria({self.tool_frame: ToolPoseCriteria()})
        solution = result.solution.view(-1, len(self.joint_names))[0].cpu().numpy() if result.success.item() else None
        return solution

    def tool_pose(self, joints: np.ndarray) -> np.ndarray:
        """Forward kinematics of the tool frame."""
        state = self.planner.compute_kinematics(self.joint_state(joints))
        link = state.tool_poses.get_link_pose(self.tool_frame)
        pose = tra.quaternion_matrix(link.quaternion[0].cpu().numpy())
        pose[:3, 3] = link.position[0].cpu().numpy()
        return pose

    def plan_grasp(self, start: np.ndarray, grasp: np.ndarray, approach_distance: float,
                   lift_height: float) -> dict[str, JointTrajectory] | None:
        """start -> backed off along the tool's approach axis -> in to the grasp -> straight up; None when a
        segment fails.

        Not cuRobo's plan_grasp: it holds all three rotations fixed along the whole linear approach and
        lift (ToolPoseCriteria.linear_motion), which a 5-DOF arm misses (its lift failed on a pose it had
        just reached). Each segment here is a plain plan_pose with one rotation free (MotionConfig.POSE_AXES_WEIGHT).
        """
        backed_off = grasp.copy()
        backed_off[:3, 3] -= approach_distance * grasp[:3, 2]
        lifted = grasp.copy()
        lifted[2, 3] += lift_height
        segments = {}
        segments["approach"] = self.plan_segment(start, backed_off, touching=False)
        if segments["approach"] is None:
            return None
        # From here on the fingers are at (and then around) the object, whose box they enter.
        segments["grasp"] = self.plan_segment(segments["approach"].positions[-1], grasp, touching=True)
        if segments["grasp"] is None:
            return None
        segments["lift"] = self.plan_segment(segments["grasp"].positions[-1], lifted, touching=True)
        if segments["lift"] is None:
            return None
        return segments

    def plan_segment(self, start: np.ndarray, target: np.ndarray, touching: bool) -> JointTrajectory | None:
        self.planner.update_tool_pose_criteria({self.tool_frame: self.relaxed})
        if touching:
            self.planner.disable_link_collision(self.contact_links)
        result = self.planner.plan_pose(self.goal([target]), self.joint_state(start))
        if touching:
            self.planner.enable_link_collision(self.contact_links)
        self.planner.update_tool_pose_criteria({self.tool_frame: ToolPoseCriteria()})
        if result is None or not result.success.any():
            logger.info("cuRobo 规划失败：目标 {}", np.round(target[:3, 3], 3))
            return None
        trajectory = self.trajectory(result.interpolated_trajectory, result.interpolated_last_tstep)
        return trajectory

    def trajectory(self, interpolated, last_step) -> JointTrajectory:
        """The interpolated plan carries every joint (locked ones too); keep the planned ones."""
        last = int(torch.as_tensor(last_step).view(-1)[0].item())
        names = list(interpolated.joint_names)
        positions = interpolated.position.reshape(-1, interpolated.position.shape[-2], len(names))[0, : last + 1]
        columns = [names.index(name) for name in self.joint_names]
        trajectory = JointTrajectory(joint_names=self.joint_names, positions=positions[:, columns].cpu().numpy(),
                                     dt=float(self.planner.trajopt_solver.config.interpolation_dt))
        return trajectory

    def joint_state(self, joints: np.ndarray) -> JointState:
        state = JointState.from_position(torch.tensor(np.asarray(joints, dtype=np.float32)[None], device="cuda"),
                                         joint_names=self.joint_names)
        return state

    def goal(self, poses: list[np.ndarray]) -> GoalToolPose:
        positions = np.stack([pose[:3, 3] for pose in poses]).astype(np.float32)
        quaternions = np.stack([tra.quaternion_from_matrix(pose) for pose in poses]).astype(np.float32)
        goal = GoalToolPose.from_poses({self.tool_frame: Pose(position=torch.tensor(positions, device="cuda"),
                                                              quaternion=torch.tensor(quaternions, device="cuda"))},
                                       num_goalset=len(poses))
        return goal

    @staticmethod
    def build_robot_config(urdf_path: Path, asset_path: Path, tool_frame: str, out_path: Path,
                           lock_joints: dict[str, float], contact_links: list[str], **kwargs) -> Path:
        """Collision spheres + self-collision matrix from the URDF; joints outside the planned arm are locked."""
        builder = RobotBuilder(urdf_path=str(urdf_path), asset_path=str(asset_path), tool_frames=[tool_frame])
        builder.fit_collision_spheres(sphere_density=kwargs.get("sphere_density", 1.0))
        builder.compute_collision_matrix(num_samples=kwargs.get("num_collision_samples", 2000))
        builder.save(builder.build(), str(out_path))
        config = yaml.safe_load(out_path.read_text())
        kinematics = config["kinematics"]
        kinematics["lock_joints"] = lock_joints
        # The fingers may touch the object while grasping; cuRobo ignores these links' collisions then.
        kinematics["grasp_contact_link_names"] = contact_links
        out_path.write_text(yaml.safe_dump(config, sort_keys=False))
        logger.info("cuRobo 机器人配置：{}（{} 个碰撞球）", out_path, builder.num_spheres)
        return out_path


def demo_build_robot_config() -> None:
    """Builds the real right-arm config (generated, never hand-edited)."""
    out_path = MotionConfig.ROBOT_CONFIG
    out_path.parent.mkdir(parents=True, exist_ok=True)
    MotionPlanHelper.build_robot_config(PathConfig.URDF_PATH, PathConfig.ROS_SRC_DIR, MotionConfig.TOOL_FRAME,
                                        out_path, MotionConfig.LOCK_JOINTS, MotionConfig.CONTACT_LINKS)
    config = yaml.safe_load(out_path.read_text())["kinematics"]
    logger.info("cspace 关节：{}；锁定：{}", config["cspace"]["joint_names"], list(config["lock_joints"]))


def demo_plan_grasp() -> None:
    """A reachable pose from the 'ready' joints, nudged 6 cm down: IK, FK round trip, then the 3-phase plan."""
    helper = MotionPlanHelper()
    table = np.eye(4)
    table[:3, 3] = [0.45, 0.0, 0.72]
    helper.set_obstacles([("table", (0.5, 0.8, 0.04), table)])
    start = np.array([0.0, 0.3, 0.3, 0.4, 0.0])
    target = helper.tool_pose(start)
    target[2, 3] -= 0.06
    solution = helper.solve_ik(target, start)
    logger.info("IK：{}", None if solution is None else np.round(solution, 3))
    reached = helper.tool_pose(solution)
    logger.info("FK 位置误差 {:.4f} m", np.linalg.norm(reached[:3, 3] - target[:3, 3]))
    segments = helper.plan_grasp(start, reached, approach_distance=0.05, lift_height=0.08)
    for name, segment in segments.items():
        logger.info("{}：{} 点，dt {}s，终点 {}", name, len(segment.positions), segment.dt,
                    np.round(segment.positions[-1], 3))


def main() -> None:
    use_utf8_output()
    # The config is generated once (demo_build_robot_config); planning reuses it.
    demo_plan_grasp()


if __name__ == "__main__":
    main()
