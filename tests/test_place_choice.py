"""抓取摆放's place: a desk click, a click in a box (placed there), a box by words (the middle of what reaches), and
the + following the plan."""

from __future__ import annotations

import unittest

import numpy as np

from component.place_choice.service import PlaceChoice
from tests.support import BOXES, FakeSelector


class PlaceChoiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reports: list[str] = []
        self.choice = PlaceChoice(FakeSelector(), self.reports.append)

    def test_kind_alone_waits_for_the_click(self) -> None:
        status = self.choice.choose("box")
        self.assertEqual((status["place_kind"], status["place_box"]), ("box", None))
        self.assertFalse(self.choice.chosen)

    def test_desk_click(self) -> None:
        status = self.choice.click_spot([500, 380])
        self.assertEqual(status["place_kind"], "spot")
        self.assertEqual(status["place_spot"]["uv"], [500, 380])
        self.assertTrue(self.choice.chosen)
        self.assertIsNone(self.choice.toward())
        with self.assertRaisesRegex(ValueError, "不在桌面上"):
            self.choice.click_spot([500, 50])

    def test_click_in_a_box_goes_there(self) -> None:
        status = self.choice.click_box([300, 215])
        box = status["place_box"]
        self.assertEqual((box["by"], box["uv"], box["frame_uv"]), ("click", [300, 215], BOXES[0]["point"]))
        np.testing.assert_allclose(self.choice.toward(), box["xy"])
        with self.assertRaisesRegex(ValueError, "没看到盒子"):
            self.choice.click_box([100, 400])

    def test_box_by_words_takes_the_reachable_middle(self) -> None:
        status = self.choice.box_by_words(BOXES[1])
        self.assertEqual((status["place_box"]["by"], status["place_box"]["uv"]), ("words", BOXES[1]["aim"]["uv"]))
        self.assertIsNone(self.choice.toward())
        self.assertEqual(self.choice.box_by_words_at([560, 260])["place_box"]["frame_uv"], BOXES[1]["point"])

    def test_plus_follows_the_plan(self) -> None:
        self.choice.click_box([300, 215])
        shown = list(self.choice.box["xy"])
        self.choice.planned(np.array(shown) + 0.01, [301, 216])
        self.assertEqual(self.choice.box["uv"], [300, 215])
        self.choice.planned(np.array(shown) + 0.05, [320, 230])
        self.assertEqual(self.choice.box["uv"], [320, 230])
        self.assertTrue(any("改放在" in line for line in self.reports))

    def test_new_kind_drops_the_old_place(self) -> None:
        self.choice.click_spot([500, 380])
        self.choice.choose(None)
        self.assertEqual(self.choice.status(), {"place_kind": None, "place_box": None, "place_spot": None})


if __name__ == "__main__":
    unittest.main()
