"""底盘的键盘遥操作：按键 → 当前速度档的车体速度；按一下换一档。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.robot import TeleopConfig
from config.settings import use_utf8_output


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


def demo_base_teleop() -> None:
    from config.robot import TeleopConfig

    keys = {"forward": "KeyI", "backward": "KeyK", "left": "KeyJ", "right": "KeyL", "turn_left": "KeyU",
            "turn_right": "KeyO", "faster": "Equal", "slower": "Minus"}
    teleop = BaseTeleop(keys)
    teleop.step({"KeyI", "Equal"})
    logger.info("档位 {} / {}，{}", teleop.level, len(TeleopConfig.BASE_SPEED_LEVELS), teleop.action())


def main() -> None:
    use_utf8_output()
    demo_base_teleop()


if __name__ == "__main__":
    main()
