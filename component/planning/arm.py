"""左右手的名字和参数：关节、夹爪、工具坐标系、腕部相机、MoveIt 组、cuRobo 配置、腕部转动范围、肩部位置。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

from config.planning import ArmConfig

from config.settings import use_utf8_output

# The right arm's URDF names; the left arm's carry the suffix "_2".
BASE_JOINTS = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll")


@dataclass(frozen=True)
class Arm:
    """One of the two SO-101 arms. The left arm is a copy of the right one, not a mirror: same parts, same joint frames,
    and on both grippers the right half (moving jaw) opens and closes while the left half (fixed jaw) never moves. So the
    grasp logic built around the right arm's fixed finger (align its left edge, move right to touch, open while moving
    left) holds for the left arm as it is; only per-arm numbers differ (ArmConfig)."""

    side: str

    @property
    def suffix(self) -> str:
        suffix = "" if self.side == "right" else "_2"
        return suffix

    @property
    def label(self) -> str:
        label = "右手" if self.side == "right" else "左手"
        return label

    @property
    def part(self) -> str:
        """The teleop service's and the joint mapping's name: right_arm / left_arm."""
        part = f"{self.side}_arm"
        return part

    @property
    def joints(self) -> tuple[str, ...]:
        """The five arm joints in URDF names (pan, lift, elbow, wrist pitch, wrist roll)."""
        joints = tuple(joint + self.suffix for joint in BASE_JOINTS)
        return joints

    @property
    def jaw(self) -> str:
        jaw = "Jaw" + self.suffix
        return jaw

    @property
    def tcp(self) -> str:
        tcp = f"{self.side}_grasp_tcp"
        return tcp

    @property
    def wrist_camera(self) -> str:
        camera = f"{self.side}_wrist"
        return camera

    @property
    def moveit_arm(self) -> str:
        group = self.part
        return group

    @property
    def moveit_gripper(self) -> str:
        group = f"{self.side}_gripper"
        return group

    @property
    def moveit_arm_jaw(self) -> str:
        group = f"{self.part}_jaw"
        return group

    def link(self, name: str) -> str:
        """A right-arm link name as this arm's (Fixed_Jaw -> Fixed_Jaw_2; Right_Arm_Camera -> Left_Arm_Camera)."""
        if name in ("Right_Arm_Camera", "Right_Camera_Plate"):
            link = name if self.side == "right" else name.replace("Right", "Left")
            return link
        link = name + self.suffix
        return link

    @property
    def robot_config(self) -> Path:
        path = ArmConfig.ROBOT_CONFIGS[self.side]
        return path

    @property
    def real_config(self) -> Path:
        path = ArmConfig.REAL_CONFIGS[self.side]
        return path

    @property
    def roll_range_deg(self) -> tuple[float, float]:
        limits = ArmConfig.ROLL_RANGE_DEG[self.side]
        return limits

    @property
    def shoulder_xy(self) -> tuple[float, float]:
        shoulder = ArmConfig.SHOULDER_XY[self.side]
        return shoulder

    @property
    def ignore_pairs(self) -> tuple[tuple[str, str], ...]:
        """Self-collision pairs ignored for this arm: the shared ones and its own (ArmConfig), in its link names."""
        from config.planning import RealPlanningConfig

        pairs = tuple((self.link(a), self.link(b)) for a, b in (*RealPlanningConfig.IGNORE_PAIRS, *ArmConfig.EXTRA_IGNORE_PAIRS[self.side]))
        return pairs

    @property
    def reach_extra_m(self) -> float:
        extra = ArmConfig.REACH_EXTRA_M[self.side]
        return extra

    @property
    def head_check(self) -> bool:
        trusted = ArmConfig.HEAD_CHECK[self.side]
        return trusted

    @property
    def desk_margin_m(self) -> float:
        margin = ArmConfig.DESK_MARGIN_M[self.side]
        return margin

    @property
    def table_clearance_m(self) -> float:
        clearance = ArmConfig.TABLE_CLEARANCE_M[self.side]
        return clearance

    @property
    def approach_check_offset_m(self) -> float:
        offset = ArmConfig.APPROACH_CHECK_OFFSET_M[self.side]
        return offset

    @property
    def contact_column_px(self) -> float:
        column = ArmConfig.CONTACT_COLUMN_PX[self.side]
        return column


ARMS = {side: Arm(side) for side in ("right", "left")}
RIGHT, LEFT = ARMS["right"], ARMS["left"]


def demo_arm() -> None:
    for arm in ARMS.values():
        print(arm.side, arm.label, arm.part, arm.joints, arm.jaw, arm.tcp, arm.wrist_camera, arm.moveit_arm_jaw,
              arm.link("Fixed_Jaw"), arm.link("Right_Arm_Camera"), arm.robot_config.name, arm.roll_range_deg, arm.shoulder_xy)


def main() -> None:
    use_utf8_output()
    demo_arm()


if __name__ == "__main__":
    main()
