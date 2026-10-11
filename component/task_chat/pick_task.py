"""物体抓取的对话（抓哪个 → 确认 → 开始）：提示词、当前状态、下一步等什么、可点的回答，以及没有 GPT 时的词表规则。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from component.task_chat.chat_task import ChatTask
from component.task_chat.chat_turn import ChatTurn
from component.task_chat.pick_tools import PickTools
from component.task_chat.words import CONFIRM_OPTIONS, reach_words
from config.settings import use_utf8_output
from config.voice import DialogConfig

PICK_PROMPT = """任务：物体抓取——把用户选的一个物体抓起来，再放回原处。
要定下的只有一项：抓哪个物体。选定后问用户框得对不对；用户说对 / 开始，就调用 start。
用户说框少了一部分（盖子也是它的一部分）调用 include_part；说不对但没说要哪个调用 reject_target；
说了要哪个（包括改选）调用 choose_object；问桌上有什么调用 look_desk。
用户在一句话里既选又说开始（「抓左边那个，直接抓」）时，先 choose_object 再 start。"""


def common_before(tools: PickTools, turn: ChatTurn) -> str | None:
    """Without GPT, at once: a stop word stops the robot; while a run goes nothing else is taken."""
    runner = tools.runner
    if any(word in turn.text.lower() for word in DialogConfig.STOP_WORDS):
        runner.teleop.stop()
        turn.awaiting_next = "confirm" if tools.chosen() else "target"
        turn.options = []
        return "好的，停下了。要继续就说「开始」。"
    if runner.running or runner.auto:
        turn.awaiting_next = "confirm"
        turn.options = []
        return "正在执行，等这一轮做完再说。要停下就说「停」。"
    return None


def target_line(tools: PickTools, reach: str) -> str:
    """The 当前状态 line about the object."""
    chosen = tools.chosen()
    line = (f"目标：{tools.name_of(chosen)}（{chosen.get('label')}，画面里紫色框），{reach}" if chosen
            else "目标：还没选")
    return line


def pick_state(tools: PickTools, turn: ChatTurn) -> str:
    chosen = tools.chosen()
    asked = {"confirm": "机器人上一句在等：用户确认目标对不对",
             "target": "机器人上一句在等：用户说要抓哪一个"}.get(turn.awaiting)
    lines = ["任务：物体抓取", target_line(tools, (reach_words(chosen) if chosen else "") or "还不知道哪只手够得到"),
             asked]
    state = "\n".join(line for line in lines if line)
    return state


def pick_by_rules(tools: PickTools, turn: ChatTurn) -> str:
    """The word table's reading of the turn, done with the same tools."""
    from component.voice_dialog.service import VoiceDialog

    words = VoiceDialog.chat_by_words(turn.history, turn.text)
    intent = words["intent"]
    if intent == "describe":
        return tools.look_desk({}, turn)["say"]
    if intent == "include":
        return tools.include_part({"part": words["part"]}, turn)["say"]
    which = words["side"] or words["ordinal"] or words["nearest"]
    named = VoiceDialog.by_words(turn.text)["object"]
    # 「抓右边的瓶子，直接开始」: a start that also says which - chosen first (choose_object starts it then).
    picks = intent == "start" and (which or (named and not tools.chosen()))
    if intent in ("confirm", "start") and not picks:
        return tools.start({}, turn)["say"]
    if intent == "deny" and not words["side"] and not words["ordinal"]:
        return tools.reject_target({}, turn)["say"]
    if not named and not which:
        # Nothing to grasp in this turn (chit-chat): back to what is asked.
        return f"没太听明白。{tools.ask_next(turn)}"
    # 「左边那个」 with no name said before: the task's object, a bottle.
    said = {"name": words["object"] or "bottle", "n": words["ordinal"], "side": words["side"],
            "nearest": words["nearest"]}
    reply = tools.choose_object(said, turn)["say"]
    return reply


def pick_task(tools: PickTools) -> ChatTask:
    """物体抓取: which object, confirmed, started."""
    task = ChatTask(
        name="pick", prompt=PICK_PROMPT, tools=tools.tools(),
        state=lambda turn: pick_state(tools, turn),
        awaiting=lambda turn: "confirm" if tools.chosen() else "target",
        options=lambda awaiting, turn: CONFIRM_OPTIONS if awaiting == "confirm" else [],
        before=lambda turn: common_before(tools, turn),
        by_rules=lambda turn: pick_by_rules(tools, turn), next=tools.ask_next)
    return task


def demo_pick_task() -> None:
    """Offline: the state lines on a stand-in runner."""
    class Runner:
        target = {"label": "bottle", "point": [120, 300], "reach": {"ok": True, "arms": ["left"]}}
        running = auto = False

    task = pick_task(PickTools(Runner(), None))
    logger.info("\n{}", task.state(ChatTurn(text="", history=[], awaiting="confirm")))


def main() -> None:
    use_utf8_output()
    demo_pick_task()


if __name__ == "__main__":
    main()
