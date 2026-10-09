"""抓取执行接口：规划并执行最佳可达抓取，WebSocket 推送进度。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from loguru import logger

from app.context import grasp_context
from config.settings import ServerConfig, use_utf8_output


def create_grasp_execution_api() -> APIRouter:
    router = APIRouter()

    @router.post("/api/grasps/execute")
    def execute():
        """Plain def: runs in a worker thread while the WebSocket below streams the steps."""
        response = grasp_context.execute_grasp()
        return response

    @router.websocket("/ws/execution")
    async def execution_stream(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(grasp_context.execution_status())
                await asyncio.sleep(ServerConfig.STATE_PUSH_INTERVAL_S * 2)
        except WebSocketDisconnect:
            # The page was closed or reloaded; nothing to clean up.
            pass

    return router


def demo_grasp_execution_api() -> None:
    """Needs the bringup launch; starts the application in-process and grasps the water bottle."""
    grasp_context.start()
    grasp_context.generate_grasps("water_bottle")
    app = FastAPI()
    app.include_router(create_grasp_execution_api())
    client = TestClient(app)
    result = client.post("/api/grasps/execute").json()
    logger.info("执行：{}，抓取 #{}\n{}", result["done"], result["index"], "\n".join(result["progress"]))
    with client.websocket_connect("/ws/execution") as websocket:
        logger.info("WebSocket：{}", websocket.receive_json()["running"])


def main() -> None:
    use_utf8_output()
    demo_grasp_execution_api()


if __name__ == "__main__":
    main()
