"""BytePlus Seed ASR（双向流式 WebSocket）：一段 16 kHz 单声道 PCM 进，文字出。帧格式、握手和结束方式照搬
respeaker-voicebot 的 util/asr_helper.py（它又来自 robot-concierge，都是对着线上服务调通的）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio
import gzip
import json
import struct
import uuid

import aiohttp
from loguru import logger

from config.settings import use_utf8_output
from config.voice import ByteplusAsrConfig, ByteplusTtsConfig


class ByteplusAsrHelper:
    """A frame: 4-byte header (version 1, header 1 word; type << 4 | flags; JSON + gzip; reserved), a sequence number
    (negative on the last frame), the payload size, the gzipped payload. Type 1 is the config, 2 audio; the server
    answers 9 (full response) or 15 (error)."""

    def __init__(self, api_key: str = ByteplusTtsConfig.API_KEY, **kwargs) -> None:
        self.api_key = api_key.strip()
        self.frame_ms = kwargs.get("frame_ms", ByteplusAsrConfig.FRAME_MS)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def frame(message_type: int, sequence: int, payload: bytes, last: bool = False) -> bytes:
        flags = 3 if last else 1
        header = bytes([0x11, message_type << 4 | flags, 0x11, 0x00])
        compressed = gzip.compress(payload)
        sequence = -sequence if last else sequence
        return header + struct.pack(">iI", sequence, len(compressed)) + compressed

    @staticmethod
    def parse(data: bytes) -> dict:
        """One server frame: the header, the prefixes its flags announce, then JSON (gzipped when the nibble says)."""
        header_size = (data[0] & 0x0F) * 4
        message_type = data[1] >> 4
        flags = data[1] & 0x0F
        compression = data[2] & 0x0F
        payload = data[header_size:]
        if flags & 1:
            payload = payload[4:]
        if flags & 4:
            payload = payload[4:]
        if message_type == 9:
            payload = payload[4:]
        elif message_type == 15:
            code = struct.unpack(">i", payload[:4])[0]
            size = struct.unpack(">I", payload[4:8])[0]
            message = payload[8: 8 + size]
            if compression == 1:
                message = gzip.decompress(message)
            raise RuntimeError(f"BytePlus ASR error {code}: {message.decode(errors='replace')}")
        if not payload:
            return {}
        if compression == 1:
            payload = gzip.decompress(payload)
        return json.loads(payload)

    @staticmethod
    def text(payload: dict) -> str:
        result = payload.get("result", {})
        results = result if isinstance(result, list) else [result]
        return " ".join(item.get("text", "").strip() for item in results).strip()

    @staticmethod
    def is_final(payload: dict) -> bool:
        result = payload.get("result", {})
        results = result if isinstance(result, list) else [result]
        return any(utterance.get("definite", False) for item in results for utterance in item.get("utterances", []))

    def config(self) -> dict:
        config = {"user": {"uid": ByteplusAsrConfig.UID},
                  "audio": {"format": "pcm", "codec": "raw", "rate": ByteplusAsrConfig.SAMPLE_RATE, "bits": 16,
                            "channel": 1},
                  "request": {"model_name": ByteplusAsrConfig.MODEL, "enable_itn": True, "enable_punc": True,
                              "show_utterances": True, "end_window_size": ByteplusAsrConfig.END_WINDOW_MS}}
        return config

    async def transcribe_async(self, pcm: bytes) -> str:
        """The whole recording at once (not paced like live speech: it is already recorded), then the final text."""
        frame_bytes = ByteplusAsrConfig.SAMPLE_RATE * self.frame_ms // 1000 * 2
        headers = {"X-Api-Key": self.api_key, "X-Api-Resource-Id": ByteplusAsrConfig.RESOURCE_ID,
                   "X-Api-Request-Id": str(uuid.uuid4())}
        text = ""
        timeout = aiohttp.ClientTimeout(total=ByteplusAsrConfig.MAX_SECONDS + ByteplusAsrConfig.FINAL_TIMEOUT_S + 10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(ByteplusAsrConfig.URL, headers=headers) as socket:
                await socket.send_bytes(self.frame(1, 1, json.dumps(self.config()).encode()))
                self.parse((await socket.receive(timeout=ByteplusAsrConfig.FINAL_TIMEOUT_S)).data)
                sequence = 2
                for offset in range(0, len(pcm), frame_bytes):
                    await socket.send_bytes(self.frame(2, sequence, pcm[offset: offset + frame_bytes]))
                    sequence += 1
                    # Partial results queue up meanwhile; take what is there and go on sending.
                    while True:
                        try:
                            message = await socket.receive(timeout=0.001)
                        except TimeoutError:
                            break
                        if message.type == aiohttp.WSMsgType.BINARY:
                            text = self.text(self.parse(message.data)) or text
                await socket.send_bytes(self.frame(2, sequence, b"", last=True))
                while True:
                    try:
                        message = await socket.receive(timeout=ByteplusAsrConfig.FINAL_TIMEOUT_S)
                    except TimeoutError:
                        break
                    if message.type != aiohttp.WSMsgType.BINARY:
                        break
                    payload = self.parse(message.data)
                    text = self.text(payload) or text
                    if self.is_final(payload) or message.data[1] & 2:
                        break
        return text

    def transcribe(self, pcm: bytes) -> str:
        if not self.configured:
            raise RuntimeError("没有 BYTEPLUS_API_KEY（.env）")
        seconds = len(pcm) / 2 / ByteplusAsrConfig.SAMPLE_RATE
        if seconds > ByteplusAsrConfig.MAX_SECONDS:
            pcm = pcm[: int(ByteplusAsrConfig.MAX_SECONDS * ByteplusAsrConfig.SAMPLE_RATE) * 2]
        text = asyncio.run(self.transcribe_async(pcm))
        logger.info("语音识别（{:.1f} s）：{}", seconds, text)
        return text


def demo_byteplus_asr_helper() -> None:
    """Online, no microphone: a sentence spoken by the TTS (resampled to 16 kHz), recognised back."""
    from difflib import SequenceMatcher

    import numpy as np

    from util.byteplus_tts_helper import ByteplusTtsHelper

    spoken = "把右边的水瓶抓起来放回原处。"
    pcm24 = np.frombuffer(ByteplusTtsHelper().synthesize(spoken, voice=ByteplusTtsConfig.REPLY_VOICE), np.int16)
    times = np.arange(0, len(pcm24) / ByteplusTtsConfig.SAMPLE_RATE, 1 / ByteplusAsrConfig.SAMPLE_RATE)
    pcm16 = np.interp(times, np.arange(len(pcm24)) / ByteplusTtsConfig.SAMPLE_RATE, pcm24).astype(np.int16).tobytes()
    text = ByteplusAsrHelper().transcribe(pcm16)
    logger.info("原文 {} → 识别 {}（相似度 {:.2f}）", spoken, text, SequenceMatcher(None, spoken, text).ratio())


def main() -> None:
    use_utf8_output()
    demo_byteplus_asr_helper()


if __name__ == "__main__":
    main()
