"""任务对话里机器人怎么说：物体和位置的叫法、选好之后问的话、页面上可点的回答。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.settings import use_utf8_output
from config.voice import DialogConfig

CIRCLED = "①②③④⑤⑥"
ASK_TARGET = "要抓哪一个？在左边画面里点它，或者告诉我它的名字。"
ASK_PLACE = "放到哪里？放进盒子，还是放到桌面上的一个点？"
ASK_SPOT = "放在哪里？在左边画面里点一下桌面。"
ASK_BOX = "放在盒子哪里？在左边画面里点盒子里要放的位置。"
# The answers to tap: under the question whether the object is right (物体抓取), where it goes, and whether the object
# and the place are right (抓取摆放). "action" is done by the page itself, "say" is said to the chat.
CONFIRM_OPTIONS = [{"label": "对，开始抓", "action": "go"}, {"label": "不对，换一个", "action": "redo"},
                   {"label": "没框全", "say": "目标没框全，缺的部分也是它的一部分"}]
PLACE_OPTIONS = [{"label": "放进盒子", "say": "放进盒子"}, {"label": "放到桌面点", "say": "放到桌面点"}]
PLACE_CONFIRM_OPTIONS = [{"label": "对，开始", "action": "go"}, {"label": "不对，换一个", "action": "redo"},
                         {"label": "换放置位置", "say": "换个放置位置"}]
# A detection this unsure is said so.
SURE_SCORE = 0.5
# The head image's width (px): where a target's point lies, said as 左边 / 中间 / 右边.
IMAGE_WIDTH = 640


def say_name(label: str | None) -> str:
    name = DialogConfig.SAY_NAMES.get(label, label or "物体")
    return name


def which_words(index: int, count: int) -> str:
    """How the robot names the index-th (from the left) of count objects."""
    words = (("左边的", "右边的")[index] if count == 2 else ("左边的", "中间的", "右边的")[index] if count == 3
             else f"从左数第 {index + 1} 个")
    return words


def where_words(target: dict) -> str:
    """左边的 / 中间的 / 右边的, from where the target's point lies in the head image."""
    point = target.get("point")
    if not point:
        return ""
    share = point[0] / IMAGE_WIDTH
    words = "左边的" if share < 0.42 else "右边的" if share > 0.58 else "中间的"
    return words


def said_index(count: int, n, side) -> int | None:
    """The one of count the user said: the n-th from the left, or the one on a side (middle: of an odd count)."""
    if isinstance(n, int) and 1 <= n <= count:
        return n - 1
    index = {"left": 0, "right": count - 1, "middle": count // 2 if count % 2 == 1 else None}.get(side)
    return index


def reachable(target: dict) -> bool:
    """An arm reaches it (unknown: yes)."""
    reach = target.get("reach") or {}
    ok = not reach or bool(reach.get("ok") and reach.get("arms"))
    return ok


def right_reaches(target: dict) -> bool:
    """Pick-place uses the right arm only: does it reach the target (unknown: yes)."""
    reach = target.get("reach") or {}
    ok = not reach or "right" in (reach.get("arms") or [])
    return ok


def reach_words(target: dict) -> str:
    reach = target.get("reach") or {}
    if not reach:
        return ""
    if not reachable(target):
        return "两只手都够不着，请把它往近处放一点。"
    words = f"{'右手' if reach['arms'][0] == 'right' else '左手'}够得到。"
    return words


def chosen_words(name: str, reach: str) -> str:
    """What the robot says it chose (画面里紫色框) and which arm reaches it."""
    words = f"我选的是{name}（画面里紫色框），{reach}"
    return words


def confirm_question(target: dict, which: str = "") -> str:
    """After a target is chosen (物体抓取): what it is, whether an arm reaches it,
    and the question whether it is right."""
    name = f"{which}{say_name(target.get('label'))}"
    if not reachable(target):
        return f"我选的是{name}，但两只手都够不着它。换一个，还是把它往机器人这边挪近一点再选？"
    unsure = "我不太确定那是" if (target.get("score") or 1.0) < SURE_SCORE else "我选的是"
    question = f"{unsure}{name}（画面里紫色框），{reach_words(target)}框得对吗？对的话我就开始抓。"
    return question


def demo_words() -> None:
    target = {"label": "bottle", "point": [500, 300], "score": 0.8, "reach": {"ok": True, "arms": ["right"]}}
    logger.info("{}", confirm_question(target, where_words(target)))
    logger.info("{}", confirm_question({**target, "reach": {"ok": False, "arms": []}}))
    logger.info("{} {} {}", which_words(1, 3), said_index(3, None, "middle"), said_index(2, 2, None))


def main() -> None:
    use_utf8_output()
    demo_words()


if __name__ == "__main__":
    main()
