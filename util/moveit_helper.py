"""MoveItPy 的薄封装：读当前关节、规划并执行到 SRDF 里的命名位姿。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import xml.etree.ElementTree as ET

import numpy as np
from loguru import logger
from moveit.core.robot_trajectory import RobotTrajectory
from moveit.planning import MoveItPy
from moveit_configs_utils import MoveItConfigsBuilder
from moveit_msgs.msg import RobotTrajectory as RobotTrajectoryMsg
from rclpy.duration import Duration
from trajectory_msgs.msg import JointTrajectoryPoint

from config.settings import MoveItConfig, use_utf8_output


class MoveItHelper:
    """Plans and executes in-process; needs the bringup launch (ros2_control) to be running."""

    def __init__(self, package_name: str = MoveItConfig.MOVEIT_PACKAGE, **kwargs) -> None:
        builder = MoveItConfigsBuilder(kwargs.get("robot_name", MoveItConfig.ROBOT_NAME), package_name=package_name)
        builder.moveit_cpp(file_path=kwargs.get("moveit_cpp_file", MoveItConfig.MOVEIT_CPP_FILE))
        config = builder.to_moveit_configs().to_dict()
        self.robot = MoveItPy(node_name=kwargs.get("node_name", MoveItConfig.NODE_NAME), config_dict=config)
        self.monitor = self.robot.get_planning_scene_monitor()
        # group -> names of its <group_state>s, read from the SRDF MoveIt was given.
        self.named_states = self.parse_named_states(config["robot_description_semantic"])

    @staticmethod
    def parse_named_states(srdf: str) -> dict[str, list[str]]:
        named_states = {}
        for state in ET.fromstring(srdf).findall("group_state"):
            named_states.setdefault(state.get("group"), []).append(state.get("name"))
        return named_states

    def joint_positions(self) -> dict[str, float]:
        with self.monitor.read_only() as scene:
            positions = dict(scene.current_state.joint_positions)
        return positions

    def execute_trajectory(self, group: str, joint_names: list[str], positions: np.ndarray, dt: float) -> bool:
        """Runs a trajectory planned elsewhere (cuRobo) through MoveIt's controllers; blocks until done.
        positions (T, n) for joint_names, one row every dt seconds; row 0 must be the current state."""
        message = RobotTrajectoryMsg()
        message.joint_trajectory.joint_names = list(joint_names)
        for step, row in enumerate(positions):
            point = JointTrajectoryPoint()
            point.positions = [float(value) for value in row]
            point.time_from_start = Duration(seconds=step * dt).to_msg()
            message.joint_trajectory.points.append(point)
        trajectory = RobotTrajectory(self.robot.get_robot_model())
        # MoveIt picks the controller by group; without one it finds none.
        trajectory.joint_model_group_name = group
        with self.monitor.read_only() as scene:
            trajectory.set_robot_trajectory_msg(scene.current_state, message)
        status = self.robot.execute(trajectory, controllers=[])
        executed = bool(status)
        return executed

    def move_to_named_state(self, group: str, state: str) -> bool:
        """Blocks until the controller has finished; False when no plan was found."""
        component = self.robot.get_planning_component(group)
        component.set_start_state_to_current_state()
        component.set_goal_state(configuration_name=state)
        result = component.plan()
        if result:
            self.robot.execute(result.trajectory, controllers=[])
        moved = bool(result)
        return moved


def demo_moveit_helper() -> None:
    """Needs `ros2 launch xlerobot_moveit_config bringup.launch.py` running."""
    helper = MoveItHelper(node_name="moveit_helper_demo")
    logger.info("命名位姿：{}", helper.named_states)
    for state in ("ready", "zero"):
        moved = helper.move_to_named_state("right_arm", state)
        arm = {name: round(value, 3) for name, value in helper.joint_positions().items()
               if name in ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll")}
        logger.info("right_arm -> {}：{}，关节 {}", state, "成功" if moved else "失败", arm)


def demo_execute_trajectory() -> None:
    """A 2 s sweep of Rotation from the current pose, as cuRobo would hand it over."""
    helper = MoveItHelper(node_name="moveit_execute_demo")
    names = ["Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll"]
    current = helper.joint_positions()
    start = np.array([current[name] for name in names])
    steps = np.linspace(0.0, 1.0, 81)[:, None]
    positions = start + steps * np.array([0.5, 0.0, 0.0, 0.0, 0.0])
    executed = helper.execute_trajectory("right_arm", names, positions, 0.025)
    logger.info("执行 {}：Rotation {:.3f} -> {:.3f}", "成功" if executed else "失败", start[0],
                helper.joint_positions()["Rotation"])
    helper.execute_trajectory("right_arm", names, positions[::-1], 0.025)


def main() -> None:
    use_utf8_output()
    demo_execute_trajectory()


if __name__ == "__main__":
    main()
