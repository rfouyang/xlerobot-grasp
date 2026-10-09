"""相机标定页的接口：当前的标定结果，以及头部 / 左腕 / 右腕相机里实时的 ChArUco 检测（画了角点的 JPEG）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time
from typing import Literal

import cv2
from fastapi import APIRouter, HTTPException, Response
from loguru import logger

from pydantic import BaseModel

from component.calibration.versions import calibrations
from config.charuco import CharucoConfig
from config.settings import use_utf8_output


class LiveDetection:
    """One picture per camera at most every DETECT_PERIOD_S, shared by every open page."""

    def __init__(self) -> None:
        self.helper = None
        self.teleop = None
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, bytes, int, float | None]] = {}

    def picture(self, camera: str) -> tuple[bytes, int, float | None]:
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
            return data, view.count, view.error_px


class SessionRequest(BaseModel):
    arm: Literal["left", "right"]


def create_calibration_api() -> APIRouter:
    router = APIRouter()
    live = LiveDetection()
    sessions: dict = {}

    def session():
        if "current" not in sessions:
            from component.calibration.capture import CalibrationSession

            sessions["current"] = CalibrationSession()
        return sessions["current"]

    @router.get("/api/calibration/session")
    def session_status():
        response = session().status()
        return response

    @router.post("/api/calibration/session")
    def session_start(request: SessionRequest):
        response = session().start(request.arm)
        return response

    @router.post("/api/calibration/capture")
    def capture():
        """The current wrist view, head RGB-D and joints into the session (nothing moves). Plain def: a worker thread."""
        response = session().capture()
        return response

    @router.post("/api/calibration/solve")
    def solve():
        """Starts the solve in the background; the page polls the session."""
        response = session().solve()
        return response

    @router.post("/api/calibration/save")
    def save():
        response = session().save()
        return response

    @router.get("/api/calibration/results")
    def results():
        response = {"results": calibrations(), "board": {"squares": CharucoConfig.SQUARES, "square_mm": CharucoConfig.SQUARE_M * 1000,
                                                         "marker_mm": CharucoConfig.MARKER_M * 1000, "dictionary": CharucoConfig.DICTIONARY}}
        return response

    @router.get("/api/calibration/detect/{camera}.jpg")
    def detect(camera: Literal["head", "left_wrist", "right_wrist"]):
        """The camera's picture with the board's corners drawn; X-Corners and X-Error-Px tell the page how many."""
        try:
            data, count, error = live.picture(camera)
        except Exception as failure:
            raise HTTPException(status_code=503, detail=f"{camera} 没有画面：{failure}")
        headers = {"X-Corners": str(count), "X-Error-Px": f"{error:.2f}" if error is not None else "", "Cache-Control": "no-store"}
        response = Response(content=data, media_type="image/jpeg", headers=headers)
        return response

    return router


def demo_calibration_api() -> None:
    """Needs the robot process: the results, and the board in the head camera."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_calibration_api())
    client = TestClient(app)
    logger.info("标定结果 {} 个", len(client.get("/api/calibration/results").json()["results"]))
    head = client.get("/api/calibration/detect/head.jpg")
    logger.info("头部：{}，角点 {}", head.status_code, head.headers.get("x-corners"))


def main() -> None:
    use_utf8_output()
    demo_calibration_api()


if __name__ == "__main__":
    main()
