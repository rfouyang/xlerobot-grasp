"""标定会话（标定页）：手臂由人摆好（键盘控制，或关力矩用手摆），让腕部相机看到桌上的 ChArUco 板，「采集当前有效帧」
把腕部图像、头部 RGB-D（含红外对）和全部关节读数存进 data/calibration/<手>_board_<时间>/，格式和 2026-10-09 左臂那次
一样；「计算标定」在后台跑 solve.py；「保存为新版本」写到 config/calibration/（不会自动启用）。这里不动机器人。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import threading
import time

import cv2
import numpy as np
from loguru import logger

from component.calibration.solve import WristSolve
from component.planning.arm import ARMS
from config.charuco import CharucoConfig
from config.settings import use_utf8_output
from util.charuco_helper import CharucoHelper
from util.teleop_helper import TeleopHelper


class CalibrationSession:
    def __init__(self, teleop: TeleopHelper | None = None) -> None:
        self.teleop = teleop or TeleopHelper()
        self.helper = CharucoHelper()
        self.lock = threading.Lock()
        self.directory: Path | None = None
        self.side = "left"
        self.poses: list[dict] = []
        self.solving = False
        self.result: dict | None = None
        self.error = ""
        self.saved: str | None = None

    def start(self, side: str) -> dict:
        with self.lock:
            if self.solving:
                return {"ok": False, "error": "正在计算，等算完再开新会话"}
            self.side = side
            self.directory = CharucoConfig.SESSION_DIR / f"{side}_board_{time.strftime('%Y%m%d_%H%M%S')}"
            self.directory.mkdir(parents=True, exist_ok=True)
            self.poses, self.result, self.error, self.saved = [], None, "", None
        logger.info("标定会话：{}（{}）", self.directory.name, ARMS[side].label)
        return {"ok": True, **self.status()}

    def capture(self) -> dict:
        """The wrist camera of the session's arm, the head RGB-D and every joint, as they are now."""
        if self.directory is None:
            return {"ok": False, "error": "先开一个标定会话（选左腕或右腕）"}
        if self.solving:
            return {"ok": False, "error": "正在计算"}
        wrist = self.teleop.camera(f"{self.side}_wrist")
        head = self.teleop.rgbd(infrared=True)
        status = self.teleop.status()
        joints = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in status["joints"].items()}
        wrist_view, head_view = self.helper.detect(wrist), self.helper.detect(head["color"])
        with self.lock:
            index = len(self.poses)
            cv2.imwrite(str(self.directory / f"pose_{index:02d}_wrist.png"), cv2.cvtColor(wrist, cv2.COLOR_RGB2BGR))
            np.savez_compressed(self.directory / f"pose_{index:02d}_head.npz", **head)
            (self.directory / f"pose_{index:02d}_joints.json").write_text(json.dumps({"measured_real": joints}, indent=1))
            pose = {"i": index, "wrist_corners": wrist_view.count, "head_corners": head_view.count,
                    "usable": wrist_view.count >= CharucoConfig.MIN_CORNERS, "arm": {k: round(v, 1) for k, v in joints[ARMS[self.side].part].items()}}
            self.poses.append(pose)
        logger.info("标定采样 {:02d}：腕部 {} 角点，头部 {} 角点", index, wrist_view.count, head_view.count)
        return {"ok": True, "pose": pose, **self.status()}

    def solve(self) -> dict:
        usable = sum(pose["usable"] for pose in self.poses)
        if self.directory is None or usable < 6:
            return {"ok": False, "error": f"有效帧 {usable} 个，至少要 6 个（不同角度看板子）"}
        if self.solving:
            return {"ok": False, "error": "正在计算"}
        self.solving, self.result, self.error = True, None, ""
        threading.Thread(target=self.solve_in_background, name="calibration_solve", daemon=True).start()
        return {"ok": True, **self.status()}

    def solve_in_background(self) -> None:
        try:
            result = WristSolve(self.directory, ARMS[self.side]).run()
            (self.directory / "result.json").write_text(json.dumps(result, indent=1))
            self.result = result
            logger.info("标定计算完成：内参 {:.3f} px，关节偏置 {}", result["rms_px"], result["joint_offset_deg"])
        except Exception as error:
            self.error = str(error)
            logger.warning("标定计算失败：{}", error)
        finally:
            self.solving = False

    def save(self) -> dict:
        """The result as a new dated file in config/calibration (a draft: the robot keeps the one it uses until it is
        switched over by hand, after a check)."""
        if self.result is None:
            return {"ok": False, "error": "还没有计算结果"}
        name = f"{self.side}_wrist_{time.strftime('%Y%m%d_%H%M')}.json"
        path = CharucoConfig.RESULT_DIR / name
        path.write_text(json.dumps({**self.result, "date": time.strftime("%Y-%m-%d"), "status": "draft",
                                    "method": f"标定页：{len(self.result['views'])} 个腕部视角看桌面 ChArUco 板，头部 PnP 定板子，"
                                              "求腕部内参、手眼和关节偏置（component/calibration/solve.py）"}, indent=1))
        self.saved = name
        logger.info("标定结果已存为 {}（未启用）", name)
        return {"ok": True, "file": name, **self.status()}

    def status(self) -> dict:
        status = {"session": self.directory.name if self.directory else None, "arm": self.side, "poses": self.poses,
                  "usable": sum(pose["usable"] for pose in self.poses), "solving": self.solving, "error": self.error,
                  "saved": self.saved, "result": self.result and {key: self.result[key] for key in
                  ("rms_px", "joint_offset_deg", "loo_max_deg", "reprojection_px", "board_spread_mm", "correction_deg", "base_correction_deg", "views")}}
        if self.result:
            status["result"]["lens_mm"] = (np.array(self.result["fixed_jaw_T_camera_optical"])[:3, 3] * 1000).round(1).tolist()
        return status


def demo_calibration_session() -> None:
    """Needs the robot process: one capture of the current view (nothing moves); the session folder stays."""
    session = CalibrationSession()
    session.start("left")
    logger.info("{}", session.capture())


def main() -> None:
    use_utf8_output()
    demo_calibration_session()


if __name__ == "__main__":
    main()
