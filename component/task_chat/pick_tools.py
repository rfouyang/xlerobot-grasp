"""物体抓取的对话工具：看桌面、选物体、补框、换目标、开始。每个工具直接调用真机任务（runner），返回给 GPT 的结果里带上
这一轮要说的话（say）。能不能开始在 start 里由代码把关：物体选了、用户看过确认的问题、有手够得到。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from component.task_chat.chat_tool import ChatTool
from component.task_chat.chat_turn import ChatTurn
from component.task_chat.words import (
    ASK_TARGET,
    CIRCLED,
    confirm_question,
    reach_words,
    reachable,
    said_index,
    say_name,
    where_words,
    which_words,
)
from component.task_execution.busy_error import TaskBusyError
from config.settings import use_utf8_output
from config.voice import DialogConfig

NULLABLE_INT = {"type": ["integer", "null"]}
SIDE = {"type": ["string", "null"], "enum": ["left", "right", "middle", None]}


class PickTools:
    """The tools over the real-robot task (runner) and a VoiceDialog (to look at the desk). A choice the runner
    refuses (ValueError, TaskBusyError) is said back; the head camera without frames (CameraStaleError) goes up to
    the chat, which says so."""

    def __init__(self, runner, dialog) -> None:
        self.runner = runner
        self.dialog = dialog
        # How the chat last named the target it chose ((point, 右边的)): said the same way later, not worked out again
        # from where it lies in the picture (右边的 of two bottles can lie in the picture's middle).
        self.named = (None, "")

    def which(self, target: dict) -> str:
        point, words = self.named
        which = words if point is not None and point == target.get("point") else where_words(target)
        return which

    def chosen(self) -> dict | None:
        """The target the user chose, None while there is none."""
        target = self.runner.target
        chosen = None if target.get("default") else target
        return chosen

    def name_of(self, target: dict, which: str | None = None) -> str:
        name = f"{self.which(target) if which is None else which}{say_name(target.get('label'))}"
        return name

    def starts(self, turn: ChatTurn) -> bool:
        """The user said to start in this very turn (直接 / 开始 / 马上...)."""
        starts = any(word in turn.text for word in DialogConfig.START_WORDS)
        return starts

    # --- the tools ---

    def look_desk(self, args: dict, turn: ChatTurn) -> dict:
        import cv2

        rgb = self.runner.teleop.rgbd()["color"]
        jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        seen = self.dialog.describe(jpeg)
        if not seen:
            return {"ok": False, "say": f"现在看不清（GPT 连不上）。{ASK_TARGET}"}
        chosen = self.chosen()
        follow = f"现在选的是{self.name_of(chosen)}，要换吗？" if chosen else ASK_TARGET
        result = {"ok": True, "seen": seen, "say": f"{seen}{follow}"}
        return result

    def choose_object(self, args: dict, turn: ChatTurn) -> dict:
        """The object of the name the user means: the only one, the n-th from the left, the one on a side, the nearest;
        when it cannot tell, the candidates numbered on the head image and the question which."""
        prompt, objects = self.runner.selector.candidates(args.get("name") or "bottle")
        name = say_name(prompt)
        if not objects:
            return {"ok": False, "found": 0, "say": f"画面里没看到{name}。在左边画面里点它，或者换个说法？"}
        count = len(objects)
        nearest = min(range(count), key=lambda i: objects[i]["distance"] if objects[i]["distance"] is not None else 1e9)
        picked = 0 if count == 1 else said_index(count, args.get("n"), args.get("side"))
        if picked is None and args.get("nearest"):
            picked = nearest
        if picked is None:
            shown = objects[:len(CIRCLED)]
            turn.options = [{"label": f"{CIRCLED[i]} {which_words(i, count)}{'（更近）' if i == nearest else ''}",
                             "prompt": prompt, "point": item["point"]} for i, item in enumerate(shown)]
            turn.candidates = [{"n": i + 1, "point": item["point"], "box": item["box"]} for i, item in enumerate(shown)]
            turn.awaiting_next = "target"
            return {"ok": False, "found": count,
                    "say": f"画面里有 {count} 个{name}（左边画面里的 {CIRCLED[:len(shown)]}），要抓哪一个？"}
        try:
            target = self.runner.set_target(prompt, objects[picked]["point"])["target"]
        except (ValueError, TaskBusyError) as error:
            return {"ok": False, "say": f"没选上：{error}。{ASK_TARGET}"}
        which = which_words(picked, count) if count > 1 else ""
        self.named = (target.get("point"), which)
        turn.target = target
        turn.chosen_now = which
        turn.awaiting_next = "confirm"
        if self.starts(turn):
            # 「抓左边那个，直接抓」: the pick and the go-ahead in one sentence - started here,
            # not left to a second call.
            started = self.start({}, turn)
            return {**started, "chosen": f"{which}{name}", "started": started["ok"]}
        result = {"chosen": f"{which}{name}", **self.next_step(target, which, turn)}
        return result

    def include_part(self, args: dict, turn: ChatTurn) -> dict:
        part = args.get("part") or "缺的部分"
        try:
            target = self.runner.include_part(args.get("part"))["target"]
        except (ValueError, TaskBusyError) as error:
            return {"ok": False, "say": f"没能加上：{error}。"}
        turn.target = target
        turn.awaiting_next = "confirm"
        result = {"ok": True, "say": f"好的，把{part}也算进去了。{self.after_include(target, turn)}"}
        return result

    def reject_target(self, args: dict, turn: ChatTurn) -> dict:
        self.runner.set_target(clear=True)
        turn.awaiting_next = "target"
        turn.options = []
        result = {"ok": True, "say": "好的，那要抓哪一个？在左边画面里点它，或者说「左边那个」。"}
        return result

    def start(self, args: dict, turn: ChatTurn) -> dict:
        """Starts only when the user has seen the target and agreed: after the question whether it is right, or with an
        explicit word (直接 / 开始 / 马上...) in this turn; never without an object chosen or with none reachable."""
        if self.runner.running or self.runner.auto:
            return {"ok": False, "say": "正在执行，等这一轮做完再说。要停下就说「停」。"}
        chosen = self.chosen()
        if chosen is None:
            # The object first (任务步骤 ①, 10-11): not the nearest bottle unasked.
            turn.options = []
            turn.awaiting_next = "target"
            return {"ok": False, "say": f"还没选要抓的物体。{ASK_TARGET}"}
        which = self.which(chosen) if turn.chosen_now is None else turn.chosen_now
        blocked = self.blocked(chosen, which, turn)
        if blocked is not None:
            return {**blocked, "ok": False}
        # Seen: the robot asked whether it is all right and nothing was changed in this turn.
        seen = turn.awaiting == "confirm" and turn.chosen_now is None and not turn.place_now
        if not (seen or self.starts(turn)):
            return {**self.next_step(chosen, which, turn), "ok": False}
        turn.go = True
        turn.awaiting_next = "confirm"
        result = {"ok": True, "say": self.started_words(chosen, which)}
        return result

    # --- what the task asks for (PlaceTools has its own) ---

    def next_step(self, target: dict, which: str, turn: ChatTurn) -> dict:
        """After the object is chosen (or when asked what is next): whether it is right."""
        turn.awaiting_next = "confirm"
        result = {"ok": True, "reachable": reachable(target), "say": confirm_question(target, which)}
        return result

    def after_include(self, target: dict, turn: ChatTurn) -> str:
        """What is asked once the missed part is in."""
        question = "现在框得对吗？对的话我就开始抓。"
        return question

    def blocked(self, chosen: dict, which: str, turn: ChatTurn) -> dict | None:
        """Why the chosen target cannot be started (no arm reaches it), else None."""
        if reachable(chosen):
            return None
        turn.awaiting_next = "target"
        blocked = {"ok": False,
                   "say": f"{self.name_of(chosen, which)}两只手都够不着，请把它往机器人这边挪近一点，或者换一个。"}
        return blocked

    def started_words(self, chosen: dict, which: str) -> str:
        words = f"好的，开始抓{self.name_of(chosen, which)}。{reach_words(chosen)}要停就说「停」。"
        return words

    def ask_next(self, turn: ChatTurn) -> str:
        """The question the robot leads on with, with no words from the user (after a click or a tap on the page)."""
        chosen = self.chosen()
        if chosen is None:
            turn.awaiting_next = "target"
            return ASK_TARGET
        question = self.next_step(chosen, self.which(chosen), turn)["say"]
        return question

    def tools(self) -> list[ChatTool]:
        tools = [
            ChatTool("look_desk", "看头部相机的画面，说桌上有什么、大概在哪"
                     "（用户问「你能看见什么」「桌上有什么」时）。", {}, self.look_desk),
            ChatTool("choose_object", "选定要抓的物体（也用于改选：「不是这个，是左边那个」）。"
                     "画面里有多个而用户没说哪个时，工具会在画面上标出 ①② 并问用户。", {
                         "name": {"type": "string",
                                  "description": "物体的英文通用名词，如 bottle；"
                                                 "用户只说「右边那个」时取之前说过的物体"},
                         "n": {**NULLABLE_INT,
                               "description": "从左数第几个（用户说「第二个」或点了选项 ②），否则 null"},
                         "side": {**SIDE, "description": "用户说的左边 / 右边 / 中间，否则 null"},
                         "nearest": {"type": "boolean",
                                     "description": "只有用户明确说要最近 / 离机器人近的那个时为 true，否则 false"}},
                     self.choose_object),
            ChatTool("include_part", "用户说选的目标没框全（瓶盖、盖子等也是它的一部分）：把那部分加进目标框。", {
                         "part": {"type": ["string", "null"], "description": "缺的部分，中文，如 瓶盖"}},
                     self.include_part),
            ChatTool("reject_target", "用户说选错了但没说要哪个（不对 / 不是这个）：清掉当前目标，问要哪个。", {},
                     self.reject_target),
            ChatTool("start", "开始执行任务。只在用户明确同意时调用（对 / 可以 / 开始吧 / 直接抓）；"
                     "代码还会检查目标是否选定、确认过、够得着，不行时结果里会说为什么。", {}, self.start),
        ]
        return tools


def demo_pick_tools() -> None:
    """Offline: the tools' names and what a confirm question says."""
    tools = PickTools(None, None)
    logger.info("{}", [tool.name for tool in tools.tools()])
    target = {"label": "bottle", "point": [500, 300], "score": 0.8, "reach": {"ok": True, "arms": ["right"]}}
    logger.info("{}", tools.next_step(target, "右边的", ChatTurn(text="", history=[], awaiting="target"))["say"])


def main() -> None:
    use_utf8_output()
    demo_pick_tools()


if __name__ == "__main__":
    main()
