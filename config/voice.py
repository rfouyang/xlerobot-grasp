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
    """BytePlus Seed Speech 2.0, as in g1-action-recorder (the user prefers it to Windows' voices). The key is in .env."""

    API_KEY = os.getenv("BYTEPLUS_API_KEY", "")
    URL = os.getenv("BYTEPLUS_API_BASE_URL", "https://voice.ap-southeast-1.bytepluses.com") + "/api/v3/tts/unidirectional"
    RESOURCE_ID = os.getenv("BYTEPLUS_TTS_RESOURCE_ID", "seed-tts-2.0")
    USER_ID = "xlerobot-grasp"
    TIMEOUT_S = 30.0
    SAMPLE_RATE = 24000
    # Clean, clear and gentle (g1-action-recorder/config/byteplus_voices.json lists the others).
    VOICE = "en_female_nadia_uranus_bigtts"
