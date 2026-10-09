"""单臂键盘遥操作：XLeRobot 的方式（臂平面内指尖 x/y 走 IK，再加 pitch、pan、roll、夹爪），速度按每秒算。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import math

from loguru import logger

from config.robot import RobotConfig, TeleopConfig

from config.settings import use_utf8_output

# XLeRobot's SO-101 geometry (lerobot/model/SO101Robot.py): upper and lower arm lengths, and the joint offsets
# between the links' straight lines and the joint zero positions.
UPPER_ARM = 0.1159
LOWER_ARM = 0.1350
SHOULDER_OFFSET = math.atan2(0.028, 0.11257)
ELBOW_OFFSET = math.atan2(0.0052, 0.1349) + SHOULDER_OFFSET


def tip_from_joints(lift_deg: float, elbow_deg: float) -> tuple[float, float]:
    """Wrist position (x forward, y up) in the arm's plane: the inverse of XLeRobot's IK. Not XLeRobot's own
    forward_kinematics, which adds the elbow angle where its IK subtracts it (its teleop never calls it): for
    this arm's rest pose it puts the wrist 24 cm behind the shoulder. Checked against xlerobot.urdf: at lift 0,
    elbow 0 this gives (0.1629, 0.1177), the URDF (0.163, 0.118) and XLeRobot's start point (0.1629, 0.1131)."""
    theta1 = math.radians(90 - lift_deg) - SHOULDER_OFFSET
    theta2 = math.radians(elbow_deg + 90) - ELBOW_OFFSET
    x = UPPER_ARM * math.cos(theta1) + LOWER_ARM * math.cos(theta1 - theta2)
    y = UPPER_ARM * math.sin(theta1) + LOWER_ARM * math.sin(theta1 - theta2)
    return x, y


def joints_from_tip(x: float, y: float) -> tuple[float, float]:
    """XLeRobot's inverse kinematics without its URDF clamps (elbow <= 90°): this arm's calibrated range
    reaches 97° and its rest pose sits at 96°, so those clamps would jump the elbow on the first key."""
    r = min(max(math.hypot(x, y), abs(UPPER_ARM - LOWER_ARM) + 1e-6), UPPER_ARM + LOWER_ARM - 1e-6)
    cos_theta2 = -(r**2 - UPPER_ARM**2 - LOWER_ARM**2) / (2 * UPPER_ARM * LOWER_ARM)
    theta2 = math.pi - math.acos(max(-1.0, min(1.0, cos_theta2)))
    theta1 = math.atan2(y, x) + math.atan2(LOWER_ARM * math.sin(theta2), UPPER_ARM + LOWER_ARM * math.cos(theta2))
    lift_deg = 90 - math.degrees(theta1 + SHOULDER_OFFSET)
    elbow_deg = math.degrees(theta2 + ELBOW_OFFSET) - 90
    return lift_deg, elbow_deg


