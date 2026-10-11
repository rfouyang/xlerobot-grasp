"""对话能做的一件事：给 GPT 的名字、说明和 JSON 参数，
以及 run(args, turn) -> 给 GPT 的结果（带 say 时就是这一轮要说的话）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from component.task_chat.chat_turn import ChatTurn
from config.settings import use_utf8_output


@dataclass
class ChatTool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict, ChatTurn], dict]

    def schema(self) -> dict:
        # Strict schemas: every key required (null when not given), so the model fills them all.
        schema = {"type": "function", "function": {
            "name": self.name, "description": self.description, "strict": True,
            "parameters": {"type": "object", "properties": self.parameters, "required": list(self.parameters),
                           "additionalProperties": False}}}
        return schema


def demo_chat_tool() -> None:
    tool = ChatTool("pick", "选物体", {"name": {"type": "string"}}, lambda args, turn: {"say": f"选了 {args['name']}"})
    logger.info("{}", tool.schema())
    logger.info("{}", tool.run({"name": "bottle"}, ChatTurn(text="", history=[], awaiting="target")))


def main() -> None:
    use_utf8_output()
    demo_chat_tool()


if __name__ == "__main__":
    main()
