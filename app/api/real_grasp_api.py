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
    action: Literal["grasp", "move_right", "place_in_box", "place_at_spot", "pick_target"]
    ignore_left: bool = False


class ResetRequest(BaseModel):
    # The page's task: the grasp page resets to "grasp", the pick-place page to "place_in_box".
    action: Literal["grasp", "move_right", "place_in_box", "place_at_spot", "pick_target"] = "pick_target"


class PlaceSpotRequest(BaseModel):
    # A click (u, v) on the desk in the head image; None clears the spot.
    point: list[float] | None = None


class ArmRequest(BaseModel):
    arm: Literal["auto", "left", "right"]


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

    @router.post("/api/real/algorithms")
    def algorithms(request: dict):
        real = grasp_context.real
        if not real.gpu_lock.acquire(blocking=False):
            raise HTTPException(409, "GPU 正在使用，稍后设置算法")
        try:
            if real.running or real.auto or real.next > 1:
                raise HTTPException(409, "请在规划之前设置算法")
            try:
                real.planning.preferences.configure(request)
            except (ValueError, KeyError) as error:
                raise HTTPException(422, str(error)) from error
            return {"ok": True, "options": real.planning.preferences.options}
        finally:
            real.gpu_lock.release()

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
        response = grasp_context.real_reset(request.action if request else "pick_target")
        return response

    @router.post("/api/real/place_spot")
    def place_spot(request: PlaceSpotRequest):
        """抓取摆放's desk point (the 桌面点 placement)."""
        response = grasp_context.real_place_spot(request.point)
        return response

    @router.post("/api/real/arm")
    def arm(request: ArmRequest):
        """The interactive grasp page's arm choice."""
        response = grasp_context.real_set_arm(request.arm)
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

    @router.get("/api/real/semantic.jpg")
    def semantic_image():
        import cv2
        import numpy as np
        preferences = grasp_context.real.planning.preferences
        report = preferences.report
        if not report.get("observation_image") or not preferences.directory:
            raise HTTPException(404, "尚未生成语义建议")
        with np.load(Path(preferences.directory.name)/"frame.npz", allow_pickle=False) as archive:
            image = cv2.cvtColor(archive["color"], cv2.COLOR_RGB2BGR)
        pixel = report.get("prior", {}).get("pixel")
        if pixel:
            centre = tuple(np.round(pixel).astype(int))
            cv2.drawMarker(image, centre, (255,0,255), cv2.MARKER_CROSS, 28, 3)
            cv2.circle(image, centre, 20, (255,0,255), 2)
        for rank, row in enumerate(report.get("ranked", [])[:5]):
            centre = tuple(np.round(row["pixel"]).astype(int))
            cv2.circle(image, centre, 6, (0,255,255), 2)
            cv2.putText(image, str(rank+1), (centre[0]+8,centre[1]), cv2.FONT_HERSHEY_SIMPLEX,.45,(0,255,255),1)
        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            raise HTTPException(500, "语义图编码失败")
        return Response(encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control":"no-store"})

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
