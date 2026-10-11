"""The task pages' chat by the word table (no GPT) on the stand-in desk: it leads to what is still to choose and never
starts before everything is chosen and confirmed."""

from __future__ import annotations

import unittest

from component.task_chat.service import TaskChat
from tests.support import BOXES, FakeDialog, FakeRunner
from util.openai_chat_helper import OpenAIChatHelper
from util.teleop_helper import CameraStaleError

# A page's own move: the click the user makes where the chat asks for one.
BOX_CLICK = "<click in the box>"
SPOT_CLICK = "<click on the desk>"


class ChatTest(unittest.TestCase):
    def play(self, task: str, turns: list[tuple[str, bool, str]], runner: FakeRunner | None = None) -> list[dict]:
        """Each turn (text, go, awaiting): the reply must start (go) or not and wait for awaiting."""
        runner = runner or FakeRunner()
        chat = TaskChat(runner, FakeDialog(), OpenAIChatHelper(api_key=""))
        history, awaiting, results = [], "target", []
        for text, go, expected in turns:
            if text == BOX_CLICK:
                runner.set_place("box", [300, 215])
                result = chat.next(task)
            elif text == SPOT_CLICK:
                runner.set_place("spot", [500, 380])
                result = chat.next(task)
            else:
                result = chat.turn(task, text, history, awaiting)
            self.assertEqual((result["go"], result["awaiting"]), (go, expected), f"{text} → {result['reply']}")
            history += [{"role": "user", "text": text}, {"role": "robot", "text": result["reply"]}]
            awaiting = result["awaiting"]
            results.append(result)
        return results

    # --- 物体抓取 ---

    def test_pick_confirmed_then_started(self) -> None:
        results = self.play("pick", [("你能看见桌上有什么", False, "target"),
                                     ("那我想让你抓右边的瓶子", False, "confirm"),
                                     ("你指定错了 瓶子上的盖子也是目标的一部分", False, "confirm"),
                                     ("好现在指定目标是对的 开始抓吧", True, "confirm")])
        self.assertIn("纸盒", results[0]["reply"])
        self.assertIn("盖子也算进去", results[2]["reply"])

    def test_pick_which_one_asked(self) -> None:
        results = self.play("pick", [("把瓶子抓起来", False, "target"), ("第一个", False, "confirm"),
                                     ("对", True, "confirm")])
        self.assertEqual([option["label"][0] for option in results[0]["options"]], ["①", "②"])
        self.assertEqual(len(results[0]["candidates"]), 2)

    def test_pick_and_start_in_one_sentence(self) -> None:
        self.play("pick", [("抓左边那一个，直接抓", True, "confirm")])

    def test_pick_redo_and_chit_chat(self) -> None:
        results = self.play("pick", [("抓右边的瓶子", False, "confirm"), ("不对", False, "target"),
                                     ("左边那个", False, "confirm"), ("今天天气怎么样", False, "confirm")])
        self.assertIn("没太听明白", results[3]["reply"])

    def test_pick_start_without_object(self) -> None:
        results = self.play("pick", [("开始", False, "target")])
        self.assertIn("还没选要抓的物体", results[0]["reply"])
        self.assertEqual(results[0]["options"], [])

    def test_stop_word_stops_the_robot(self) -> None:
        runner = FakeRunner()
        self.play("pick", [("抓右边的瓶子", False, "confirm"), ("停", False, "confirm")], runner)
        self.assertEqual(runner.teleop.stops, 1)

    def test_busy_runner_takes_nothing(self) -> None:
        runner = FakeRunner()
        runner.auto = True
        results = self.play("pick", [("抓右边的瓶子", False, "confirm")], runner)
        self.assertIn("正在执行", results[0]["reply"])
        self.assertTrue(runner.target["default"])

    def test_camera_without_frames_is_said(self) -> None:
        runner = FakeRunner()
        runner.selector.candidates = lambda prompt: (_ for _ in ()).throw(CameraStaleError(300))
        results = self.play("pick", [("抓右边的瓶子", False, "target")], runner)
        self.assertEqual(results[0]["by"], "camera")
        self.assertIn("头部相机没有画面了", results[0]["reply"])

    # --- 抓取摆放 ---

    def test_place_object_then_where_in_the_box(self) -> None:
        # 放进盒子 picks no box for the user (10-11): where in a box is clicked next.
        results = self.play("place", [("把右边的瓶子抓起来", False, "place"), ("放进盒子", False, "place"),
                                      (BOX_CLICK, False, "confirm"), ("对", True, "confirm")])
        self.assertIn("放到哪里", results[0]["reply"])
        self.assertIn("点盒子里要放的位置", results[1]["reply"])
        self.assertEqual([candidate["kind"] for candidate in results[1]["candidates"]], ["place", "place"])
        self.assertIn("盒子（橙框）", results[2]["reply"])

    def test_place_box_said(self) -> None:
        runner = FakeRunner()
        self.play("place", [("把右边的瓶子放进第二个盒子", False, "confirm"), ("开始", True, "confirm")], runner)
        self.assertEqual(runner.place.box["by"], "words")
        self.assertEqual(runner.place.box["xy"], BOXES[1]["aim"]["xy"])

    def test_place_desk_spot(self) -> None:
        self.play("place", [("抓右边的瓶子，直接开始", False, "place"), ("放到桌面点", False, "place"),
                            (SPOT_CLICK, False, "confirm"), ("好的开始吧", True, "confirm")])

    def test_place_left_bottle_out_of_reach(self) -> None:
        results = self.play("place", [("抓左边那个瓶子", False, "target")])
        self.assertIn("右手够不着", results[0]["reply"])

    def test_place_changed_and_start_without_object(self) -> None:
        self.play("place", [("抓右边的瓶子", False, "place"), ("放进左边的盒子", False, "confirm"),
                            ("换放置位置", False, "place"), ("放到桌面点", False, "place")])
        results = self.play("place", [("开始", False, "target")])
        self.assertIn("还没选要抓的物体", results[0]["reply"])

    def test_place_one_box_and_a_missed_part(self) -> None:
        results = self.play("place", [("把右边的瓶子抓起来", False, "place"), ("放进盒子", False, "place"),
                                      (BOX_CLICK, False, "confirm"),
                                      ("你指定错了 瓶子上的盖子也是目标的一部分", False, "confirm"),
                                      ("对，开始吧", True, "confirm")],
                            FakeRunner(boxes=BOXES[:1]))
        self.assertEqual([option["label"] for option in results[1]["options"]], ["放在盒子中间"])
        self.assertIn("现在框得对吗", results[3]["reply"])

    def test_no_reachable_box(self) -> None:
        results = self.play("place", [("把右边的瓶子抓起来", False, "place"), ("放进盒子", False, "place")],
                            FakeRunner(boxes=[{**BOXES[0], "right_reach": False}]))
        self.assertIn("右手都够不着", results[1]["reply"])


if __name__ == "__main__":
    unittest.main()
