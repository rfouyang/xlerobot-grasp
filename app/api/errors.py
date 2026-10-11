"""把 component 抛出的错误统一变成页面能直接显示的 HTTP 回答：{"detail": 原话}。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from component.task_execution.busy_error import TaskBusyError
from config.settings import use_utf8_output
from util.teleop_helper import CameraStaleError

# 422: it cannot be chosen or done (said why); 404: not there (yet); 409: a step or a run is going; 503: the head
# camera gives no frames.
STATUS_BY_ERROR = {ValueError: 422, KeyError: 422, FileNotFoundError: 404, TaskBusyError: 409, CameraStaleError: 503}


def register_error_handlers(app: FastAPI) -> None:
    for error_type, status in STATUS_BY_ERROR.items():
        app.add_exception_handler(error_type, handler(status))
    # Anything unexpected still reaches the page with its message, not just "500".
    app.add_exception_handler(Exception, handler(500))


def handler(status: int):
    async def handle(request: Request, error: Exception) -> JSONResponse:
        detail = str(error).strip("'") or type(error).__name__
        logger.warning("{} {} -> {}: {}", request.method, request.url.path, status, detail)
        response = JSONResponse({"detail": detail}, status_code=status)
        return response

    return handle


def demo_errors() -> None:
    from fastapi.testclient import TestClient

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/busy")
    def busy():
        raise TaskBusyError("正在执行，等这一轮结束再说")

    response = TestClient(app).get("/busy")
    logger.info("{} {}", response.status_code, response.json())


def main() -> None:
    use_utf8_output()
    demo_errors()


if __name__ == "__main__":
    main()
