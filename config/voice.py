"""Voice: the BytePlus TTS alerts (and, next, speech recognition and the dialog)."""

from __future__ import annotations

import os
from pathlib import Path

from config.settings import BASE_DIR


class AlertConfig:
    """Back at the start pose, the head camera looks again: a bottle that fell lies about its diameter high instead
    of its height. Until it stands, a voice on the Windows speaker asks someone at the desk to stand it up (the
    user, 2026-10-06, operating remotely)."""

    # Upright when the bottle seen is at least this share of its height at planning.
    UPRIGHT_HEIGHT_SHARE = 0.6
    BOTTLE_FALLEN = "Olis, the bottle handling failed. Could you please help move it straight up?"
    # Once it stands again someone is at the desk: thanks and a warning, this long to step back, a second warning,
    # and only then the robot moves (the user, 2026-10-06).
    THANKS_STEP_AWAY = "Thank you! Please step away from the robot."
    CLEAR_WAIT_S = 10.0
    ABOUT_TO_MOVE = "The robot is about to move. Please stay away from the robot."
    # WSL has no sound here: powershell.exe (WSL interop) plays on Windows' default device, the Bluetooth speaker.
    POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    # The clips are made once by BytePlus and kept (Windows has to reach the file, so it sits on the C: drive).
    CLIP_DIR = Path("/mnt/c/Users/rfouy/AppData/Local/Temp/xlerobot_alerts")
    # Before each alert the speaker is unmuted and turned up to at least this (louder stays); Olis has to hear it
    # elsewhere in the home (the user, 2026-10-06).
    MIN_VOLUME_PERCENT = 65
    VOLUME_SCRIPT = BASE_DIR / "asset" / "windows" / "ensure_volume.ps1"
    # Said again after this pause and another look, until the bottle stands: once is easily missed elsewhere in the
    # home (the user, 2026-10-06).
    RECHECK_PAUSE_S = 5.0


class ByteplusTtsConfig:
    """BytePlus Seed Speech 2.0, as in g1-action-recorder (the user prefers it to Windows' voices).
    The key is in .env."""

    API_KEY = os.getenv("BYTEPLUS_API_KEY", "")
    URL = (os.getenv("BYTEPLUS_API_BASE_URL", "https://voice.ap-southeast-1.bytepluses.com")
           + "/api/v3/tts/unidirectional")
    RESOURCE_ID = os.getenv("BYTEPLUS_TTS_RESOURCE_ID", "seed-tts-2.0")
    USER_ID = "xlerobot-grasp"
    TIMEOUT_S = 30.0
    SAMPLE_RATE = 24000
    # Clean, clear and gentle (g1-action-recorder/config/byteplus_voices.json lists the others).
    VOICE = "en_female_nadia_uranus_bigtts"
    # The task pages' spoken replies are Chinese (respeaker-voicebot's voice).
    REPLY_VOICE = "zh_female_yingyujiaoxue_uranus_bigtts"


class ByteplusAsrConfig:
    """BytePlus Seed ASR, bidirectional streaming over a WebSocket: the endpoint, resource and framing proven in
    services/respeaker-voicebot (util/asr_helper.py, from robot-concierge). Same key as the TTS."""

    URL = "wss://voice.ap-southeast-1.bytepluses.com/api/v3/sauc/bigmodel_async"
    RESOURCE_ID = "volc.seedasr.sauc.duration"
    MODEL = "bigmodel"
    UID = "xlerobot-grasp"
    # 16 kHz mono int16 PCM, sent in blocks of FRAME_MS.
    SAMPLE_RATE = 16000
    FRAME_MS = 200
    # How long an utterance stays open after the last audio (ms), and the wait for the final transcript (s).
    END_WINDOW_MS = 1000
    FINAL_TIMEOUT_S = 10.0
    # A recording longer than this is cut (s): the page records one instruction.
    MAX_SECONDS = 30.0


class DialogConfig:
    """A spoken or typed instruction on a task page -> what to grasp (an English noun for YOLOE), which side of the
    desk, and a short spoken reply. GPT (the key OPENAI_API_KEY in .env) understands it; without the network a word
    table does."""

    OPENAI_URL = "https://api.openai.com/v1/chat/completions"
    MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    TIMEOUT_S = 20.0
    # The word table: Chinese names -> YOLOE prompts (YOLOE's text encoder is English).
    # 水平: the ASR hears 水瓶 as it (2026-10-09).
    OBJECT_WORDS = {"水瓶": "bottle", "水平": "bottle", "瓶子": "bottle", "瓶": "bottle", "喷雾": "spray bottle",
                    "杯子": "cup", "杯": "cup", "罐子": "can", "易拉罐": "can", "罐": "can", "盒子": "box",
                    "碗": "bowl", "香蕉": "banana", "苹果": "apple", "橙子": "orange", "手机": "cell phone",
                    "遥控器": "remote", "书": "book", "笔": "pen"}
    SIDE_WORDS = {"左": "left", "右": "right"}
    # How the robot names a YOLOE prompt in its Chinese replies (others as they are).
    SAY_NAMES = {"bottle": "瓶子", "spray bottle": "喷雾瓶", "cup": "杯子", "can": "罐子", "box": "盒子", "bowl": "碗",
                 "banana": "香蕉", "apple": "苹果", "orange": "橙子", "cell phone": "手机", "remote": "遥控器",
                 "book": "书", "pen": "笔"}
    # The chat on the task pages: this many earlier turns go to GPT with the new one.
    CHAT_TURNS = 8
    # The task chat's GPT with tools (component/chat): "low" reasoning answers a tool call in ~1.5 s ("minimal" ~1 s
    # but guessed numbers nobody said, the default ~3 s; 2026-10-10); at most this many tool rounds a turn.
    CHAT_EFFORT = os.getenv("OPENAI_CHAT_EFFORT", "low")
    CHAT_STEPS = 3
    # A start in the same turn as the pick needs one of these words (抓左边那个，直接抓); after the question whether
    # the target is right, an agreement is enough.
    START_WORDS = ("开始", "直接", "马上", "立刻", "现在就", "抓吧", "动手", "去抓", "go")
    # Said to the chat, these stop the robot at once, without GPT.
    STOP_WORDS = ("停", "别动", "等一下", "等等", "stop")
