"""OpenAI Chat Completions（HTTPS，不用 SDK）：一段系统提示 + 一句话进，一个 JSON 对象出。
key 只从 .env 读（OPENAI_API_KEY）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import base64
import json
import os

import httpx
from loguru import logger

from config.settings import use_utf8_output
from config.voice import DialogConfig


class OpenAIChatHelper:
    def __init__(self, api_key: str | None = None, **kwargs) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
        self.model = kwargs.get("model", DialogConfig.MODEL)
        self.timeout = kwargs.get("timeout", DialogConfig.TIMEOUT_S)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def json_reply(self, system: str, user: str) -> dict:
        """The model's answer as a JSON object (response_format json_object); raises on any failure."""
        response = httpx.post(DialogConfig.OPENAI_URL, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                              json={"model": self.model, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": user}]})
        if response.status_code != 200:
            # The body names the problem (model, quota); never the key.
            raise RuntimeError(f"OpenAI {response.status_code}: {response.text[:300]}")
        content = response.json()["choices"][0]["message"]["content"]
        reply = json.loads(content)
        return reply

    def tool_turn(self, messages: list[dict], tools: list[dict], tool_choice: str = "auto") -> dict:
        """One step of a conversation with tools (function calling): the model's message, with "tool_calls" when it
        calls any; raises on any failure."""
        body = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": tool_choice}
        if DialogConfig.CHAT_EFFORT:
            body["reasoning_effort"] = DialogConfig.CHAT_EFFORT
        response = httpx.post(DialogConfig.OPENAI_URL, timeout=self.timeout, json=body,
                              headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        if response.status_code != 200:
            raise RuntimeError(f"OpenAI {response.status_code}: {response.text[:300]}")
        message = response.json()["choices"][0]["message"]
        return message

    def look(self, system: str, question: str, jpeg: bytes) -> str:
        """The model's plain-text answer to question about the picture (a JPEG); raises on any failure."""
        picture = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        response = httpx.post(DialogConfig.OPENAI_URL, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                              json={"model": self.model, "messages": [
                                  {"role": "system", "content": system},
                                  {"role": "user", "content": [{"type": "text", "text": question},
                                                               {"type": "image_url", "image_url": {"url": picture}}]}]})
        if response.status_code != 200:
            raise RuntimeError(f"OpenAI {response.status_code}: {response.text[:300]}")
        answer = str(response.json()["choices"][0]["message"]["content"]).strip()
        return answer


def demo_openai_chat_helper() -> None:
    """Online: one tiny JSON answer."""
    reply = OpenAIChatHelper().json_reply('Answer as JSON: {"ok": true, "word": <the English for the noun>}.', "水瓶")
    logger.info("{}", reply)


def main() -> None:
    use_utf8_output()
    demo_openai_chat_helper()


if __name__ == "__main__":
    main()
