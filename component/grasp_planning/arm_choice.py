"""用哪只手：离哪边肩膀近用哪只手（用户的规则），两只手的可达图，够不着时建议往哪挪，以及头部画面里的分手线。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
from loguru import logger

from component.grasp_planning.arm import ARMS
from component.grasp_planning.reach_map import ReachMap
from config.planning import ArmConfig, ReachMapConfig
from config.settings import use_utf8_output


class ArmChoice:
    """The reach maps of both arms are solved (or read from disk) in the background when this is made."""

    def __init__(self) -> None:
        self.reaches = {side: ReachMap(arm) for side, arm in ARMS.items()}
        for reach in self.reaches.values():
            reach.start(ReachMapConfig.TABLE_Z_M)

    @staticmethod
    def sides(action: str) -> list[str]:
        """The arms a task may use, preferred first: the interactive grasp per ArmConfig.CHOICE, the rest the right."""
        if action != "pick_target" or ArmConfig.CHOICE == "right":
            return ["right"]
        sides = ["left"] if ArmConfig.CHOICE == "left" else ["right", "left"]
        return sides

    def advice(self, x: float, y: float, height: float, table_z: float, sides: list[str]) -> dict:
        """Which of sides reaches an object standing at (x, y): "arms" lists those that do (preferred first), "ok" if
        any, "text" for the page; if none, the shorter of their suggested moves."""
        answers = {side: self.reaches[side].advice(x, y, height, table_z) for side in sides}
        arms = [side for side in sides if answers[side]["ok"]]
        if len(sides) == 2:
            # The nearer shoulder's arm first (the user's rule), whether or not both reach.
            near = {side: float(np.hypot(x - ARMS[side].shoulder_xy[0], y - ARMS[side].shoulder_xy[1]))
                    for side in sides}
            arms = sorted(arms, key=lambda side: near[side])
            nearer = min(sides, key=lambda side: near[side])
            if arms:
                note = "更近" if arms[0] == nearer else f"抓（{ARMS[nearer].label}更近但够不着）"
                result = {**answers[arms[0]], "arms": arms,
                          "text": f"{ARMS[arms[0]].label}{note}（离左肩 {near['left'] * 100:.0f} cm、"
                                  f"右肩 {near['right'] * 100:.0f} cm）"}
                return result
        if arms:
            other = "左手" if arms[0] == "right" else "右手"
            note = f"（{other}够不着）" if len(sides) == 2 else ""
            result = {**answers[arms[0]], "arms": arms, "text": f"{ARMS[arms[0]].label}够得着{note}"}
            return result
        moves = [answer for answer in answers.values() if answer["move"]]
        best = min(moves, key=lambda answer: float(np.hypot(*answer["move"]))) if moves else answers[sides[0]]
        text = best["text"]
        if len(sides) > 1:
            text = text.replace(ARMS["right"].label, "两只手都").replace(ARMS["left"].label, "两只手都")
        result = {**best, "arms": [], "text": text}
        return result

    @staticmethod
    def midline_y(x: float) -> float:
        """The line halfway between the two shoulders (base y at depth x, m): left of it the left shoulder is nearer."""
        a, b = np.array(ARMS["right"].shoulder_xy), np.array(ARMS["left"].shoulder_xy)
        middle = float((b @ b - a @ a - 2 * x * (b[0] - a[0])) / (2 * (b[1] - a[1])))
        return middle

    def midline_pixels(self, frame: dict) -> np.ndarray | None:
        """The halfway line on the desk as head-image pixels, over the rows where either arm reaches; None before the
        grids."""
        right, left = self.reaches["right"], self.reaches["left"]
        if right.reachable is None or left.reachable is None:
            return None
        rows = [i for i in range(len(right.xs)) if (right.reachable[i] | left.reachable[i]).any()]
        if len(rows) < 2:
            return None
        points = np.array([[right.xs[i], self.midline_y(right.xs[i]), ReachMapConfig.TABLE_Z_M] for i in rows])
        pixels = right.project(frame, points)
        return pixels


def demo_arm_choice() -> None:
    """Offline: the reach maps from disk (built the first time), then which arm takes a few desk spots."""
    import time

    choice = ArmChoice()
    while any(reach.reachable is None for reach in choice.reaches.values()):
        time.sleep(1.0)
    for x, y in ((0.20, -0.004), (0.184, 0.035), (0.21, 0.117), (0.30, -0.10)):
        logger.info("({:.3f}, {:+.3f})：{}，分手线 y {:+.3f}", x, y,
                    choice.advice(x, y, 0.15, ReachMapConfig.TABLE_Z_M, ["right", "left"])["text"], choice.midline_y(x))


def main() -> None:
    use_utf8_output()
    demo_arm_choice()


if __name__ == "__main__":
    main()
