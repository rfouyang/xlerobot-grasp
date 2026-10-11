"""相机标定页的实时检测：头部 / 左腕 / 右腕相机里的 ChArUco 角点，画在 JPEG 上；所有打开的页面共用，每个相机最多每
DETECT_PERIOD_S 检测一次。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

import cv2
from loguru import logger

from config.charuco import CharucoConfig
from config.settings import use_utf8_output


class LiveDetection:
    def __init__(self) -> None:
        self.helper = None
        self.teleop = None
        self.lock = threading.Lock()
        # camera -> (when, JPEG, corner count, reprojection error px or None)
        self.cache: dict[str, tuple[float, bytes, int, float | None]] = {}

    def picture(self, camera: str) -> tuple[bytes, int, float | None]:
        """(JPEG with the corners drawn, how many corners, the board's reprojection error in px or None)."""
        with self.lock:
            cached = self.cache.get(camera)
            if cached and time.monotonic() - cached[0] < CharucoConfig.DETECT_PERIOD_S:
                return cached[1:]
            if self.helper is None:
                from util.charuco_helper import CharucoHelper
                from util.teleop_helper import TeleopHelper

                self.helper, self.teleop = CharucoHelper(), TeleopHelper()
            if camera == "head":
                frame = self.teleop.rgbd()
                rgb, K = frame["color"], frame["K"]
            else:
                rgb, K = self.teleop.camera(camera), None
            view = self.helper.detect(rgb, K)
            data = cv2.imencode(".jpg", self.helper.draw(rgb, view), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
            self.cache[camera] = (time.monotonic(), data, view.count, view.error_px)
            picture = (data, view.count, view.error_px)
            return picture


def demo_live_detection() -> None:
    """Needs the robot process: the head camera's board corners (nothing moves)."""
    data, count, error = LiveDetection().picture("head")
    logger.info("头部：{} 个角点，误差 {} px，JPEG {} 字节", count, error, len(data))


def main() -> None:
    use_utf8_output()
    demo_live_detection()


if __name__ == "__main__":
    main()
