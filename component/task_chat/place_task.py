"""抓取摆放的对话（抓哪个 → 放哪里 → 确认 → 开始，只用右手）：提示词、当前状态、下一步等什么、可点的回答，以及没有 GPT
时的词表规则。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from component.task_chat.chat_task import ChatTask
from component.task_chat.chat_turn import ChatTurn
from component.task_chat.pick_task import common_before, pick_by_rules, target_line
from component.task_chat.place_tools import PlaceTools
from component.task_chat.words import PLACE_CONFIRM_OPTIONS, right_reaches
from config.settings import use_utf8_output

PLACE_PROMPT = """任务：抓取摆放——把用户选的一个物体用右手抓起来，放进盒子，或者放到桌面上用户点的一个点。
要定下两项，先物体后放置：抓哪个物体（choose_object），放到哪里（choose_place：盒子 box；桌面点 spot，
位置只能由用户在画面里点，不能用话说）。两项都定了再问用户对不对；用户说对 / 开始，就调用 start。
还缺物体或放置时用户说开始，也调用 start：它会问缺的那项。用户一句话里两项都说了（「把右边的瓶子放进盒子」），
先 choose_object 再 choose_place；这时「右边」说的是物体，盒子的 n、side 给 null。
这个任务里「盒子」是放的地方，不是要抓的物体（除非用户明说抓盒子）。
用户说换放置位置但没说放哪，choose_place 的 kind 给 null；说不对但没说哪个（对着物体）调用 reject_target。
用户说框少了一部分调用 include_part；问桌上有什么调用 look_desk。"""

ASKED = {"confirm": "机器人上一句在等：用户确认物体和放置对不对", "target": "机器人上一句在等：用户说要抓哪一个",
         "place": "机器人上一句在等：用户说放到哪里（盒子 / 桌面点），或在画面里点位置"}


def place_state(tools: PlaceTools, turn: ChatTurn) -> str:
    chosen, place = tools.chosen(), tools.runner.place
    reach = "右手够得到" if chosen and right_reaches(chosen) else "右手够不着（要用户往右挪）"
    kind_waiting = {"spot": "桌面点，还没点位置", "box": "盒子，还没点盒子里的位置"}.get(place.kind, "还没定")
    lines = ["任务：抓取摆放（只用右手）", target_line(tools, reach), f"放置：{tools.place_words() or kind_waiting}",
             ASKED.get(turn.awaiting)]
    state = "\n".join(line for line in lines if line)
    return state


def place_by_rules(tools: PlaceTools, turn: ChatTurn) -> str:
    """The word table's reading for pick-place: where it goes first (盒子 / 桌面), the rest as in 物体抓取."""
    from component.voice_dialog.service import VoiceDialog

    text = turn.text
    words = VoiceDialog.chat_by_words(turn.history, text)
    kind = "box" if "盒" in text else "spot" if "桌面" in text or "桌上" in text else None
    if kind is None and "换" in text and ("放" in text or "位置" in text):
        return tools.choose_place({"kind": None}, turn)["say"]
    if (kind is None and turn.awaiting == "place" and tools.runner.place.kind != "spot"
            and (words["side"] or words["ordinal"])):
        # 「左边那个」 when the robot asked which box.
        kind = "box"
    if kind is None:
        return pick_by_rules(tools, turn)
    # 「把右边的瓶子放进第二个盒子」: before 放 the object (右边 is the bottle's),
    # from 放 on the place (第二个 is the box's).
    at = text.find("放")
    before, after = (text[:at], text[at:]) if at > 0 else ("", text)
    named = VoiceDialog.by_words(before)["object"] if before else None
    if named and named != "box":
        said = VoiceDialog.chat_by_words([], before)
        tools.choose_object({"name": named, "n": said["ordinal"], "side": said["side"], "nearest": said["nearest"]},
                            turn)
    place = VoiceDialog.chat_by_words([], after)
    reply = tools.choose_place({"kind": kind, "n": place["ordinal"], "side": place["side"]}, turn)["say"]
    return reply


def place_task(tools: PlaceTools) -> ChatTask:
    """抓取摆放: which object, where it goes, both confirmed, started (the right arm only)."""

    def awaiting(turn: ChatTurn) -> str:
        chosen = tools.chosen()
        asked = ("target" if chosen is None or not right_reaches(chosen) else "place" if not tools.runner.place.chosen
                 else "confirm")
        return asked

    def options(asked: str, turn: ChatTurn) -> list[dict]:
        question = tools.place_question()
        answers = PLACE_CONFIRM_OPTIONS if asked == "confirm" else question[1] if asked == "place" and question else []
        return answers

    task = ChatTask(
        name="place", prompt=PLACE_PROMPT, tools=tools.tools(),
        state=lambda turn: place_state(tools, turn), awaiting=awaiting, options=options,
        before=lambda turn: common_before(tools, turn),
        by_rules=lambda turn: place_by_rules(tools, turn), next=tools.ask_next)
    return task


def demo_place_task() -> None:
    """Offline: the state lines on a stand-in runner."""
    class Place:
        kind, chosen = "box", False

    class Runner:
        target = {"label": "bottle", "point": [500, 300], "reach": {"ok": True, "arms": ["right"]}}
        running = auto = False
        place = Place()

    task = place_task(PlaceTools(Runner(), None))
    logger.info("\n{}", task.state(ChatTurn(text="", history=[], awaiting="place")))


def main() -> None:
    use_utf8_output()
    demo_place_task()


if __name__ == "__main__":
    main()
