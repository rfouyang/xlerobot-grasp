"""正在执行（一步或一次跑完）时，什么都不能选、不能开始。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from loguru import logger

from config.settings import use_utf8_output


class TaskBusyError(RuntimeError):
    """A step or a full run is going; the API answers it with 409."""


def demo_busy_error() -> None:
    try:
        raise TaskBusyError("正在执行，等这一轮结束再说")
    except TaskBusyError as error:
        logger.info("{}: {}", type(error).__name__, error)


def main() -> None:
    use_utf8_output()
    demo_busy_error()


if __name__ == "__main__":
    main()
