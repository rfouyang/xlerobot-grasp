"""头部和底盘的键盘遥操作：头部两个电机按每秒速度走目标位置，底盘按速度档给体速度。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.robot import RobotConfig, TeleopConfig

from config.settings import use_utf8_output


class HeadTeleop:
    """head_motor_1 pans (+ = look right), head_motor_2 tilts (+ = look down), both measured on this robot."""

    def __init__(self, keymap: dict[str, str], limits: dict[str, tuple[float, float]]) -> None:
        self.keymap = keymap
        self.limits = {motor: limits[motor] for motor in RobotConfig.HEAD_MOTORS}
        self.target: dict[str, float] = {}
        # A pose the targets walk to (the rest pose); None while the keys drive.
        self.goal: dict[str, float] | None = None

    def sync(self, present: dict[str, float]) -> None:
        self.target = dict(present)
        self.goal = None

    def step(self, pressed: set[str], dt: float) -> None:
        held = {action: code in pressed for action, code in self.keymap.items()}
        if any(held.values()):
            self.goal = None
        if self.goal is not None:
            reach = TeleopConfig.HOMING_SPEED_DEG_S * dt
            for motor, value in self.target.items():
                self.target[motor] = value + max(-reach, min(reach, self.goal[motor] - value))
            if all(abs(self.goal[motor] - value) < 1e-6 for motor, value in self.target.items()):
                self.goal = None
        else:
            reach = TeleopConfig.HEAD_SPEED_DEG_S * dt
            self.target["head_motor_1"] += reach * (held["look_right"] - held["look_left"])
            self.target["head_motor_2"] += reach * (held["look_down"] - held["look_up"])
        for motor, (low, high) in self.limits.items():
            self.target[motor] = max(low, min(high, self.target[motor]))

    def limit_lead(self, present: dict[str, float]) -> None:
        lead = TeleopConfig.MAX_LEAD_DEG
        for motor, value in present.items():
            self.target[motor] = max(value - lead, min(value + lead, self.target[motor]))

    def go_to(self, pose: dict[str, float]) -> None:
        self.goal = {motor: max(low, min(high, pose[motor])) for motor, (low, high) in self.limits.items()}

    def action(self) -> dict[str, float]:
        action = {f"{motor}.pos": value for motor, value in self.target.items()}
        return action


class BaseTeleop:
    """XLeRobot's base keys: body velocity at the current speed level. The level changes once per key press
    (XLeRobot's own loop changes it on every pass while the key is held)."""

    def __init__(self, keymap: dict[str, str], **kwargs) -> None:
        self.keymap = keymap
        self.level = kwargs.get("level", 0)
        self.previous: set[str] = set()
        self.command = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}

    def step(self, pressed: set[str]) -> None:
        held = {action: code in pressed for action, code in self.keymap.items()}
        new = pressed - self.previous
        self.previous = set(pressed)
        top = len(TeleopConfig.BASE_SPEED_LEVELS) - 1
        if self.keymap["faster"] in new:
            self.level = min(self.level + 1, top)
        if self.keymap["slower"] in new:
            self.level = max(self.level - 1, 0)
        speed = TeleopConfig.BASE_SPEED_LEVELS[self.level]
        self.command = {
            "x.vel": speed["xy"] * (held["forward"] - held["backward"]),
            "y.vel": speed["xy"] * (held["left"] - held["right"]),
            "theta.vel": speed["theta"] * (held["turn_left"] - held["turn_right"]),
        }

    def stop(self) -> None:
        self.command = dict.fromkeys(self.command, 0.0)

    def action(self) -> dict[str, float]:
        action = dict(self.command)
        return action


def demo_head_base_teleop() -> None:
    """No robot: 1 s of 'look right' and 'look up', back to a rest pose, then the base with ArrowUp held and '=' pressed twice."""
    head_keys = TeleopConfig.KEYMAP["head"]
    head = HeadTeleop(head_keys, {"head_motor_1": (-119.6, 119.6), "head_motor_2": (-92.6, 92.6)})
    head.sync({"head_motor_1": -5.1, "head_motor_2": 14.2})
    for _ in range(30):
        head.step({head_keys["look_right"], head_keys["look_up"]}, 1 / 30)
    logger.info("头部按住 向右看+向上看 1 s：{}", {motor: round(value, 1) for motor, value in head.target.items()})
    rest = {"head_motor_1": -3.5, "head_motor_2": 16.5}
    head.go_to(rest)
    for _ in range(90):
        head.step(set(), 1 / 30)
    logger.info("头部回休息位 3 s 后：{}（休息位 {}），还在走 {}", {motor: round(value, 1) for motor, value in head.target.items()}, rest, head.goal is not None)
    base_keys = TeleopConfig.KEYMAP["base"]
    base = BaseTeleop(base_keys)
    for pressed in ({base_keys["forward"]}, {base_keys["forward"], base_keys["faster"]}, {base_keys["forward"], base_keys["faster"]},
                    {base_keys["forward"]}, {base_keys["forward"], base_keys["faster"]}, set()):
        base.step(pressed)
        logger.info("底盘按键 {} → 档位 {} 指令 {}", sorted(pressed), base.level, base.action())


def main() -> None:
    use_utf8_output()
    demo_head_base_teleop()


if __name__ == "__main__":
    main()
