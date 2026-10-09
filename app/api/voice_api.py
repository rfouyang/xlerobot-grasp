"""任务页的语音接口：录音转文字（BytePlus ASR），一句指令选目标（GPT 理解，词表兜底），回答说出来（BytePlus TTS）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import io
import wave

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel

from config.settings import use_utf8_output
from config.voice import ByteplusAsrConfig, ByteplusTtsConfig


class CommandRequest(BaseModel):
    text: str


class SayRequest(BaseModel):
    text: str


def wav(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def create_voice_api() -> APIRouter:
    router = APIRouter()
    helpers: dict = {}

    def helper(name: str):
        if name not in helpers:
            if name == "asr":
                from util.byteplus_asr_helper import ByteplusAsrHelper

                helpers[name] = ByteplusAsrHelper()
            elif name == "tts":
                from util.byteplus_tts_helper import ByteplusTtsHelper

                helpers[name] = ByteplusTtsHelper()
            else:
                from component.voice.dialog import VoiceDialog

                helpers[name] = VoiceDialog()
        return helpers[name]

    @router.post("/api/voice/transcribe")
    async def transcribe(request: Request):
        """The body: the page's recording, 16 kHz mono int16 PCM."""
        import asyncio

        pcm = await request.body()
        if len(pcm) < ByteplusAsrConfig.SAMPLE_RATE * 2 * 0.3:
            response = {"ok": False, "error": "录音太短"}
            return response
        try:
            text = await asyncio.to_thread(helper("asr").transcribe, pcm)
        except Exception as error:
            logger.warning("语音识别失败：{}", error)
            response = {"ok": False, "error": f"语音识别失败：{error}"}
            return response
        response = {"ok": bool(text), "text": text, "seconds": round(len(pcm) / 2 / ByteplusAsrConfig.SAMPLE_RATE, 1),
                    **({} if text else {"error": "没听清，请再说一次"})}
        return response

    @router.post("/api/voice/command")
    def command(request: CommandRequest):
        """One instruction: understood, then the target chosen (by name, and side when it says one)."""
        from app.context import grasp_context

        understood = helper("dialog").understand(request.text)
        if not understood["object"]:
            response = {"ok": False, "understood": understood, "reply": understood["reply"], "error": "没听出要抓什么"}
            return response
        target = grasp_context.real.set_target(understood["object"], None, False, understood["side"])
        reply = understood["reply"] if target.get("ok") else f"没找到{understood['object']}，请在画面里点一下它。"
        response = {"ok": bool(target.get("ok")), "understood": understood, "target": target, "reply": reply,
                    **({} if target.get("ok") else {"error": target.get("error")})}
        return response

    @router.post("/api/voice/say")
    def say(request: SayRequest):
        """The reply as WAV for the page to play."""
        text = request.text.strip()[:200]
        if not text:
            raise HTTPException(status_code=400, detail="没有要说的")
        try:
            pcm = helper("tts").synthesize(text, voice=ByteplusTtsConfig.REPLY_VOICE)
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"语音合成失败：{error}")
        response = Response(content=wav(pcm, ByteplusTtsConfig.SAMPLE_RATE), media_type="audio/wav", headers={"Cache-Control": "no-store"})
        return response

    return router


def demo_voice_api() -> None:
    """Online: the reply spoken, then recognised back through the transcribe endpoint."""
    import numpy as np
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_voice_api())
    client = TestClient(app)
    spoken = client.post("/api/voice/say", json={"text": "抓右边的水瓶"})
    with wave.open(io.BytesIO(spoken.content)) as clip:
        pcm24 = np.frombuffer(clip.readframes(clip.getnframes()), np.int16)
    times = np.arange(0, len(pcm24) / ByteplusTtsConfig.SAMPLE_RATE, 1 / ByteplusAsrConfig.SAMPLE_RATE)
    pcm16 = np.interp(times, np.arange(len(pcm24)) / ByteplusTtsConfig.SAMPLE_RATE, pcm24).astype(np.int16).tobytes()
    logger.info("say {} 字节 → transcribe {}", len(spoken.content), client.post("/api/voice/transcribe", content=pcm16).json())


def main() -> None:
    use_utf8_output()
    demo_voice_api()


if __name__ == "__main__":
    main()
