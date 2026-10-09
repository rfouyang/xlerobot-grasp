"""抓取生成接口：物体列表、生成抓取、选中一个抓取。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import BaseModel

from app.context import grasp_context
from config.settings import use_utf8_output


class GenerateRequest(BaseModel):
    object: str


class SelectRequest(BaseModel):
    index: int


def create_grasp_generation_api() -> APIRouter:
    router = APIRouter()

    @router.get("/api/objects")
    def objects():
        response = grasp_context.objects()
        return response

    @router.post("/api/grasps")
    def generate(request: GenerateRequest):
        response = grasp_context.generate_grasps(request.object)
        return response

    @router.post("/api/grasps/select")
    def select(request: SelectRequest):
        response = grasp_context.select_grasp(request.index)
        return response

    return router


def demo_grasp_generation_api() -> None:
    """Needs the bringup launch; starts the application (and its 3D view) in-process."""
    grasp_context.start()
    app = FastAPI()
    app.include_router(create_grasp_generation_api())
    client = TestClient(app)
    logger.info("/api/objects：{}", client.get("/api/objects").json())
    result = client.post("/api/grasps", json={"object": "coke_can"}).json()
    logger.info("{}：{} 个抓取，用时 {}s", result["object"], len(result["grasps"]), result["seconds"])
    logger.info("选中 0：{}", client.post("/api/grasps/select", json={"index": 0}).json())


def main() -> None:
    use_utf8_output()
    demo_grasp_generation_api()


if __name__ == "__main__":
    main()
