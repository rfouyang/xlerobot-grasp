"""抓取控制台页面。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from loguru import logger

from app.context import grasp_context
from config.settings import use_utf8_output
from config.simulation import SimConfig

UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=UI_DIR / "templates")


def create_ui_grasp() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    def index(request: Request):
        """Everything else arrives through /api and the WebSocket; the page only needs the viewer port."""
        page = templates.TemplateResponse(request, "index.html", {"viewer_port": grasp_context.viewer_port})
        return page

    @router.get("/favicon.ico")
    def favicon():
        """Browsers ask for it on every load; an empty answer keeps a 404 out of the log."""
        response = Response(status_code=204)
        return response

    return router


def mount_ui_static(app: FastAPI) -> None:
    """Routers cannot mount, so the entry calls this next to include_router. /videos serves the
    simulator's recordings, which the execute tab plays."""
    app.mount("/static/ui_grasp", StaticFiles(directory=UI_DIR / "static"), name="ui_grasp_static")
    SimConfig.VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/videos", StaticFiles(directory=SimConfig.VIDEO_DIR), name="videos")


def demo_ui_grasp() -> None:
    app = FastAPI()
    app.include_router(create_ui_grasp())
    mount_ui_static(app)
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200 and 'id="viewer"' in page.text
    assert client.get("/static/ui_grasp/grasp_ui.js").status_code == 200
    assert client.get("/favicon.ico").status_code == 204
    logger.info("页面与脚本可访问")


def main() -> None:
    use_utf8_output()
    demo_ui_grasp()


if __name__ == "__main__":
    main()
