"""抓取摆放的对话工具：物体和物体抓取一样，再加上放到哪里（盒子 / 桌面点），选好之后问两项都对不对。只用右手，物体和放置
都定了才开始（用户 10-10：先确定要抓的物体和要放的地方，再开始）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from component.task_chat.chat_tool import ChatTool
from component.task_chat.chat_turn import ChatTurn
from component.task_chat.pick_tools import NULLABLE_INT, SIDE, PickTools
from component.task_chat.words import (
    ASK_BOX,
    ASK_PLACE,
    ASK_SPOT,
    ASK_TARGET,
    CIRCLED,
    PLACE_CONFIRM_OPTIONS,
    PLACE_OPTIONS,
    chosen_words,
    right_reaches,
    said_index,
    which_words,
)
from component.task_execution.busy_error import TaskBusyError
from config.settings import use_utf8_output

RIGHT_REACHES = "右手够得到。"


class PlaceTools(PickTools):
    def place_words(self) -> str | None:
        """Where it goes, as said in the question whether both are right (Penpot: 右边的瓶子 → 盒子（橙框）); None while
        that is not chosen yet."""
        place = self.runner.place
        words = None if not place.chosen else "盒子（橙框）" if place.kind == "box" else "这里（橙色叉）"
        return words

    def place_question(self) -> tuple[str, list[dict]] | None:
        """(question, answers to tap) about where it goes while that is still open; None once it is chosen."""
        place = self.runner.place
        if place.chosen:
            return None
        question = {"spot": (ASK_SPOT, []), "box": (ASK_BOX, [])}.get(place.kind, (ASK_PLACE, PLACE_OPTIONS))
        return question

    def next_step(self, target: dict, which: str, turn: ChatTurn) -> dict:
        name = self.name_of(target, which)
        if not right_reaches(target):
            turn.awaiting_next = "target"
            turn.options = []
            return {"ok": False, "right_reaches": False,
                    "say": f"{name}右手够不着。抓取摆放只用右手：请把它往右边挪一点，再告诉我，或者换一个。"}
        asked = self.place_question()
        if asked is not None:
            question, turn.options = asked
            turn.awaiting_next = "place"
            # The same sentence may say where it goes (把右边的瓶子放进盒子): GPT then calls choose_place too.
            more = turn.options is PLACE_OPTIONS and not turn.place_now and any(word in turn.text for word in "放盒桌")
            return {"ok": True, "place": None, "say": f"{chosen_words(name, RIGHT_REACHES)}{question}",
                    **({"more": True, "note": "用户这句话里说了放到哪里：接着调用 choose_place"} if more else {})}
        turn.awaiting_next = "confirm"
        turn.options = PLACE_CONFIRM_OPTIONS
        result = {"ok": True, "place": self.place_words(),
                  "say": f"好的：{name} → {self.place_words()}，用右手。对吗？对的话我就开始。"}
        return result

    def after_include(self, target: dict, turn: ChatTurn) -> str:
        """On to what is still open (where it goes), or the question whether it is all right."""
        if not right_reaches(target):
            return self.next_step(target, self.which(target), turn)["say"]
        asked = self.place_question()
        if asked is None:
            turn.options = PLACE_CONFIRM_OPTIONS
            return "现在框得对吗？对的话我就开始。"
        question, turn.options = asked
        turn.awaiting_next = "place"
        return question

    def blocked(self, chosen: dict, which: str, turn: ChatTurn) -> dict | None:
        blocked = (self.next_step(chosen, which, turn) if not right_reaches(chosen) or not self.runner.place.chosen
                   else None)
        return blocked

    def started_words(self, chosen: dict, which: str) -> str:
        where = "放进盒子" if self.runner.place.kind == "box" else "放到你点的位置"
        words = f"好的，开始把{self.name_of(chosen, which)}{where}，用右手。要停就说「停」。"
        return words

    def ask_next(self, turn: ChatTurn) -> str:
        chosen = self.chosen()
        if chosen is not None:
            return self.next_step(chosen, self.which(chosen), turn)["say"]
        turn.awaiting_next = "target"
        turn.options = []
        where = "放进盒子" if self.runner.place.kind == "box" else "放在这里"
        question = f"好的，{where}。{ASK_TARGET}" if self.runner.place.chosen else ASK_TARGET
        return question

    def choose_place(self, args: dict, turn: ChatTurn) -> dict:
        """Where the object goes: kind "box" (which box when the user said; where in it clicked next), "spot" (a desk
        point clicked next), or None (asked)."""
        kind = args.get("kind")
        if kind not in ("box", "spot"):
            # The user's own words over GPT's null (「放到桌面点」 came as null once, 10-10).
            text = turn.text
            kind = "spot" if "桌面" in text or "桌上" in text else "box" if "盒" in text else None
        turn.place_now = True
        try:
            if kind == "box":
                return self.choose_box(args, turn)
            self.runner.set_place(kind)
        except (ValueError, TaskBusyError) as error:
            return {"ok": False, "say": f"没设上：{error}。"}
        turn.awaiting_next = "place"
        if kind is None:
            turn.options = PLACE_OPTIONS
            return {"ok": True, "say": ASK_PLACE}
        chosen = self.chosen()
        if chosen is not None and turn.chosen_now is not None:
            # 「把右边的瓶子放到桌面上」: what was chosen, then where to click (Penpot 「放到桌面点（对话）」).
            return self.next_step(chosen, turn.chosen_now, turn)
        turn.options = []
        result = {"ok": True, "say": ASK_SPOT}
        return result

    def choose_box(self, args: dict, turn: ChatTurn) -> dict:
        """Into a box: the one the user said (第二个, 右边的; the only one with any side said) goes in the middle
        of what the right arm reaches; else where in a box is clicked on the picture, the reachable boxes framed
        in orange (Penpot 「抓取摆放 / 电脑 · 选盒子（对话）」). Not picked for the user (10-11)."""
        seen = self.runner.selector.boxes()
        # Only the boxes the right arm reaches (a tissue box at the back left is no place to put it).
        boxes = [box for box in seen if box["right_reach"] is not False]
        if not boxes:
            turn.awaiting_next = "place"
            turn.options = PLACE_OPTIONS
            far = ("画面里的盒子右手都够不着，把盒子往机器人右前方挪近一点" if seen
                   else "画面里没看到盒子，把盒子放到机器人前面")
            return {"ok": False, "found": 0, "say": f"{far}再说一次，或者放到桌面上的一个点？"}
        count = len(boxes)
        picked = said_index(count, args.get("n"), args.get("side"))
        if picked is not None:
            self.runner.set_place("box", box=boxes[picked])
            if self.chosen() is not None and self.starts(turn):
                return self.start({}, turn)
            return {"ok": True, "say": self.ask_next(turn)}
        self.runner.set_place("box")
        shown = boxes[:len(CIRCLED)]
        turn.options = ([{"label": "放在盒子中间", "place_box": shown[0]["point"]}] if count == 1 else
                        [{"label": f"{CIRCLED[i]} {which_words(i, count)}盒子（放中间）", "place_box": box["point"]}
                         for i, box in enumerate(shown)])
        turn.candidates = [{"n": i + 1 if count > 1 else "", "point": box["point"], "box": box["box"],
                            "kind": "place"}
                           for i, box in enumerate(shown)]
        turn.awaiting_next = "place"
        chosen = self.chosen()
        head = (chosen_words(self.name_of(chosen), RIGHT_REACHES)
                if chosen is not None and turn.chosen_now is not None else "")
        result = {"ok": True, "found": count,
                  "say": f"{head}在左边画面里点盒子里要放的位置（橙框是够得到的盒子）。"
                         "也可以说放哪个盒子，我就放在它中间。"}
        return result

    def tools(self) -> list[ChatTool]:
        tools = super().tools()
        tools.insert(2, ChatTool("choose_place", "定下放到哪里：放进盒子（box），或放到桌面上的一个点"
                                 "（spot，位置只能由用户在画面里点）；用户说换放置位置但没说放哪时 kind 给 null。"
                                 "用户没说哪个盒子时，工具会请用户在画面里点盒子里的位置。", {
                                     "kind": {"type": ["string", "null"], "enum": ["box", "spot", None],
                                              "description": "放进盒子 = box；放到桌面（桌面点、桌上某处）= spot；"
                                                             "只说换个地方、没说放哪时 null"},
                                     "n": {**NULLABLE_INT,
                                           "description": "第几个盒子（从左数，用户说「第二个」或点了 ②），否则 null"},
                                     "side": {**SIDE, "description": "用户说的左边 / 右边 / 中间的盒子，否则 null"}},
                                 self.choose_place))
        return tools


def demo_place_tools() -> None:
    """Offline: the tools' names."""
    logger.info("{}", [tool.name for tool in PlaceTools(None, None).tools()])


def main() -> None:
    use_utf8_output()
    demo_place_tools()


if __name__ == "__main__":
    main()
