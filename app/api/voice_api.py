"""任务页的对话和语音接口：一轮对话（GPT 理解，词表兜底），页面自己选了之后的下一句，录音转文字（BytePlus ASR），
回答说出来（BytePlus TTS），以及哪一页在说话。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio

from fastapi import APIRouter, Request, Response
from loguru import logger
from pydantic import BaseModel

from app.context import grasp_context
from config.settings import use_utf8_output

router = APIRouter(tags=["voice"])


class ChatRequest(BaseModel):
    text: str
    # The conversation before this turn: {"role": "user" / "robot", "text"}.
    history: list[dict] = []
    # What the robot last asked for: "target" (which object), "place" (where to) or "confirm" (is it all right).
    awaiting: str = "target"
    # Which page's chat: "pick" (物体抓取) or "place" (抓取摆放).
    task: str = "pick"


class NextRequest(BaseModel):
    task: str = "pick"


class SayRequest(BaseModel):
    text: str


class SpeakerRequest(BaseModel):
    # A page's own random id (made once per page load).
    client: str


@router.post("/api/task/chat")
def chat_turn(request: ChatRequest) -> dict:
    """One turn of a task page's chat; every reply says what it waits for next (awaiting).
    Plain def: a worker thread."""
    result = grasp_context.chat_turn(request.task, request.text, request.history, request.awaiting)
    return result


@router.post("/api/task/next")
def chat_next(request: NextRequest) -> dict:
    """What a task page's chat says after the page itself chose (a click, a tapped answer)."""
    result = grasp_context.chat_next(request.task)
    return result


@router.post("/api/voice/transcribe")
async def transcribe(request: Request) -> dict:
    """The body: the page's recording, 16 kHz mono int16 PCM."""
    pcm = await request.body()
    result = await asyncio.to_thread(grasp_context.transcribe, pcm)
    return result


@router.post("/api/voice/say")
def say(request: SayRequest) -> Response:
    """The reply as WAV for the page to play."""
    wav = grasp_context.speech(request.text)
    response = Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})
    return response


@router.post("/api/voice/speaker")
def claim_speaker(request: SpeakerRequest) -> dict:
    result = grasp_context.claim_speaker(request.client)
    return result


@router.get("/api/voice/speaker")
def current_speaker() -> dict:
    result = {"client": grasp_context.speaker_client}
    return result


def demo_voice_api() -> None:
    """Online (BytePlus): the reply spoken, then recognised back through the transcribe endpoint."""
    import io
    import wave

    import numpy as np
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from config.voice import ByteplusAsrConfig, ByteplusTtsConfig

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    spoken = client.post("/api/voice/say", json={"text": "抓右边的水瓶"})
    with wave.open(io.BytesIO(spoken.content)) as clip:
        pcm24 = np.frombuffer(clip.readframes(clip.getnframes()), np.int16)
    times = np.arange(0, len(pcm24) / ByteplusTtsConfig.SAMPLE_RATE, 1 / ByteplusAsrConfig.SAMPLE_RATE)
    pcm16 = np.interp(times, np.arange(len(pcm24)) / ByteplusTtsConfig.SAMPLE_RATE, pcm24).astype(np.int16).tobytes()
    logger.info("say {} 字节 → transcribe {}", len(spoken.content),
                client.post("/api/voice/transcribe", content=pcm16).json())


def main() -> None:
    use_utf8_output()
    demo_voice_api()


if __name__ == "__main__":
    main()
