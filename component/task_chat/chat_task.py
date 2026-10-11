"""一个任务页的对话：提示词和工具；state(turn)：每轮给 GPT 的「当前状态」；
awaiting(turn)：这一轮之后等什么（工具没说时）；options(awaiting, turn)：可点的回答；
before(turn)：不用 GPT 就回的话（停、正在执行）或 None；by_rules(turn)：没有 GPT 时用词表回答（调用同一组工具）；
next(turn)：页面自己选了（点了、点了回答）之后，机器人接着问的话。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from component.task_chat.chat_tool import ChatTool
from component.task_chat.chat_turn import ChatTurn
from config.settings import use_utf8_output


@dataclass
class ChatTask:
    name: str
    prompt: str
    tools: list[ChatTool]
    state: Callable[[ChatTurn], str]
    awaiting: Callable[[ChatTurn], str]
    options: Callable[[str, ChatTurn], list[dict]]
    before: Callable[[ChatTurn], str | None]
    by_rules: Callable[[ChatTurn], str]
    next: Callable[[ChatTurn], str]


def demo_chat_task() -> None:
    task = ChatTask("demo", "演示", [], state=lambda turn: "无", awaiting=lambda turn: "target",
                    options=lambda awaiting, turn: [], before=lambda turn: None, by_rules=lambda turn: "好的",
                    next=lambda turn: "要抓什么？")
    logger.info("{} {}", task.name, task.next(ChatTurn(text="", history=[], awaiting="target")))


def main() -> None:
    use_utf8_output()
    demo_chat_task()


if __name__ == "__main__":
    main()
