"""任务页的语音 / 文字指令：一句话（「把右边的水瓶抓起来」）→ 抓什么（给 YOLOE 的英文名词）、在桌子哪一边、
一句要说出来的回答。GPT 理解；没有网络或没有 key 时，用词表（DialogConfig.OBJECT_WORDS / SIDE_WORDS）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.settings import use_utf8_output
from config.voice import DialogConfig
from util.openai_chat_helper import OpenAIChatHelper

SYSTEM = """You turn one instruction for a tabletop robot (two arms, a camera looking at a desk) into JSON.
The instruction is usually Chinese and comes from speech recognition, so homophones happen (水平 often means 水瓶).
Answer with exactly these keys:
- "object": the thing to grasp as the most generic common English noun an open-vocabulary detector knows: one word
  where possible ("bottle" for any water / drink / spray bottle, "cup" for a mug or glass, "can", "box", "bowl"), \
or null
  when the instruction names nothing to grasp;
- "side": "left" or "right" when the instruction says which one on the desk (from the robot's view, as the user
  says it), otherwise null;
- "place": "box" when it says to put it into a box, "back" when it says to put it back or says nothing, "spot" when it
  names another place on the desk;
- "reply": one short spoken Chinese sentence confirming what the robot will grasp (no Markdown), or asking what to
  grasp when "object" is null."""


LOOK_SYSTEM = """You are the eyes of a tabletop robot with two arms; the picture is from its head camera looking down at
a desk in front of it. Answer the user in Chinese, in one or two short spoken sentences (no lists, no Markdown): which
objects that could be grasped are on the desk and roughly where (左边 / 中间 / 右边, as seen in the picture), at most
five. Ignore the robot's own parts, the desk, the mat, and things far behind the desk."""


class VoiceDialog:
    def __init__(self, chat: OpenAIChatHelper | None = None) -> None:
        self.chat = chat or OpenAIChatHelper()

    def understand(self, text: str) -> dict:
        """{"object", "side", "place", "reply", "by"}; by: "gpt" or "words" (the table, when GPT is unavailable)."""
        text = text.strip()
        if self.chat.configured:
            try:
                answer = self.chat.json_reply(SYSTEM, text)
                understood = {"object": answer.get("object") or None,
                              "side": answer.get("side") if answer.get("side") in ("left", "right") else None,
                              "place": answer.get("place") if answer.get("place") in ("box", "back", "spot")
                              else "back",
                              "reply": str(answer.get("reply") or "").strip(), "by": "gpt"}
                logger.info("指令理解（GPT）：{} → {}", text, understood)
                return understood
            except Exception as error:
                logger.warning("GPT 没理解成（{}），用词表", error)
        understood = self.by_words(text)
        logger.info("指令理解（词表）：{} → {}", text, understood)
        return understood

    def describe(self, jpeg: bytes) -> str | None:
        """What is on the desk, in one or two spoken Chinese sentences, from the head camera's picture (GPT looks);
        None when GPT is unavailable."""
        if not self.chat.configured:
            return None
        try:
            answer = self.chat.look(LOOK_SYSTEM, "桌上有什么？", jpeg)
            logger.info("看桌面（GPT）：{}", answer)
            return answer
        except Exception as error:
            logger.warning("GPT 没看成（{}）", error)
            return None

    @staticmethod
    def chat_by_words(history: list[dict], text: str) -> dict:
        """The word table on a chat turn: the object from this turn, else from the user's earlier ones."""
        words = VoiceDialog.by_words(text)
        thing = words["object"]
        for turn in reversed(history):
            if thing:
                break
            if turn.get("role") == "user":
                thing = VoiceDialog.by_words(turn.get("text", ""))["object"]
        side = "middle" if "中间" in text else words["side"]
        ordinal = next((n for n, word in enumerate(("第一", "第二", "第三", "第四"), start=1) if word in text), None)
        intent = ("stop" if any(word in text for word in ("停", "别动"))
                  else "deny" if any(word in text for word in ("不对", "不是"))
                  else "confirm" if text.strip("。！!，, ") in ("对", "对的", "是的", "是",
                                                                "没错", "可以", "好", "好的")
                  else "describe" if any(word in text for word in ("看见", "看到", "有什么"))
                  else "include" if any(word in text for word in ("也是", "也算", "包括"))
                  else "start" if any(word in text for word in ("开始", "抓吧"))
                  else "choose")
        understood = {"intent": intent, "part": "盖子" if "盖" in text else None, "object": thing, "side": side,
                      "ordinal": ordinal, "nearest": "近" in text, "by": "words"}
        return understood

    @staticmethod
    def by_words(text: str) -> dict:
        # The longest name first, so 喷雾 wins over 瓶 in 喷雾瓶.
        word = next((name for name in sorted(DialogConfig.OBJECT_WORDS, key=len, reverse=True) if name in text), None)
        side = next((value for key, value in DialogConfig.SIDE_WORDS.items()
                     if f"{key}边" in text or f"{key}侧" in text), None)
        place = "box" if "盒" in text and word not in ("盒子",) else "back"
        thing = DialogConfig.OBJECT_WORDS[word] if word else None
        where = {"left": "左边的", "right": "右边的"}.get(side, "")
        reply = f"好的，抓{where}{word}。" if word else "没听清要抓什么，请再说一次。"
        understood = {"object": thing, "side": side, "place": place, "reply": reply, "by": "words"}
        return understood


def demo_voice_dialog() -> None:
    """Online (GPT) and offline (the word table) on a few instructions, the ASR's 水平 included."""
    dialog = VoiceDialog()
    for text in ("把右边的水平抓起来放回原处。", "抓那个杯子", "把左侧的瓶子放进盒子里", "你好"):
        logger.info("{} → GPT {} / 词表 {}", text, dialog.understand(text), dialog.by_words(text))


def main() -> None:
    use_utf8_output()
    demo_voice_dialog()


if __name__ == "__main__":
    main()
