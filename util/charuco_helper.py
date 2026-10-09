"""ChArUco 板检测：角点、它们的编号，画到图上；有内参时再解板子的位姿（PnP）和重投影误差。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from config.charuco import CharucoConfig
from config.settings import use_utf8_output


@dataclass
class BoardView:
    # (N, 2) image pixels and their ids; empty when the board is not seen.
    corners: np.ndarray
    ids: np.ndarray
    # Board -> camera (4 x 4) and the mean reprojection error (px), when K was given and enough corners were seen.
    pose: np.ndarray | None = None
    error_px: float | None = None

    @property
    def count(self) -> int:
        return len(self.ids)


class CharucoHelper:
    def __init__(self) -> None:
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, CharucoConfig.DICTIONARY))
        self.board = cv2.aruco.CharucoBoard(CharucoConfig.SQUARES, CharucoConfig.SQUARE_M, CharucoConfig.MARKER_M, dictionary)
        self.detector = cv2.aruco.CharucoDetector(self.board)
        self.total = (CharucoConfig.SQUARES[0] - 1) * (CharucoConfig.SQUARES[1] - 1)

    def detect(self, rgb: np.ndarray, K: np.ndarray | None = None, dist: np.ndarray | None = None) -> BoardView:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        corners, ids, _, _ = self.detector.detectBoard(gray)
        if ids is None or len(ids) == 0:
            view = BoardView(np.zeros((0, 2)), np.zeros(0, int))
            return view
        view = BoardView(corners.reshape(-1, 2), ids.ravel())
        if K is not None and view.count >= CharucoConfig.MIN_CORNERS:
            obj, img = self.board.matchImagePoints(corners, ids)
            ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist)
            if ok:
                projected = cv2.projectPoints(obj, rvec, tvec, K, dist)[0].reshape(-1, 2)
                view.error_px = float(np.linalg.norm(projected - img.reshape(-1, 2), axis=1).mean())
                view.pose = np.eye(4)
                view.pose[:3, :3], view.pose[:3, 3] = cv2.Rodrigues(rvec)[0], tvec.ravel()
        return view

    def draw(self, rgb: np.ndarray, view: BoardView) -> np.ndarray:
        """BGR: the corners in green (red when too few to use), and the count in the corner."""
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        enough = view.count >= CharucoConfig.MIN_CORNERS
        colour = (0, 200, 0) if enough else (0, 0, 255)
        for x, y in view.corners:
            cv2.circle(image, (int(round(x)), int(round(y))), 4, colour, -1)
        text = f"{view.count}/{self.total} corners" + (f"  {view.error_px:.2f} px" if view.error_px is not None else "")
        cv2.putText(image, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(image, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
        return image


def demo_charuco_helper() -> None:
    """Offline: a rendered board, detected back."""
    helper = CharucoHelper()
    picture = helper.board.generateImage((700, 1000), marginSize=40)
    rgb = cv2.cvtColor(picture, cv2.COLOR_GRAY2RGB)
    view = helper.detect(rgb, K=np.array([[800.0, 0, 350], [0, 800.0, 500], [0, 0, 1]]))
    logger.info("角点 {}/{}，重投影 {:.3f} px", view.count, helper.total, view.error_px or float("nan"))


def main() -> None:
    use_utf8_output()
    demo_charuco_helper()


if __name__ == "__main__":
    main()
