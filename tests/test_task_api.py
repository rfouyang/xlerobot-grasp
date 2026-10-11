"""The task pages' API through FastAPI's test client, on the stand-in desk (no robot, no models, no GPT): the answers,
and the errors as the pages show them (422 cannot be chosen, 409 busy, 503 no head camera picture)."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import task_api, voice_api
from app.api.errors import register_error_handlers
from app.context import grasp_context
from component.task_chat.service import TaskChat
from tests.support import FakeDialog, FakeRunner
from util.openai_chat_helper import OpenAIChatHelper
from util.teleop_helper import CameraStaleError


class TaskApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = FakeRunner()
        grasp_context.real = self.runner
        grasp_context.chat = TaskChat(self.runner, FakeDialog(), OpenAIChatHelper(api_key=""))
        app = FastAPI()
        for router in (task_api.router, voice_api.router):
            app.include_router(router)
        register_error_handlers(app)
        self.client = TestClient(app)

    def test_choose_object_and_place(self) -> None:
        target = self.client.post("/api/real/target", json={"point": [420, 300]})
        self.assertEqual(target.status_code, 200)
        self.assertEqual(target.json()["target"]["point"], [420, 300])
        place = self.client.post("/api/real/place", json={"kind": "box", "point": [300, 215]})
        self.assertEqual(place.json()["place_box"]["by"], "click")
        status = self.client.get("/api/real/status").json()
        self.assertEqual((status["place_kind"], status["target"]["point"]), ("box", [420, 300]))

    def test_refused_choices(self) -> None:
        nothing = self.client.post("/api/real/place", json={"kind": "box", "point": [100, 400]})
        self.assertEqual(nothing.status_code, 422)
        self.assertIn("没看到盒子", nothing.json()["detail"])
        self.assertEqual(self.client.post("/api/real/place", json={"kind": "jar"}).status_code, 422)
        out_of_order = self.client.post("/api/real/stage", json={"name": "lift"})
        self.assertEqual((out_of_order.status_code, out_of_order.json()["detail"]), (422, "下一步应是 prepare"))

    def test_busy(self) -> None:
        self.runner.auto = True
        for url, body in (("/api/real/arm", {"arm": "left"}), ("/api/real/target", {"clear": True}),
                          ("/api/real/action", {"action": "place_in_box"})):
            answer = self.client.post(url, json=body)
            self.assertEqual(answer.status_code, 409, url)
            self.assertIn("正在执行", answer.json()["detail"])

    def test_camera_without_frames(self) -> None:
        self.runner.selector.camera_error = CameraStaleError(400)
        answer = self.client.post("/api/real/place", json={"kind": "spot", "point": [500, 380]})
        self.assertEqual(answer.status_code, 503)
        self.assertIn("头部相机没有画面了", answer.json()["detail"])

    def test_start(self) -> None:
        self.assertEqual(self.client.post("/api/real/action", json={"action": "pick_target"}).json(), {"started": True})
        self.assertEqual(self.runner.started, ["pick_target"])

    def test_chat_turn_and_next(self) -> None:
        turn = self.client.post("/api/task/chat", json={"task": "place", "text": "把右边的瓶子抓起来"}).json()
        self.assertEqual(turn["awaiting"], "place")
        self.client.post("/api/real/place", json={"kind": "spot", "point": [500, 380]})
        after = self.client.post("/api/task/next", json={"task": "place"}).json()
        self.assertEqual(after["awaiting"], "confirm")
        self.assertEqual(self.client.post("/api/task/next", json={"task": "sort"}).status_code, 422)

    def test_speaker(self) -> None:
        self.client.post("/api/voice/speaker", json={"client": "page-a"})
        self.assertEqual(self.client.get("/api/voice/speaker").json(), {"client": "page-a"})

    def test_short_recording(self) -> None:
        answer = self.client.post("/api/voice/transcribe", content=b"\0" * 100)
        self.assertEqual((answer.status_code, answer.json()["detail"]), (422, "录音太短"))


if __name__ == "__main__":
    unittest.main()
