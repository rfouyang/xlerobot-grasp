"""语音提醒：BytePlus 合成一句英文（做一次就存成 WAV），从 WSL 调 Windows 在默认输出设备（蓝牙音箱）上播。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import hashlib
import subprocess
import threading
import wave

from loguru import logger

from config.settings import AlertConfig, ByteplusTtsConfig, use_utf8_output
from util.byteplus_tts_helper import ByteplusTtsHelper


class SpeechHelper:
    """Speaking does not block the robot: synthesis and playback run in a thread of their own."""

    def __init__(self, powershell: str = AlertConfig.POWERSHELL, **kwargs) -> None:
        self.powershell = powershell
        self.tts = kwargs.get("tts") or ByteplusTtsHelper()
        self.clip_dir = Path(kwargs.get("clip_dir", AlertConfig.CLIP_DIR))

    def say(self, text: str, **kwargs) -> threading.Thread:
        """wait: block until it has been said (the demo)."""
        thread = threading.Thread(target=self._say, args=(text,), name="speech", daemon=True)
        thread.start()
        if kwargs.get("wait"):
            thread.join()
        return thread

    def _say(self, text: str) -> None:
        try:
            clip = self.clip(text)
            windows_path = subprocess.run(["wslpath", "-w", str(clip)], capture_output=True, text=True, check=True).stdout.strip()
            # The speaker unmuted and at least MIN_VOLUME_PERCENT loud first (someone had turned it down to 13, 12:16).
            # The script runs from beside the clips on C:, which PowerShell trusts more than a WSL path.
            script = self.clip_dir / AlertConfig.VOLUME_SCRIPT.name
            script.write_bytes(AlertConfig.VOLUME_SCRIPT.read_bytes())
            script_path = subprocess.run(["wslpath", "-w", str(script)], capture_output=True, text=True, check=True).stdout.strip()
            self.powershell_run(f"& '{script_path}' -MinPercent {AlertConfig.MIN_VOLUME_PERCENT} | Out-Null; "
                                f"(New-Object System.Media.SoundPlayer '{windows_path}').PlaySync()")
        except Exception as error:
            # In its own thread nothing else would see the failure.
            logger.warning("语音提醒没有播出：{}", error)

    def clip(self, text: str) -> Path:
        """The WAV of text in the BytePlus voice, synthesized the first time only."""
        key = hashlib.sha1(f"{ByteplusTtsConfig.VOICE}|{text}".encode()).hexdigest()[:16]
        path = self.clip_dir / f"{key}.wav"
        if not path.exists():
            if not self.tts.configured:
                raise RuntimeError("BYTEPLUS_API_KEY 没有设置")
            pcm = self.tts.synthesize(text)
            self.clip_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as file:
                file.setnchannels(1)
                file.setsampwidth(2)
                file.setframerate(ByteplusTtsConfig.SAMPLE_RATE)
                file.writeframes(pcm)
            logger.info("语音提醒已合成：{}（{:.1f} s）", path, len(pcm) / 2 / ByteplusTtsConfig.SAMPLE_RATE)
        return path

    def powershell_run(self, script: str) -> None:
        # Bypass: Windows may refuse to run the volume script file under its default policy.
        subprocess.run([self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60)


def demo_speech_helper() -> None:
    """Says the fallen-bottle alert once on the Windows speaker."""
    SpeechHelper().say(AlertConfig.BOTTLE_FALLEN, wait=True)


def main() -> None:
    use_utf8_output()
    demo_speech_helper()


if __name__ == "__main__":
    main()
