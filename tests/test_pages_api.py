"""The page process's app as app/main.py builds it (create_app), without starting anything: every page and script
answers, and the other pages' API (抓取规划, 相机标定, 系统设置) answers or refuses with the server's own words."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.context import grasp_context
from app.main import create_app
from tests.support import FakeRunner

BASE_DIR = Path(__file__).resolve().parents[1]
PAGES = ("/", "/grasp", "/pick-place", "/planning", "/calibration", "/settings")
SCRIPTS = ("common.js", "keyboard.js", "task.js", "task_chat.js", "task_voice.js", "planning.js", "calibration.js",
           "settings.js")


class FakeSupervisor:
    def __init__(self, restarts: bool) -> None:
        self.restarts = restarts

    def restart_robot(self) -> bool:
        return self.restarts


class PagesApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def setUp(self) -> None:
        grasp_context.real = None
        grasp_context.calibration = None

    def test_pages_and_scripts(self) -> None:
        for path in PAGES:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        for name in SCRIPTS:
            self.assertEqual(self.client.get(f"/static/ui/{name}").status_code, 200, name)
        # The task pages load their three scripts, task.js first.
        page = self.client.get("/pick-place").text
        self.assertLess(page.index("task.js"), page.index("task_chat.js"))
        self.assertLess(page.index("task_chat.js"), page.index("task_voice.js"))

    def test_planning_before_start(self) -> None:
        self.assertEqual(self.client.get("/api/planning/report").json(), {"ready": False})
        self.assertEqual(self.client.get("/api/planning/records").json(), {"recent": [], "summary": {}})

    def test_task_api_under_the_grasp_prefix(self) -> None:
        grasp_context.real = FakeRunner()
        self.assertEqual(self.client.get("/grasp-api/api/real/status").status_code, 200)
        busy = FakeRunner()
        busy.auto = True
        grasp_context.real = busy
        answer = self.client.post("/grasp-api/api/real/arm", json={"arm": "left"})
        self.assertEqual(answer.status_code, 409)

    def test_calibration_capture_without_session(self) -> None:
        answer = self.client.post("/api/calibration/capture")
        self.assertEqual((answer.status_code, answer.json()["detail"]), (422, "先开一个标定会话（选左腕或右腕）"))
        self.assertIn("board", self.client.get("/api/calibration/results").json())

    def test_restart_robot(self) -> None:
        grasp_context.supervisor = FakeSupervisor(restarts=True)
        self.assertEqual(self.client.post("/api/system/restart_robot").json(), {"restarted": True})
        grasp_context.supervisor = FakeSupervisor(restarts=False)
        # An unexpected error is answered with its words too (the test client would re-raise it otherwise).
        failed = TestClient(create_app(), raise_server_exceptions=False).post("/api/system/restart_robot")
        self.assertEqual(failed.status_code, 500)
        self.assertIn("没有重启起来", failed.json()["detail"])

    def test_devices(self) -> None:
        devices = self.client.get("/api/system/devices").json()
        self.assertIn("mapping", devices)


class LayoutTest(unittest.TestCase):
    """The framework's rules (AGENTS.md) that can be read off the source."""

    def files(self, *folders: str) -> list[Path]:
        found = [path for folder in folders for path in (BASE_DIR / folder).rglob("*.py")
                 if "__pycache__" not in path.parts]
        return found

    def test_util_never_imports_component(self) -> None:
        for path in self.files("util"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith(("component", "app")), f"{path.name}: {node.module}")

    def test_component_never_imports_app(self) -> None:
        for path in self.files("component"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith("app"), f"{path.relative_to(BASE_DIR)}: {node.module}")

    def test_routes_are_module_routers(self) -> None:
        for path in self.files("app/api", "app/robot_api", "app/ui"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            factories = [node.name for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name.startswith("create_")]
            self.assertEqual(factories, [], path.name)

    def test_no_ok_false_answers(self) -> None:
        """Errors are raised (app/api/errors.py turns them into answers), not returned as {"ok": False}. The chat's
        tool results are what GPT reads (ok and what to say), not answers to a page."""
        for path in self.files("app", "component"):
            if "task_chat" in path.parts:
                continue
            self.assertNotIn('{"ok": False', path.read_text(encoding="utf-8"), path.relative_to(BASE_DIR))

    def test_every_module_has_a_demo(self) -> None:
        for path in self.files("app", "component", "util"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
            if path.name in ("__init__.py", "context.py", "robot_context.py"):
                continue
            self.assertIn("main", names, path.relative_to(BASE_DIR))
            self.assertTrue(any(name.startswith("demo_") for name in names), path.relative_to(BASE_DIR))


if __name__ == "__main__":
    unittest.main()
