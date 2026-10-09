"""robot_state_publisher + ros2_control (mock hardware) + controller spawners.

Planning and execution happen inside the xlerobot-grasp service through MoveItPy,
so no move_group is started here.
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    moveit_config = MoveItConfigsBuilder("xlerobot", package_name="xlerobot_moveit_config").to_moveit_configs()
    controllers_file = str(moveit_config.package_path / "config" / "ros2_controllers.yaml")
    controllers = ["joint_state_broadcaster", "right_arm_controller", "right_gripper_controller",
                   "left_arm_controller", "left_gripper_controller", "head_controller"]

    nodes = [
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             parameters=[moveit_config.robot_description]),
        Node(package="controller_manager", executable="ros2_control_node",
             parameters=[controllers_file],
             remappings=[("~/robot_description", "/robot_description")]),
        Node(package="controller_manager", executable="spawner", arguments=controllers),
    ]
    description = LaunchDescription(nodes)
    return description
