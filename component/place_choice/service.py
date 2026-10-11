"""抓取摆放的「放到哪里」（任务步骤 ②）：放进盒子还是放到桌面点，以及盒子里 / 桌面上的哪个位置。点选的放在点的地方；
说出来的（对话、选项）放在盒子里右手够得到那部分的中间。选不成时抛 ValueError（说为什么）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable

import numpy as np
from loguru import logger

from config.settings import use_utf8_output

# Planning puts the bottle elsewhere than shown when its nearest place point is further than this (m): the + moves.
MOVED_M = 0.03


def rounded(values, digits: int = 0) -> list:
    """Pixels to whole numbers (digits 0), metres to digits places."""
    result = [round(float(value), digits) if digits else round(float(value)) for value in values]
    return result


class PlaceChoice:
    """kind: "box", "spot" or None (not chosen). box: in the box, {"uv" (the orange + in the head image), "by"
    ("click" / "words"), "xy" (where, m; None unknown), "frame_uv" (the box's own point, to find it by at planning)}.
    spot: on the desk, {"uv", "xy"}. The selector (target_selection) turns clicks into desk points and finds boxes."""

    def __init__(self, selector, report: Callable[[str], None]) -> None:
        self.selector = selector
        self.report = report
        self.kind: str | None = None
        self.box: dict | None = None
        self.spot: dict | None = None

    @property
    def chosen(self) -> bool:
        chosen = (self.kind == "box" and self.box is not None) or (self.kind == "spot" and self.spot is not None)
        return chosen

    def status(self) -> dict:
        status = {"place_kind": self.kind, "place_box": self.box, "place_spot": self.spot}
        return status

    def choose(self, kind: str | None) -> dict:
        """Into a box or onto a desk point, where exactly clicked next; None: not chosen.
        What was chosen before goes."""
        self.kind, self.box, self.spot = kind, None, None
        if kind:
            self.report(f"放置：{'盒子（等点选盒子里的位置）' if kind == 'box' else '桌面点（等点选位置）'}")
        return self.status()

    def click_spot(self, uv: list[float]) -> dict:
        """A click on the desk: the bottle goes there."""
        xy = self.selector.desk_point(uv)
        if xy is None:
            raise ValueError("点的位置不在桌面上")
        self.kind, self.box, self.spot = "spot", None, {"uv": rounded(uv), "xy": rounded(xy, 3)}
        self.report(f"放置点：桌面 x {xy[0]:.3f} m，y {xy[1]:+.3f} m")
        return self.status()

    def click_box(self, uv: list[float]) -> dict:
        """A click in a box the right arm reaches: the bottle goes where it was clicked."""
        box = self.selector.box_at(uv)
        xy = self.selector.desk_point(uv)
        self.kind, self.spot = "box", None
        self.box = {"uv": rounded(uv), "by": "click", "xy": None if xy is None else rounded(xy, 3),
                    "frame_uv": box["point"]}
        self.report(f"放置：盒子，画面 {tuple(self.box['uv'])}")
        return self.status()

    def box_by_words(self, box: dict) -> dict:
        """A box said or tapped (selector.boxes()'s item): the middle of its part the right arm reaches."""
        aim = box["aim"]
        self.kind, self.spot = "box", None
        self.box = {"uv": aim["uv"] if aim else box["point"], "by": "words", "xy": aim["xy"] if aim else None,
                    "frame_uv": box["point"]}
        self.report(f"放置：盒子中间，画面 {tuple(self.box['uv'])}")
        return self.status()

    def box_by_words_at(self, uv: list[float]) -> dict:
        """A tapped answer naming a box by its point: that box, by words."""
        box = self.selector.box_at(uv)
        return self.box_by_words(box)

    def toward(self) -> np.ndarray | None:
        """Where in the box planning aims (as clicked); None: the middle of its reachable part."""
        aim = np.array(self.box["xy"]) if self.box and self.box["by"] == "click" and self.box["xy"] else None
        return aim

    def planned(self, point: np.ndarray, uv: list[int]) -> None:
        """Planning put the bottle at point (head image uv): the + follows when that is not where it was shown."""
        shown = self.box["xy"]
        gap = None if shown is None else float(np.linalg.norm(np.asarray(shown) - point))
        if gap is not None and gap <= MOVED_M:
            return
        if gap is not None and self.box["by"] == "click":
            self.report(f"点的位置放不了（离盒壁太近或够不着），改放在离它 {gap * 100:.0f} cm 的地方（橙色＋）")
        self.box = {**self.box, "uv": uv, "xy": rounded(point, 3)}


def demo_place_choice() -> None:
    """Offline, with a stand-in selector: a desk click, a box click, a box by words."""
    class Selector:
        def desk_point(self, uv):
            return 0.3, -0.1

        def box_at(self, uv):
            return {"point": [350, 300], "box": [280, 150, 460, 440], "aim": {"xy": [0.29, 0.0], "uv": [350, 320]}}

    choice = PlaceChoice(Selector(), logger.info)
    logger.info("{}", choice.click_spot([500, 380]))
    logger.info("{}", choice.click_box([340, 250]))
    logger.info("{} toward {}", choice.box_by_words(Selector().box_at(None)), choice.toward())


def main() -> None:
    use_utf8_output()
    demo_place_choice()


if __name__ == "__main__":
    main()