class ArmTeleop:
    """Targets for one arm. Starts from where the arm is (sync), never from XLeRobot's fixed start point, and
    every step is limited per second, so neither a key nor the IK can make the arm jump."""

    def __init__(self, prefix: str, keymap: dict[str, str], limits: dict[str, tuple[float, float]], **kwargs) -> None:
        self.prefix = prefix
        self.keymap = keymap
        # joint -> (low, high), from the calibration.
        self.limits = {joint: limits[f"{prefix}_arm_{joint}"] for joint in RobotConfig.ARM_JOINTS}
        self.ik_joint_speed = kwargs.get("ik_joint_speed", TeleopConfig.IK_JOINT_SPEED_DEG_S)
        self.target: dict[str, float] = {}
        self.x = 0.0
        self.y = 0.0
        self.pitch = 0.0
        # A pose the targets walk to (go to zero, rest pose); None while the keys drive.
        self.goal: dict[str, float] | None = None

    def sync(self, present: dict[str, float]) -> None:
        """present: joint -> position. The tip and pitch follow from the joints (XLeRobot couples
        wrist_flex = pitch - shoulder_lift - elbow_flex)."""
        self.target = dict(present)
        self.x, self.y = tip_from_joints(present["shoulder_lift"], present["elbow_flex"])
        self.pitch = present["wrist_flex"] + present["shoulder_lift"] + present["elbow_flex"]
        self.goal = None

    def step(self, pressed: set[str], dt: float) -> None:
        held = {action: code in pressed for action, code in self.keymap.items()}
        if any(held.values()):
            self.goal = None
        if self.goal is not None:
            self.step_goal(dt)
            return
        target = self.target
        target["shoulder_pan"] += TeleopConfig.JOINT_SPEED_DEG_S * dt * (held["pan_right"] - held["pan_left"])
        # wrist_roll + turns the wrist counter-clockwise seen from the wrist towards the fingertips.
        target["wrist_roll"] += TeleopConfig.JOINT_SPEED_DEG_S * dt * (held["roll_ccw"] - held["roll_cw"])
        target["gripper"] += TeleopConfig.GRIPPER_SPEED_S * dt * (held["open"] - held["close"])
        self.pitch += TeleopConfig.PITCH_SPEED_DEG_S * dt * (held["pitch_down"] - held["pitch_up"])
        dx = TeleopConfig.TIP_SPEED_M_S * dt * (held["forward"] - held["back"])
        dy = TeleopConfig.TIP_SPEED_M_S * dt * (held["up"] - held["down"])
        if dx or dy:
            lift, elbow = joints_from_tip(self.x + dx, self.y + dy)
            limit = self.ik_joint_speed * dt
            target["shoulder_lift"] += max(-limit, min(limit, lift - target["shoulder_lift"]))
            target["elbow_flex"] += max(-limit, min(limit, elbow - target["elbow_flex"]))
        target["wrist_flex"] = self.pitch - target["shoulder_lift"] - target["elbow_flex"]
        self.clamp()

    def step_goal(self, dt: float) -> None:
        """Walks to the goal along a straight line in joint space: the joint with the farthest to go moves at the
        homing speed, the others in proportion, so all arrive together. Walked one by one at the same speed, the
        elbow and wrist folded long before the shoulder rose and swept the jaw 1.7 cm into the table (from the
        pose behind the bottle). The gripper closes at its own pace."""
        reach = TeleopConfig.HOMING_SPEED_DEG_S * dt
        joints = [joint for joint in self.target if joint != "gripper"]
        farthest = max(abs(self.goal[joint] - self.target[joint]) for joint in joints)
        share = min(1.0, reach / farthest) if farthest > 0 else 1.0
        for joint in joints:
            self.target[joint] += share * (self.goal[joint] - self.target[joint])
        gripper = self.target["gripper"]
        self.target["gripper"] = gripper + max(-reach, min(reach, self.goal["gripper"] - gripper))
        self.clamp()
        if all(abs(self.goal[joint] - value) < 1e-6 for joint, value in self.target.items()):
            self.goal = None

    def clamp(self) -> None:
        """Keeps every target in its calibrated range; the tip and pitch follow what the joints can do."""
        for joint, (low, high) in self.limits.items():
            self.target[joint] = max(low, min(high, self.target[joint]))
        self.x, self.y = tip_from_joints(self.target["shoulder_lift"], self.target["elbow_flex"])
        self.pitch = self.target["wrist_flex"] + self.target["shoulder_lift"] + self.target["elbow_flex"]

    def limit_lead(self, present: dict[str, float]) -> None:
        """Every target but the gripper's within MAX_LEAD_DEG of its joint."""
        lead = TeleopConfig.MAX_LEAD_DEG
        for joint, value in present.items():
            if joint != "gripper":
                self.target[joint] = max(value - lead, min(value + lead, self.target[joint]))
        self.clamp()

    def step_follow(self, external: dict[str, float], dt: float) -> None:
        """Joint targets from outside (follow mode), approached at FOLLOW_SPEED_DEG_S and kept in range."""
        reach = TeleopConfig.FOLLOW_SPEED_DEG_S * dt
        for joint, value in external.items():
            self.target[joint] += max(-reach, min(reach, value - self.target[joint]))
        self.goal = None
        self.clamp()

    def go_to(self, pose: dict[str, float]) -> None:
        """pose: joint -> position, kept inside the calibrated range so the walk can arrive."""
        self.goal = {joint: max(low, min(high, pose[joint])) for joint, (low, high) in self.limits.items()}

    def action(self) -> dict[str, float]:
        action = {f"{self.prefix}_arm_{joint}.pos": value for joint, value in self.target.items()}
        return action


def demo_arm_teleop() -> None:
    """No robot: today's rest pose of the right arm, the IK round trip there, then 1 s of 'forward' and 1 s of
    'up' at 30 Hz."""
    rest = {"shoulder_pan": -8.5, "shoulder_lift": -94.2, "elbow_flex": 96.2, "wrist_flex": 6.6, "wrist_roll": 3.2, "gripper": 1.6}
    limits = {"shoulder_pan": (-118.3, 118.3), "shoulder_lift": (-104.4, 104.4), "elbow_flex": (-96.8, 96.8),
              "wrist_flex": (-102.6, 102.6), "wrist_roll": (-180.0, 180.0), "gripper": (0.0, 100.0)}
    x, y = tip_from_joints(rest["shoulder_lift"], rest["elbow_flex"])
    lift, elbow = joints_from_tip(x, y)
    logger.info("静止位指尖 x={:.4f} y={:.4f}；IK 回算 lift {:.2f}（原 {}）elbow {:.2f}（原 {}）", x, y, lift, rest["shoulder_lift"], elbow, rest["elbow_flex"])
    keymap = TeleopConfig.KEYMAP["right_arm"]
    arm = ArmTeleop("right", keymap, {f"right_arm_{joint}": value for joint, value in limits.items()})
    arm.sync(rest)
    for action in ("forward", "up"):
        for _ in range(30):
            before = dict(arm.target)
            arm.step({keymap[action]}, 1 / 30)
            jump = max(abs(arm.target[joint] - before[joint]) for joint in before)
        logger.info("按住 {} 1 s：指尖 x={:.4f} y={:.4f}，每步最大关节变化 {:.2f}°，目标 {}", action, arm.x, arm.y, jump,
                    {joint: round(value, 1) for joint, value in arm.target.items()})


def main() -> None:
    use_utf8_output()
    demo_arm_teleop()


if __name__ == "__main__":
    main()
