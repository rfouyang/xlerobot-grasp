"""转发：浏览器发到 UI 服务的请求原样交给后台（HTTP 流式转发，相机的 MJPEG 也能过；WebSocket 双向转发）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio

import httpx
import websockets
from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from starlette.background import BackgroundTask

from config.settings import RobotServerConfig, use_utf8_output

# Per connection, not end to end: they describe this hop only.
HOP_HEADERS = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "upgrade"}


class ProxyHelper:
    """One backend. No read timeout: a grasp step answers when it is done (up to minutes) and a camera stream never
    ends. A backend that is down (being restarted) answers 503, which the pages show as not connected."""

    def __init__(self, base_url: str, **kwargs) -> None:
        self.base_url = base_url.rstrip("/")
        timeout = httpx.Timeout(None, connect=kwargs.get("connect_timeout", RobotServerConfig.CONNECT_TIMEOUT_S))
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def http(self, request: Request, path: str):
        headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
        outgoing = self.client.build_request(request.method, httpx.URL(path=f"/{path}", query=request.url.query.encode()),
                                             headers=headers, content=await request.body())
        try:
            incoming = await self.client.send(outgoing, stream=True)
        except (httpx.ReadError, httpx.RemoteProtocolError):
            # A kept-alive connection the backend had just closed (seen while the grasp backend was planning): a GET is
            # safe to send again on a fresh one; anything else is not repeated.
            if request.method != "GET":
                response = JSONResponse({"error": f"后台 {self.base_url} 断开了连接"}, status_code=502)
                return response
            incoming = await self.client.send(self.client.build_request(outgoing.method, outgoing.url, headers=outgoing.headers), stream=True)
        except httpx.ConnectError:
            response = JSONResponse({"error": f"后台 {self.base_url} 没有启动"}, status_code=503)
            return response
        response = StreamingResponse(incoming.aiter_raw(), status_code=incoming.status_code,
                                     headers={key: value for key, value in incoming.headers.items() if key.lower() not in HOP_HEADERS},
                                     background=BackgroundTask(incoming.aclose))
        return response

    async def websocket(self, websocket: WebSocket, path: str) -> None:
        """Both directions until either side closes; the backend then sees the page go away (keys released)."""
        await websocket.accept()
        url = self.base_url.replace("http", "ws", 1) + f"/{path}"
        try:
            upstream = await websockets.connect(url)
        except OSError:
            await websocket.close(code=1011)
            return
        async with upstream:
            async def to_backend():
                while True:
                    await upstream.send(await websocket.receive_text())

            async def to_page():
                async for message in upstream:
                    await websocket.send_text(message)

            tasks = [asyncio.create_task(to_backend()), asyncio.create_task(to_page())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            # The side that closed ends with its disconnect; that is how it ends, not an error.
            for task in done:
                task.exception()
        if websocket.client_state.name == "CONNECTED":
            await websocket.close()


def demo_proxy_helper() -> None:
    """Needs the motor backend (8011): its config and one status through the proxy."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    proxy = ProxyHelper(RobotServerConfig.URL)
    app = FastAPI()

    @app.api_route("/robot-api/{path:path}", methods=["GET", "POST"])
    async def forward(request: Request, path: str):
        response = await proxy.http(request, path)
        return response

    client = TestClient(app)
    config = client.get("/robot-api/api/config").json()
    status = client.get("/robot-api/api/status").json()
    logger.info("经转发：机器人 {}，控制循环 {}，力矩 {}", config["robot"], status["loop_alive"], status["torque"])


def main() -> None:
    use_utf8_output()
    demo_proxy_helper()


if __name__ == "__main__":
    main()
