"""任务的步骤表：物体抓取（抓起来再放回原处）、抓取摆放（放进盒子）、瓶子右移、交互抓取。runner.py 按表一步一步执行。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.planning import GripperConfig
from config.settings import use_utf8_output

# (step, label) in the only order they may run.
STAGES = (
    ("prepare", "准备：上力矩并到抓取位"), ("plan", "检测并规划"), ("approach", "到瓶子后方"),
    ("align", "腕部相机对准（左移）"), ("open", "张开夹爪"),
    ("grasp", "伸到瓶身中段"), ("close", "闭合夹爪（先右移贴近定指）"), ("lift", "抬起"), ("place", "放回原处"), ("release", "松开夹爪（同时左移让开定指）"),
    ("retreat", "退出"), ("return", "回到起始位"),
)
# The "move the bottle to the right" action: the same grasp, and while the bottle is up the shoulder turns it right,
# back towards MoveRightConfig.HOME_Y (repeated grasps leave it a little further left each time).
MOVE_RIGHT_STAGES = tuple(stage for name, label in STAGES
                          for stage in ([(name, label), ("shift", "往右移瓶子")] if name == "lift" else
                                        [("place", "在右边放下")] if name == "place" else [(name, label)]))
# "Pick and place" (the pick-place page): the same grasp, then the bottle carried over the open box on the desk,
# lowered into it and let go; out and home along their own plans (box_placement).
PLACE_STAGES = tuple(stage for name, label in STAGES if name in ("prepare", "plan", "approach", "align", "open", "grasp", "close", "lift")
                     for stage in [(name, "检测瓶子和盒子并规划" if name == "plan" else label)]) + (
    ("carry", "送到盒子上方"), ("lower", "放进盒子"), ("release", "松开夹爪（同时左移让开定指）"), ("back_out", "抬高后退出"), ("home", "回到起始位"))
# "Pick and place at a desk point" (the pick-place page, 桌面点): the same steps, the bottle carried over the clicked
# spot and lowered to the height it was grasped from (as putting it back does: it never presses into the desk).
SPOT_STAGES = tuple((name, {"plan": "检测并规划（放到桌面点）", "carry": "送到放置点上方", "lower": "放到桌面点"}.get(name, label))
                    for name, label in PLACE_STAGES)
# "Interactive grasp" (its own page): the grasp's steps on the object the user picked there, by a click on the head
# image or by name; the other tasks always take the nearest bottle.
ACTIONS = {"grasp": STAGES, "move_right": MOVE_RIGHT_STAGES, "place_in_box": PLACE_STAGES, "place_at_spot": SPOT_STAGES, "pick_target": STAGES}
# Motion steps: which planned segment, run forwards or backwards.
SEGMENTS = {"approach": ("approach", 1), "grasp": ("grasp", 1), "lift": ("lift", 1),
            "place": ("lift", -1), "retreat": ("grasp", -1), "return": ("approach", -1)}
# Gripper steps: the jaw's goal (URDF radians); None = back to where it was at the start.
JAWS = {"open": GripperConfig.JAW_OPEN, "close": GripperConfig.JAW_CLOSE, "release": GripperConfig.JAW_OPEN}


def demo_tasks() -> None:
    for action, stages in ACTIONS.items():
        logger.info("{}：{} 步：{}", action, len(stages), " → ".join(label for _, label in stages))


def main() -> None:
    use_utf8_output()
    demo_tasks()


if __name__ == "__main__":
    main()
