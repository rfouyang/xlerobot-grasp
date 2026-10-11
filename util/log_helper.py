"""loguru 日志配置：终端 + 按天轮转文件，并接管标准库 logging（uvicorn / fastapi / lerobot）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import logging
import re
import time

from loguru import logger

from config.settings import PathConfig, use_utf8_output


class InterceptHandler(logging.Handler):
    """Route standard-library records (uvicorn's access log, lerobot) into loguru.

    lerobot's camera threads log one warning per failed read and retry at once: when the cameras dropped out
    (usbipd crashed) that wrote 750 MB in three minutes. A warning repeating the same text gets through at most
    once per REPEAT_S."""

    REPEAT_S = 10.0

    def __init__(self) -> None:
        super().__init__()
        self.last_seen: dict[str, float] = {}

    def emit(self, record):
        message = record.getMessage()
        if record.levelno >= logging.WARNING:
            # Same text up to the numbers in it (counters, timings, ports), as the repeats differ only there.
            key = re.sub(r"\d+", "#", message)[:160]
            now = time.monotonic()
            if now - self.last_seen.get(key, -self.REPEAT_S) < self.REPEAT_S:
                return
            self.last_seen[key] = now
        logger.opt(exception=record.exc_info).log(record.levelname, message)


def setup_logger(log_dir: Path, level: str = "INFO", name: str = "grasp") -> None:
    """Called once, at each process's entry; name is the file prefix (grasp_*.log, robot_*.log). A day per file,
    the old ones compressed, two weeks kept."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=level, diagnose=False)
    logger.add(log_dir / f"{name}_{{time:YYYY-MM-DD}}.log", rotation="00:00", retention="14 days", compression="gz",
               encoding="utf-8", level=level, diagnose=False)
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)


def demo_logger() -> None:
    """A flood of one warning: only the first gets through, plus an unrelated one."""
    log_dir = PathConfig.DEMO_DIR / "logs"
    setup_logger(log_dir)
    logger.info("日志已配置：{}", log_dir)
    camera_log = logging.getLogger("lerobot.cameras")
    for attempt in range(1000):
        camera_log.warning("Error reading frame in background thread for OpenCVCamera(/dev/video0): attempt %d",
                           attempt)
    logging.getLogger("uvicorn").warning("a different warning still gets through")


def main() -> None:
    use_utf8_output()
    demo_logger()


if __name__ == "__main__":
    main()
