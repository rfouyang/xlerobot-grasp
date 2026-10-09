"""四个页面：键盘控制（/）、抓取（/grasp）、抓取放置（/pick-place）、交互抓取（/interactive-grasp），共用顶栏和侧边栏。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from loguru import logger

from config.settings import use_utf8_output

UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=UI_DIR / "templates")
# A page that sees this change reloads: the UI service was restarted, maybe with new pages.
STARTED_AT = time.time()


def asset_version(name: str) -> int:
    """A static file's modification time, put in its URL: a changed script is fetched again instead of the browser
    keeping the old one from its cache (a new grasp page with the old grasp.js showed no cameras, 2026-10-06)."""
    version = int((UI_DIR / "static" / name).stat().st_mtime)
    return version


templates.env.globals["asset_version"] = asset_version


def create_ui() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    def keyboard(request: Request):
        """Shells: everything on them comes from the backends through the proxy."""
        page = templates.TemplateResponse(request, "keyboard.html", {"page": "keyboard"})
        return page

    @router.get("/grasp")
    def grasp(request: Request):
        page = templates.TemplateResponse(request, "grasp.html", {"page": "grasp"})
        return page

    @router.get("/interactive-grasp")
    def interactive_grasp(request: Request):
        """Pick what to grasp: a click on the head image, or its name."""
        page = templates.TemplateResponse(request, "interactive_grasp.html", {"page": "interactive_grasp"})
        return page

    @router.get("/pick-place")
    def pick_place(request: Request):
        """The grasp page's layout with its own task: the bottle into the open box on the desk."""
        page = templates.TemplateResponse(request, "pick_place.html", {"page": "pick_place"})
        return page

    @router.get("/ui-api/info")
    def info():
        response = {"started_at": STARTED_AT}
        return response

    @router.get("/favicon.ico")
    def favicon():
        """Browsers ask for it on every load; an empty answer keeps a 404 out of the log."""
        response = Response(status_code=204)
        return response

    return router


def mount_ui_static(app: FastAPI) -> None:
    """Routers cannot mount, so the entry calls this next to include_router."""
    app.mount("/static/ui", StaticFiles(directory=UI_DIR / "static"), name="ui_static")


def demo_ui() -> None:
    app = FastAPI()
    app.include_router(create_ui())
    mount_ui_static(app)
    client = TestClient(app)
    keyboard, grasp = client.get("/"), client.get("/grasp")
    assert keyboard.status_code == 200 and 'id="keymap"' in keyboard.text
    assert grasp.status_code == 200 and 'id="steps"' in grasp.text
    for name in ("common.js", "keyboard.js", "grasp.js"):
        assert client.get(f"/static/ui/{name}").status_code == 200
    logger.info("两个页面与脚本可访问")


def main() -> None:
    use_utf8_output()
    demo_ui()


if __name__ == "__main__":
    main()
