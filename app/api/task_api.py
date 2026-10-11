"""真机任务接口（物体抓取、抓取摆放；挂在 /grasp-api 下）：状态、一步一步执行或一次跑完、重置、停止，
页面的选择（抓哪个、放哪里、用哪只手），规划图、对准图、头部实时画面。选不成的回答见 errors.py。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from loguru import logger
from pydantic import BaseModel

from app.context import grasp_context
from config.perception import PerceptionConfig
from config.settings import use_utf8_output

router = APIRouter(prefix="/api/real", tags=["task"])
# The head views keep their own address (/grasp-api/api/depth/...), as the pages have it.
depth_router = APIRouter(prefix="/api/depth", tags=["task"])
Action = Literal["grasp", "move_right", "place_in_box", "place_at_spot", "pick_target"]


class StageRequest(BaseModel):
    name: str


class ActionRequest(BaseModel):
    action: Action
    ignore_left: bool = False


class ResetRequest(BaseModel):
    # The page's task: 物体抓取 resets to "pick_target", 抓取摆放 to its place action.
    action: Action = "pick_target"


class PlaceRequest(BaseModel):
    # 抓取摆放's place: "box" or "spot"; with a point (u, v in the head image) where exactly; kind None: not chosen.
    kind: Literal["box", "spot"] | None = None
    point: list[float] | None = None
    # A point in a box: "click" (the bottle goes there) or "words" (a tapped answer: the middle of what reaches).
    by: Literal["click", "words"] = "click"


class ArmRequest(BaseModel):
    arm: Literal["auto", "left", "right"]


class TargetRequest(BaseModel):
    # A name for YOLOE (English), a click (u, v) in the head image, or clear.
    prompt: str | None = None
    point: list[float] | None = None
    clear: bool = False


@router.get("/status")
def status() -> dict:
    result = grasp_context.task_status()
    return result


@router.post("/stage")
def stage(request: StageRequest) -> dict:
    """Plain def: runs in a worker thread while the page polls the status."""
    result = grasp_context.run_stage(request.name)
    return result


@router.post("/run_all")
def run_all() -> dict:
    """Every step left, then torque off; returns at once."""
    result = grasp_context.run_rest()
    return result


@router.post("/action")
def action(request: ActionRequest) -> dict:
    """The whole task from the start; returns at once."""
    result = grasp_context.start_task(request.action, request.ignore_left)
    return result


@router.post("/reset")
def reset(request: ResetRequest) -> dict:
    result = grasp_context.reset_task(request.action)
    return result


@router.post("/target")
def target(request: TargetRequest) -> dict:
    """The task pages' object; a click alone is named by the prompt-free YOLOE (blocks ~1 s)."""
    result = grasp_context.choose_target(request.prompt, request.point, request.clear)
    return result


@router.post("/place")
def place(request: PlaceRequest) -> dict:
    """抓取摆放's place: into a box or onto a desk point, and where exactly."""
    result = grasp_context.choose_place(request.kind, request.point, request.by)
    return result


@router.post("/arm")
def arm(request: ArmRequest) -> dict:
    result = grasp_context.choose_arm(request.arm)
    return result


@router.post("/stop")
def stop() -> dict:
    result = grasp_context.stop()
    return result


@router.get("/plan.jpg")
def plan_image() -> FileResponse:
    path = grasp_context.plan_image()
    if path is None:
        raise HTTPException(status_code=404, detail="还没有规划")
    response = FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    return response


@router.get("/align.jpg")
def align_image() -> FileResponse:
    path = grasp_context.align_image()
    if path is None:
        raise HTTPException(status_code=404, detail="还没有对准")
    response = FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    return response


@depth_router.get("/{kind}.jpg")
def depth_preview(kind: str) -> Response:
    """The live head views, kind raw (the sensor's depth) or refined (what planning uses), detect (the head RGB with
    the bottle and the box framed), target or place (the task pages' views); the page fetches it again and again,
    which keeps the preview running."""
    picture = grasp_context.depth_preview_jpeg(kind)
    if picture is None:
        raise HTTPException(status_code=404, detail="深度预览还没有画面")
    headers = {"Cache-Control": "no-store"}
    valid = grasp_context.depth_preview_valid(kind)
    if valid is not None:
        # The caption under the picture: the share of pixels with a depth, and the colour scale (blue near, red far).
        near, far = PerceptionConfig.PREVIEW_RANGE_M
        headers.update({"X-Valid": f"{valid:.3f}", "X-Range": f"{near:.2f}-{far:.2f}"})
    response = Response(content=picture, media_type="image/jpeg", headers=headers)
    return response


def demo_task_api() -> None:
    """Offline, on a stand-in task: the status, a step out of order (422), and a choice while it runs (409)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.errors import register_error_handlers
    from component.task_execution.busy_error import TaskBusyError

    class Real:
        def status(self):
            return {"next": 0}

        def run(self, name):
            raise ValueError("下一步应是 prepare")

        def set_arm(self, side):
            raise TaskBusyError("正在执行，等这一轮结束再说")

    grasp_context.real = Real()
    app = FastAPI()
    app.include_router(router)
    register_error_handlers(app)
    client = TestClient(app)
    logger.info("status {}", client.get("/api/real/status").json())
    stage = client.post("/api/real/stage", json={"name": "lift"})
    logger.info("stage {} {}", stage.status_code, stage.json())
    logger.info("arm {}", client.post("/api/real/arm", json={"arm": "left"}).status_code)


def main() -> None:
    use_utf8_output()
    demo_task_api()


if __name__ == "__main__":
    main()
