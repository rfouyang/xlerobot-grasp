"""六个页面：键盘控制（/）、物体抓取（/grasp）、抓取摆放（/pick-place）、抓取规划、相机标定、系统设置，共用顶栏和侧边栏。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import RedirectResponse
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
        """物体抓取: pick it up, put it back (the interactive grasp's task)."""
        page = templates.TemplateResponse(request, "task.html", {
            "page": "grasp", "action": "pick_target", "place": False, "title": "物体抓取",
            "subtitle": "抓起来，再放回原处。在头部画面中点选，或输入名称；离哪边肩膀近用哪只手。"})
        return page

    @router.get("/pick-place")
    def pick_place(request: Request):
        """抓取摆放: pick it up, put it in the box."""
        page = templates.TemplateResponse(request, "task.html", {
            "page": "pick_place", "action": "place_in_box", "place": True, "title": "抓取摆放",
            "subtitle": "抓起来，放进盒子，或放到在画面里点的桌面位置。先选物体，再选放置位置。"})
        return page

    @router.get("/interactive-grasp")
    def interactive_grasp():
        """The old interactive grasp page is 物体抓取 now."""
        response = RedirectResponse("/grasp")
        return response

    @router.get("/planning")
    def planning(request: Request):
        """The research page: the last plan's candidates, their re-ranking, the algorithm for the next plan."""
        page = templates.TemplateResponse(request, "planning.html", {"page": "planning"})
        return page

    @router.get("/calibration")
    def calibration(request: Request):
        """The board seen live by the three cameras, the current results, and the depth comparison."""
        page = templates.TemplateResponse(request, "calibration.html", {"page": "calibration"})
        return page

    @router.get("/settings")
    def settings(request: Request):
        """The self-check (read-only) and the robot process restart."""
        page = templates.TemplateResponse(request, "settings.html", {"page": "settings"})
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
    pages = {path: client.get(path) for path in ("/", "/grasp", "/pick-place", "/planning", "/calibration", "/settings")}
    assert all(page.status_code == 200 for page in pages.values()), {path: page.status_code for path, page in pages.items()}
    assert 'id="keymap"' in pages["/"].text and 'id="steps"' in pages["/grasp"].text and "place" in pages["/pick-place"].text
    assert client.get("/interactive-grasp", follow_redirects=False).status_code in (302, 307)
    for name in ("common.js", "keyboard.js", "task.js"):
        assert client.get(f"/static/ui/{name}").status_code == 200
    logger.info("六个页面与脚本可访问")


def main() -> None:
    use_utf8_output()
    demo_ui()


if __name__ == "__main__":
    main()
