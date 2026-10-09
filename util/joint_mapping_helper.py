"""真机关节（lerobot 度）与 URDF 关节（弧度）之间的换算：方向和零点见 JointMappingConfig。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import math

from loguru import logger

from config.robot import JointMappingConfig

from config.settings import use_utf8_output

ARMS = {"right_arm": "", "left_arm": "_2"}


class JointMappingHelper:
    """real = sign * urdf_deg + offset, per joint; the grippers map 0-100 onto Jaw 0..JAW_OPEN_RAD. corrections: each arm's
    calibrated joint corrections (JointMappingConfig.ARM_CORRECTION_DEG; {} for the bare mapping, as a re-calibration
    from scratch wants)."""

    def __init__(self, **kwargs) -> None:
        self.corrections = kwargs.get("corrections", JointMappingConfig.ARM_CORRECTION_DEG)

    def to_urdf(self, real: dict[str, dict[str, float]]) -> dict[str, float]:
        """real: part -> motor -> value (the teleop service's joints) -> URDF joint -> radians."""
        urdf = {}
        for part, suffix in ARMS.items():
            if part in real:
                correction = self.corrections.get(part, {})
                for joint, (motor, sign, offset) in JointMappingConfig.ARM.items():
                    urdf[joint + suffix] = math.radians((real[part][motor] - offset) / sign + correction.get(motor, 0.0))
                urdf["Jaw" + suffix] = real[part]["gripper"] / 100 * JointMappingConfig.JAW_OPEN_RAD
        if "head" in real:
            for joint, (motor, sign, offset) in JointMappingConfig.HEAD.items():
                urdf[joint] = math.radians((real["head"][motor] - offset) / sign)
        return urdf

    def to_real(self, urdf: dict[str, float]) -> dict[str, dict[str, float]]:
        """URDF joint -> radians -> part -> motor -> value, for the joints given."""
        real: dict[str, dict[str, float]] = {}
        for part, suffix in ARMS.items():
            correction = self.corrections.get(part, {})
            for joint, (motor, sign, offset) in JointMappingConfig.ARM.items():
                if joint + suffix in urdf:
                    real.setdefault(part, {})[motor] = sign * (math.degrees(urdf[joint + suffix]) - correction.get(motor, 0.0)) + offset
            if "Jaw" + suffix in urdf:
                real.setdefault(part, {})["gripper"] = urdf["Jaw" + suffix] / JointMappingConfig.JAW_OPEN_RAD * 100
        for joint, (motor, sign, offset) in JointMappingConfig.HEAD.items():
            if joint in urdf:
                real.setdefault("head", {})[motor] = sign * math.degrees(urdf[joint]) + offset
        return real


def demo_joint_mapping_helper() -> None:
    """The rest pose and the grasp start pose in URDF terms, and the round trip back."""
    import json

    helper = JointMappingHelper()
    teleop_config = BASE_DIR / "config"
    for name in ("rest", "grasp_start"):
        real = json.loads((teleop_config / f"{name}_pose.json").read_text())["pose"]
        urdf = helper.to_urdf(real)
        back = helper.to_real(urdf)
        error = max(abs(back[part][motor] - value) for part, joints in real.items() for motor, value in joints.items())
        logger.info("{}：URDF {}；往返误差 {:.1e}°", name, {joint: round(value, 3) for joint, value in urdf.items()}, error)


def main() -> None:
    use_utf8_output()
    demo_joint_mapping_helper()


if __name__ == "__main__":
    main()
