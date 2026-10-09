"""真机抓取接口：按顺序一步一步执行，状态，停止，重置，规划图，实时深度（原始 / LingBot）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import BaseModel

from app.context import grasp_context
from config.settings import use_utf8_output


class StageRequest(BaseModel):
    name: str


class ActionRequest(BaseModel):
    action: Literal["grasp", "move_right", "place_in_box", "pick_target"]
    ignore_left: bool = False


class ResetRequest(BaseModel):
    # The page's task: the grasp page resets to "grasp", the pick-place page to "place_in_box".
    action: Literal["grasp", "move_right", "place_in_box", "pick_target"] = "grasp"


class TargetRequest(BaseModel):
    # A name for YOLOE (English), a click (u, v) in the head image, or clear (back to the default bottle).
    prompt: str | None = None
    point: list[float] | None = None
    clear: bool = False


def create_real_grasp_api() -> APIRouter:
    router = APIRouter()

    @router.get("/api/real/status")
    def status():
        response = grasp_context.real_status()
        return response

    @router.post("/api/real/stage")
    def stage(request: StageRequest):
        """Plain def: runs in a worker thread while the page polls the status."""
        response = grasp_context.real_run(request.name)
        return response

    @router.post("/api/real/run_all")
    def run_all():
        """Every step left, then torque off; returns at once."""
        response = grasp_context.real_run_all()
        return response

    @router.post("/api/real/action")
    def action(request: ActionRequest):
        """From the start: move the bottle to the right, or grasp anyway after the too-far-left stop; returns at once."""
        response = grasp_context.real_action(request.action, request.ignore_left)
        return response

    @router.post("/api/real/reset")
    def reset(request: ResetRequest | None = None):
        """No body: back to the grasp."""
        response = grasp_context.real_reset(request.action if request else "grasp")
        return response

    @router.post("/api/real/target")
    def target(request: TargetRequest):
        """The interactive grasp page's choice; a click alone is named by the prompt-free YOLOE (blocks ~1 s)."""
        response = grasp_context.real_target(request.prompt, request.point, request.clear)
        return response

    @router.post("/api/real/stop")
    def stop():
        response = grasp_context.real_stop()
        return response

    @router.get("/api/real/plan.jpg")
    def plan_image():
        path = grasp_context.real_plan_image()
        if path is None:
            raise HTTPException(status_code=404, detail="还没有规划")
        response = FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return response

    @router.get("/api/real/align.jpg")
    def align_image():
        path = grasp_context.real_align_image()
        if path is None:
            raise HTTPException(status_code=404, detail="还没有对准")
        response = FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return response

    @router.get("/api/depth/{kind}.jpg")
    def depth_preview(kind: str):
        """The live head depth, kind raw (the sensor's) or refined (what planning uses), detect (the head RGB with the
        bottle and the box framed) or target (the head RGB with the interactive page's target); the page fetches it
        again and again, which keeps the preview running."""
        picture = grasp_context.depth_preview_jpeg(kind)
        if picture is None:
            raise HTTPException(status_code=404, detail="深度预览还没有画面")
        response = Response(content=picture, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return response

    return router


def demo_real_grasp_api() -> None:
    """Needs the bringup launch and the teleop service; only reads the status and tries a step out of order."""
    grasp_context.start()
    app = FastAPI()
    app.include_router(create_real_grasp_api())
    client = TestClient(app)
    logger.info("/api/real/status：下一步 {}", client.get("/api/real/status").json()["next"])
    logger.info("跳步（lift）：{}", client.post("/api/real/stage", json={"name": "lift"}).json())
    logger.info("没规划时取图：{}", client.get("/api/real/plan.jpg").status_code)


def main() -> None:
    use_utf8_output()
    demo_real_grasp_api()


if __name__ == "__main__":
    main()
