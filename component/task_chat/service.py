"""任务页的对话：物体抓取（pick）和抓取摆放（place）各一个任务（提示词 + 工具 + 当前状态 + 下一步等什么）。
一轮：GPT 看对话、当前状态和用户的新话，调用工具（选目标、补框、放哪里、开始……），代码执行，结果给回；
工具都给出了要说的话（say）就结束，否则 GPT 再看一次结果来回答。能不能开始由 start 工具在代码里把关，不靠提示词。
没有 GPT（没网、没 key、出错）时用任务自己的词表规则，调用的是同一组工具。头部相机没有画面时，直接说相机的问题。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import time

from loguru import logger

from component.task_chat.chat_task import ChatTask
from component.task_chat.chat_turn import ChatTurn
from component.task_chat.pick_task import pick_task
from component.task_chat.pick_tools import PickTools
from component.task_chat.place_task import place_task
from component.task_chat.place_tools import PlaceTools
from config.settings import use_utf8_output
from config.voice import DialogConfig
from util.openai_chat_helper import OpenAIChatHelper
from util.teleop_helper import CameraStaleError

# What every task's chat is for, before the task's own prompt.
COMMON_SYSTEM = """你是桌面双臂机器人 XLeRobot 的对话助手，在网页的对话框里和用户说话，你的回复会被读出来。
用户的话常来自语音识别，会有同音字（「水平」多半是「水瓶」）。
你的目的只有一个：和用户一起把任务要的几项定下来，用户确认无误后开始执行。每一轮都朝这个目的推进；不确定就问，不要猜。
- 用工具做事，不要假装做了：选目标、补框、开始都必须调用工具。工具结果里有 say 时，那句就是这一轮要说的话。
- 只有用户明确同意（对 / 可以 / 开始吧 / 直接抓）时才调用 start；用户只是说出要抓什么时，先选定，再问对不对。
- 物体名给工具时用开放词汇检测器认识的最通用的英文名词：bottle（任何水瓶、饮料瓶、喷雾瓶）、cup、can、box、bowl。
- 左右按用户说的（机器人的视角）：左边 = left，右边 = right，中间 = middle；「第二个」是从左数的第 2 个。
- 用户没说物体名（「左边那个」「直接抓」）时，用之前对话里说过的物体；都没有时就是 bottle。
- 用户没说是哪一个时，n、side 都给 null、nearest 给 false，工具会在画面上标号并问用户；不要替用户挑。
- 回复里不说英文名词（说「瓶子」，不说 bottle）。
- 不调用工具时，用一两句简短的中文口语回答（不用 Markdown、不列清单），并把话题带回到下一步要定的那一项。"""
MISHEARD = "没听明白，再说一次？"


class TaskChat:
    """runner: the real-robot task (TaskRunner); dialog: a VoiceDialog (its GPT, and its look at the desk)."""

    def __init__(self, runner, dialog, chat: OpenAIChatHelper | None = None) -> None:
        self.chat = chat or dialog.chat
        self.tasks: dict[str, ChatTask] = {"pick": pick_task(PickTools(runner, dialog)),
                                           "place": place_task(PlaceTools(runner, dialog))}

    def turn(self, name: str, text: str, history: list[dict], awaiting: str = "target") -> dict:
        """One turn of the task's chat -> {"reply", "awaiting", "options", "candidates", "go", "target", "calls",
        "by"}."""
        started = time.time()
        task = self.task(name)
        turn = ChatTurn(text=text.strip(), history=history[-DialogConfig.CHAT_TURNS:], awaiting=awaiting)
        try:
            turn, reply, by = self.reply(task, turn)
        except CameraStaleError as error:
            # The head camera gave no new frame (the USB link to WSL dropped, 2026-10-10): said so, nothing chosen.
            turn, reply, by = turn.fresh(options=[]), str(error), "camera"
        result = self.answer(task, turn, reply, by)
        logger.info("对话（{}，{}，{:.1f} s）：{} → {} {}", name, by, time.time() - started, turn.text, result["reply"],
                    [call["name"] for call in turn.calls])
        return result

    def next(self, name: str) -> dict:
        """What the robot says after the page itself chose (a click on an object, a box or a desk spot, a tapped
        answer), with its answers to tap and what it waits for: the same as after a turn, without one."""
        task = self.task(name)
        turn = ChatTurn(text="", history=[], awaiting="target")
        try:
            reply = task.next(turn)
        except CameraStaleError as error:
            turn, reply = turn.fresh(options=[]), str(error)
        result = self.answer(task, turn, reply, "rule")
        return result

    def task(self, name: str) -> ChatTask:
        if name not in self.tasks:
            raise ValueError(f"没有这个任务的对话：{name}")
        task = self.tasks[name]
        return task

    def reply(self, task: ChatTask, turn: ChatTurn) -> tuple[ChatTurn, str, str]:
        """(the turn as the tools left it, the reply, by whom): a rule first (stop, busy), else GPT, else the words."""
        reply = task.before(turn)
        if reply is not None:
            return turn, reply, "rule"
        if self.chat.configured:
            gpt_turn = turn.fresh()
            try:
                reply = self.by_gpt(task, gpt_turn)
                return gpt_turn, reply, "gpt"
            except CameraStaleError:
                raise
            except Exception as error:
                logger.warning("对话 GPT 没成（{}），用词表", error)
        reply = task.by_rules(turn)
        return turn, reply, "words"

    def by_gpt(self, task: ChatTask, turn: ChatTurn) -> str:
        tools = {tool.name: tool for tool in task.tools}
        lines = [f"{'用户' if item.get('role') == 'user' else '机器人'}：{item.get('text', '')}"
                 for item in turn.history]
        context = "\n".join(["对话记录：", *(lines or ["（刚开始）"]), "", "当前状态：", task.state(turn), "",
                             f"用户（最新）：{turn.text}"])
        messages = [{"role": "system", "content": f"{COMMON_SYSTEM}\n\n{task.prompt}"},
                    {"role": "user", "content": context}]
        said, more = None, False
        for step in range(DialogConfig.CHAT_STEPS):
            last = step == DialogConfig.CHAT_STEPS - 1
            message = self.chat.tool_turn(messages, [tool.schema() for tool in task.tools], "none" if last else "auto")
            calls = message.get("tool_calls") or []
            if not calls:
                # After a tool that asked for one more look (more), its own words stay the answer.
                reply = str((said if more else message.get("content")) or said or "").strip() or MISHEARD
                return reply
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
            results = [self.call(tools, call, turn) for call in calls]
            for call, result in zip(calls, results, strict=True):
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            # The last tool's words are the answer (a pick then a start: the start's) - no second GPT call.
            said = results[-1].get("say") or said
            # more: the tool did its part but the sentence may say more for another tool (抓取摆放: the object chosen,
            # and the same sentence says where it goes) - GPT looks once more.
            more = any(result.get("more") for result in results)
            if all(result.get("say") for result in results) and not more:
                return said
        reply = said or MISHEARD
        return reply

    @staticmethod
    def call(tools: dict, call: dict, turn: ChatTurn) -> dict:
        """One tool call GPT made; a tool that breaks is told back to GPT, the head camera's trouble goes up."""
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
            result = tools[name].run(args, turn) if name in tools else {"ok": False, "error": f"没有工具 {name}"}
        except CameraStaleError:
            raise
        except Exception as error:
            logger.exception("对话工具 {} 出错", name)
            result = {"ok": False, "error": str(error)}
        turn.calls.append({"name": name, "args": call["function"].get("arguments"), "ok": result.get("ok")})
        return result

    @staticmethod
    def answer(task: ChatTask, turn: ChatTurn, reply: str, by: str) -> dict:
        awaiting = turn.awaiting_next or task.awaiting(turn)
        options = turn.options if turn.options is not None else ([] if turn.go else task.options(awaiting, turn))
        result = {"reply": reply, "awaiting": awaiting, "options": options, "candidates": turn.candidates,
                  "go": turn.go, "calls": turn.calls, "by": by, **({"target": turn.target} if turn.target else {})}
        return result


def demo_task_chat() -> None:
    """Offline, by the words (no key), on a stand-in runner with nothing on the desk."""
    class Place:
        kind, box, spot, chosen = None, None, None, False

    class Selector:
        def candidates(self, prompt):
            return prompt, []

    class Runner:
        target = {"label": "bottle", "point": None, "default": True}
        running = auto = False
        place = Place()
        selector = Selector()

    chat = TaskChat(Runner(), None, OpenAIChatHelper(api_key=""))
    logger.info("{}", chat.turn("pick", "抓右边的瓶子", [])["reply"])
    logger.info("{}", chat.next("place")["reply"])


def main() -> None:
    use_utf8_output()
    demo_task_chat()


if __name__ == "__main__":
    main()
