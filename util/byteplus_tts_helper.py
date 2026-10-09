"""BytePlus Seed Speech 2.0（单向流式 TTS）：一句文字进，16 位单声道 PCM 出。移植自 g1-action-recorder。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import base64
import json
from uuid import uuid4

import httpx
from loguru import logger

from config.settings import use_utf8_output

from config.voice import AlertConfig, ByteplusTtsConfig


class ByteplusTtsHelper:
    """The answer is JSON objects back to back: code 0 carries base64 PCM (or sentence metadata without audio),
    20000000 marks the end, any other code is a failure."""

    def __init__(self, api_key: str = ByteplusTtsConfig.API_KEY, **kwargs) -> None:
        self.api_key = api_key.strip()
        self.url = kwargs.get("url", ByteplusTtsConfig.URL)
        self.timeout = kwargs.get("timeout", ByteplusTtsConfig.TIMEOUT_S)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str, voice: str = ByteplusTtsConfig.VOICE) -> bytes:
        """Mono 16-bit PCM at ByteplusTtsConfig.SAMPLE_RATE."""
        headers = {"X-Api-Key": self.api_key, "X-Api-Resource-Id": ByteplusTtsConfig.RESOURCE_ID,
                   "X-Api-Request-Id": str(uuid4()), "Content-Type": "application/json"}
        payload = {"user": {"uid": ByteplusTtsConfig.USER_ID},
                   "req_params": {"text": text, "speaker": voice,
                                  "audio_params": {"format": "pcm", "sample_rate": ByteplusTtsConfig.SAMPLE_RATE}}}
        response = httpx.post(self.url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        pcm = bytearray()
        completed = False
        decoder = json.JSONDecoder()
        body = response.text.strip()
        while body:
            if body.startswith("data:"):
                body = body[5:].lstrip()
            message, end = decoder.raw_decode(body)
            body = body[end:].lstrip()
            code = message.get("code")
            if code == 20000000:
                completed = True
            elif code == 0:
                pcm.extend(base64.b64decode(message.get("data") or ""))
            else:
                raise RuntimeError(f"BytePlus 合成失败（{code}）：{message.get('message')}")
        if not completed or not pcm:
            raise RuntimeError("BytePlus 没有返回音频")
        audio = bytes(pcm)
        return audio


def demo_byteplus_tts_helper() -> None:
    """Synthesizes the fallen-bottle alert (needs BYTEPLUS_API_KEY in .env); writes nothing."""
    helper = ByteplusTtsHelper()
    if not helper.configured:
        logger.info("BYTEPLUS_API_KEY 没有设置，不发请求")
        return
    pcm = helper.synthesize(AlertConfig.BOTTLE_FALLEN)
    logger.info("BytePlus {}：{:.2f} s", ByteplusTtsConfig.VOICE, len(pcm) / 2 / ByteplusTtsConfig.SAMPLE_RATE)


def main() -> None:
    use_utf8_output()
    demo_byteplus_tts_helper()


if __name__ == "__main__":
    main()
