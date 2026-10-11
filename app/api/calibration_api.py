"""相机标定页的接口：标定会话（开始、采集、计算、保存），当前的标定结果，以及头部 / 左腕 / 右腕相机里实时的 ChArUco 检测
（画了角点的 JPEG）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, Response
from loguru import logger
from pydantic import BaseModel

from app.context import grasp_context
from config.settings import use_utf8_output

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


class SessionRequest(BaseModel):
    arm: Literal["left", "right"]


@router.get("/session")
def session_status() -> dict:
    result = grasp_context.calibration_session().status()
    return result


@router.post("/session")
def session_start(request: SessionRequest) -> dict:
    result = grasp_context.calibration_session().start(request.arm)
    return result


@router.post("/capture")
def capture() -> dict:
    """The current wrist view, head RGB-D and joints into the session (nothing moves). Plain def: a worker thread."""
    result = grasp_context.calibration_session().capture()
    return result


@router.post("/solve")
def solve() -> dict:
    """Starts the solve in the background; the page polls the session."""
    result = grasp_context.calibration_session().solve()
    return result


@router.post("/save")
def save() -> dict:
    result = grasp_context.calibration_session().save()
    return result


@router.get("/results")
def results() -> dict:
    result = grasp_context.calibration_results()
    return result


@router.get("/detect/{camera}.jpg")
def detect(camera: Literal["head", "left_wrist", "right_wrist"]) -> Response:
    """The camera's picture with the board's corners drawn; X-Corners and X-Error-Px tell the page how many."""
    data, count, error = grasp_context.board_picture(camera)
    headers = {"X-Corners": str(count), "X-Error-Px": f"{error:.2f}" if error is not None else "",
               "Cache-Control": "no-store"}
    response = Response(content=data, media_type="image/jpeg", headers=headers)
    return response


def demo_calibration_api() -> None:
    """Needs the robot process: the results, and the board in the head camera (nothing moves)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    logger.info("标定结果 {} 个", len(client.get("/api/calibration/results").json()["results"]))
    head = client.get("/api/calibration/detect/head.jpg")
    logger.info("头部：{}，角点 {}", head.status_code, head.headers.get("x-corners"))


def main() -> None:
    use_utf8_output()
    demo_calibration_api()


if __name__ == "__main__":
    main()
