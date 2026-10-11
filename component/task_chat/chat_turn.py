"""对话的一轮：进来的话，和工具留给页面的东西（可点的回答、画面上标号的候选、选中的目标、要不要开始）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass, field

from loguru import logger

from config.settings import use_utf8_output


@dataclass
class ChatTurn:
    text: str
    history: list[dict]
    # What the robot last asked for (the page keeps it): "target", "place" or "confirm".
    awaiting: str
    # The answers to tap under the reply; None: the task's own for what is asked next.
    options: list[dict] | None = None
    candidates: list[dict] = field(default_factory=list)
    target: dict | None = None
    go: bool = False
    awaiting_next: str | None = None
    # How the robot named the object chosen this turn (左边的...; "" for the only one); None when none was chosen.
    chosen_now: str | None = None
    # Where it goes was chosen or changed in this turn (抓取摆放): the start then waits for the question
    # whether it is right.
    place_now: bool = False
    calls: list[dict] = field(default_factory=list)

    def fresh(self, **kwargs) -> ChatTurn:
        """The same words with nothing done yet (a GPT turn that failed half way is not mixed into the rules' one)."""
        turn = ChatTurn(text=self.text, history=self.history, awaiting=self.awaiting, **kwargs)
        return turn


def demo_chat_turn() -> None:
    turn = ChatTurn(text="抓右边的瓶子", history=[], awaiting="target", go=True)
    logger.info("{} -> {}", turn, turn.fresh(options=[]))


def main() -> None:
    use_utf8_output()
    demo_chat_turn()


if __name__ == "__main__":
    main()
