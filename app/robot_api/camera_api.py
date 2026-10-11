"""相机画面接口：每个相机一路 MJPEG 流，页面直接用 <img> 显示。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio
import io

import numpy as np
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient
from loguru import logger

from app.robot_context import robot_context
from config.robot import CameraStreamConfig
from config.settings import use_utf8_output

BOUNDARY = "frame"


router = APIRouter()

@router.get("/stream/{name}")
async def stream(name: str):
    if name not in CameraStreamConfig.CAMERAS:
        raise HTTPException(status_code=404, detail=f"没有相机 {name}")

    async def frames():
        while True:
            data = robot_context.camera_jpeg(name)
            if data:
                yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                       f"Content-Length: {len(data)}\r\n\r\n").encode() + data + b"\r\n"
            await asyncio.sleep(1.0 / CameraStreamConfig.STREAM_HZ)

    response = StreamingResponse(frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}")
    return response

@router.get("/api/rgbd")
def rgbd(infrared: bool = False):
    """The head's colour (RGB) + depth aligned to it, intrinsics K, depth scale (m per unit), and the head
    joints at that moment, as one compressed .npz (for the grasp service). infrared: also the raw infrared pair
    (ir_left, ir_right, 8-bit) with K_ir, baseline_m and color_from_ir, when the head streams it."""
    frame = robot_context.head_rgbd()
    if not infrared:
        frame = {key: value for key, value in frame.items()
                 if key not in ("ir_left", "ir_right", "K_ir", "baseline_m", "color_from_ir")}
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **frame)
    response = Response(content=buffer.getvalue(), media_type="application/octet-stream")
    return response

@router.get("/snapshot/{name}")
def snapshot(name: str):
    """One frame, for checks and the demo."""
    data = robot_context.camera_jpeg(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"相机 {name} 还没有画面")
    response = Response(content=data, media_type="image/jpeg")
    return response


def demo_camera_api() -> None:
    """Real cameras (and motors connected, no torque): one snapshot per camera."""
    import time

    robot_context.start()
    time.sleep(1.0)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for name in CameraStreamConfig.CAMERAS:
        response = client.get(f"/snapshot/{name}")
        logger.info("/snapshot/{}：{} {} {} 字节", name, response.status_code, response.headers["content-type"],
                    len(response.content))
    logger.info("/snapshot/tail：{}", client.get("/snapshot/tail").status_code)
    frame = np.load(io.BytesIO(client.get("/api/rgbd").content))
    logger.info("/api/rgbd：{}", {key: frame[key].shape for key in frame.files})
    robot_context.shutdown()


def main() -> None:
    use_utf8_output()
    demo_camera_api()


if __name__ == "__main__":
    main()
