"""遥操作应用门面：页面和 API 只调这里。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import time

import numpy as np
from loguru import logger

from component.robot.camera_stream import CameraStream
from component.robot.keyboard.service import KeyboardTeleop
from component.robot.link import RobotLink
from config.robot import CameraStreamConfig, RestPoseConfig, RobotConfig, TeleopConfig
from config.settings import use_utf8_output


class RobotApplication:
    """Connecting gives no torque and moves nothing. Every session starts and ends at the rest pose (set by hand by
    the user; the zero pose is only for calibration): an arm or the head gets torque only near it, and never loses
    torque away from it: it walks back first, or the arms fall onto the table. That holds for the torque switches
    and for the service stop."""

    def __init__(self) -> None:
        self.link: RobotLink | None = None
        self.teleop: KeyboardTeleop | None = None
        self.cameras: CameraStream | None = None
        # part -> joint -> position
        self.rest_pose: dict[str, dict[str, float]] = json.loads(RestPoseConfig.FILE.read_text())["pose"]
        # Where torque may also go on and off (the grasp start pose), part -> joint -> position each.
        self.other_docks: list[dict[str, dict[str, float]]] = [
            json.loads((RestPoseConfig.FILE.parent / f"{name}_pose.json").read_text())["pose"] for name in RestPoseConfig.OTHER_DOCKS]
        # Tells an open page that the service restarted, so it reloads instead of running stale code and state.
        self.started_at = time.time()
        # What a detector (the grasp service) found in the head image, drawn on the page's head view.
        self.overlay: dict = {"time": 0.0, "source": "", "items": []}

    def start(self) -> None:
        self.link = RobotLink()
        self.link.connect()
        self.teleop = KeyboardTeleop(self.link)
        self.teleop.start()
        self.cameras = CameraStream(self.link.robot.cameras)
        self.cameras.start()

    def shutdown(self) -> None:
        """Every powered arm and the head walk to their nearest dock (rest or grasp start) first, then base stops and
        all torque goes off."""
        powered = [part for part in self.rest_pose if self.link.torque[part]]
        if powered:
            self.park(powered)
        self.teleop.shutdown()
        self.cameras.shutdown()
        self.link.disconnect()

    def page_config(self) -> dict:
        """What the page needs once: the robot's name, the parts, cameras and the keymap with labels."""
        keymap = {part: [{"action": action, "code": code, "label": TeleopConfig.ACTION_LABELS[action]}
                         for action, code in actions.items()] for part, actions in TeleopConfig.KEYMAP.items()}
        config = {"robot": self.link.robot.config.id, "parts": RobotConfig.PARTS, "cameras": CameraStreamConfig.CAMERAS,
                  "keymap": keymap, "base_levels": TeleopConfig.BASE_SPEED_LEVELS,
                  "heartbeat_ms": int(TeleopConfig.WATCHDOG_S * 1000 / 3), "rest_pose": self.rest_pose,
                  "started_at": self.started_at}
        return config

    def status(self) -> dict:
        """The loop's last pass, plus what must not come from it: if the loop has died its pass is stale, and the
        torque switches still have to show what the motors really have."""
        state = dict(self.teleop.state)
        state["connected"] = self.link.connected
        state["loop_alive"] = self.teleop.alive
        state["torque"] = dict(self.link.torque)
        state["away_from_rest"] = {part: self.away_from_rest(part) for part in self.rest_pose}
        state["camera_frames"] = dict(self.cameras.frames)
        now = time.time()
        state["camera_age_s"] = {view: round(now - stamp, 2) for view, stamp in self.cameras.times.items()}
        state["overlay"] = self.overlay
        return state

    def arm_follow(self) -> dict:
        """Lets an outside planner drive again after a stop (it calls this at the start of each of its steps)."""
        self.teleop.arm_follow()
        result = {"armed": True}
        return result

    def follow(self, part: str, joints: dict[str, float]) -> dict:
        """One joint target for a powered arm (follow mode); the reply carries where the arm is."""
        accepted = self.link.torque[part] and self.teleop.follow(part, joints)
        state = self.teleop.state
        result = {"accepted": bool(accepted), "blocked": self.teleop.follow_blocked, "torque": self.link.torque[part],
                  "present": {joint: values[0] for joint, values in state["joints"][part].items()}, "time": state["time"]}
        return result

    def end_follow(self, part: str) -> None:
        self.teleop.end_follow(part)

    def set_overlay(self, source: str, items: list[dict]) -> dict:
        """items: {label, score, box [x1, y1, x2, y2], polygon [[x, y], ...], target, distance_m} in head pixels."""
        self.overlay = {"time": time.time(), "source": source, "items": items}
        result = {"items": len(items)}
        return result

    def set_torque_all(self, on: bool) -> dict:
        """The page's one torque switch. On: every part, but only when the arms and the head are all at the rest
        pose or the grasp start pose (otherwise nothing is switched on). Off: every powered part parks at its nearest
        of the two, then the base goes off too."""
        if not on:
            result = self.park(list(self.rest_pose))
            self.link.set_torque("base", False)
            return result
        away = {part: joints for part in self.rest_pose if (joints := self.away_from_rest(part))}
        if away:
            result = {"on": False, "away_from_rest": away}
            return result
        result = {"on": True}
        for part in RobotConfig.PARTS:
            if not self.link.torque[part]:
                try:
                    self.link.set_torque(part, True)
                except ConnectionError as error:
                    # That part was switched off again; the ones already on sit at the rest pose, so they go off too.
                    logger.error("{} 上力矩失败：{}", RobotConfig.PARTS[part], error)
                    for other in RobotConfig.PARTS:
                        if self.link.torque[other]:
                            self.link.set_torque(other, False)
                    result = {"on": False, "error": str(error)}
                    break
        return result

    def set_keys(self, codes: list[str], client: str = "local") -> None:
        self.teleop.set_keys(codes, client)

    def drop_client(self, client: str) -> None:
        self.teleop.drop_client(client)

    def set_torque(self, part: str, on: bool) -> dict:
        """On for an arm or the head only at the rest or grasp start pose; off = park: to the nearer of them, then off."""
        if part == "base":
            self.link.set_torque(part, on)
            result = {"part": part, "on": on}
        elif on:
            away = self.away_from_rest(part)
            result = {"part": part, "on": False, "away_from_rest": away}
            if not away:
                try:
                    self.link.set_torque(part, True)
                    result["on"] = True
                except ConnectionError as error:
                    # The bus still got no answer after its retries; the part was switched off again.
                    logger.error("{} 上力矩失败：{}", RobotConfig.PARTS[part], error)
                    result["error"] = str(error)
        else:
            result = self.park([part])
        return result

    def dock(self, part: str) -> dict[str, float]:
        """The part's rest pose or other dock (grasp start), whichever its joints are nearest (by the most-off joint)."""
        joints = self.teleop.state["joints"][part]
        poses = [self.rest_pose[part]] + [dock[part] for dock in self.other_docks if part in dock]
        nearest = min(poses, key=lambda pose: max(abs(joints[joint][0] - value) for joint, value in pose.items()))
        return nearest

    def away_from_rest(self, part: str) -> dict[str, float]:
        """Joints further than START_TOLERANCE_DEG from the nearest dock (rest or grasp start) -> how far (from the
        loop's last reading)."""
        joints = self.teleop.state["joints"][part]
        away = {joint: round(joints[joint][0] - value, 1) for joint, value in self.dock(part).items()
                if abs(joints[joint][0] - value) > RestPoseConfig.START_TOLERANCE_DEG}
        return away

    def park(self, parts: list[str]) -> dict:
        """Walks the parts to their nearest dock, rest or grasp start (their keys are ignored meanwhile), lets them
        settle, then cuts their torque. Esc / the stop button cancels: the parts freeze where they are and keep their
        torque. Blocks."""
        parts = [part for part in parts if self.link.torque[part]]
        self.teleop.parking.update(parts)
        targets = {part: self.dock(part) for part in parts}
        for part in parts:
            self.teleop.go_to(part, targets[part])
        logger.info("回停靠位后断力矩：{}", [RobotConfig.PARTS[part] for part in parts])
        deadline = time.monotonic() + RestPoseConfig.TIMEOUT_S
        while any(self.teleop.moving(part) for part in parts) and time.monotonic() < deadline:
            time.sleep(0.05)
        arrived = [part for part in parts if self.at_rest(part, targets[part])]
        cancelled = [part for part in parts if part not in arrived]
        if arrived:
            time.sleep(RestPoseConfig.SETTLE_S)
        for part in arrived:
            self.link.set_torque(part, False)
        self.teleop.parking.difference_update(parts)
        if cancelled:
            logger.warning("没到停靠位（被停止或超时），保持力矩：{}", [RobotConfig.PARTS[part] for part in cancelled])
        result = {"parked": arrived, "cancelled": cancelled}
        return result

    def at_rest(self, part: str, pose: dict[str, float]) -> bool:
        """Targets at pose (clamped to the calibrated range, as go_to does)."""
        target = self.teleop.target(part)
        limits = self.teleop.teleop_of(part).limits
        rest = {joint: max(limits[joint][0], min(limits[joint][1], value)) for joint, value in pose.items()}
        arrived = all(abs(target[joint] - rest[joint]) <= RestPoseConfig.ARRIVED_DEG for joint in rest)
        return arrived

    def go_rest(self, part: str) -> dict:
        """Walks the part to the rest pose and keeps its torque."""
        on = self.link.torque[part]
        if on:
            self.teleop.go_to(part, self.rest_pose[part])
        result = {"part": part, "moving": on}
        return result

    def record_rest_pose(self) -> dict:
        """The joints as they are now become the rest pose (arms and head), saved to RestPoseConfig.FILE."""
        joints = self.teleop.state["joints"]
        self.rest_pose = {part: {joint: round(values[0], 2) for joint, values in joints[part].items()} for part in self.rest_pose}
        RestPoseConfig.FILE.write_text(json.dumps({"recorded": time.strftime("%Y-%m-%d %H:%M:%S"), "robot": self.link.robot.config.id,
                                                   "note": "joint positions in lerobot degrees (gripper 0-100), set by hand by the user",
                                                   "pose": self.rest_pose}, indent=2, ensure_ascii=False))
        logger.info("休息位已记录：{}", self.rest_pose)
        result = {"rest_pose": self.rest_pose}
        return result

    def stop(self) -> dict:
        """Base to zero, every part frozen where it is (torque stays: cutting it would drop the arms)."""
        self.teleop.stop()
        logger.info("停止：底盘停，各部件停在原位")
        result = {"stopped": True}
        return result

    def head_rgbd(self) -> dict:
        """Colour + aligned depth from the head camera, with the head joints read right after."""
        frame = self.cameras.rgbd()
        head = self.teleop.state["joints"]["head"]
        frame["head_joints"] = np.array([head[motor][0] for motor in RobotConfig.HEAD_MOTORS])
        return frame

    def go_to_pose(self, name: str) -> dict:
        """Walks every powered arm and the head to a saved pose (config/<name>_pose.json, e.g. grasp_start)."""
        pose = json.loads((RestPoseConfig.FILE.parent / f"{name}_pose.json").read_text())["pose"]
        moving = [part for part in pose if self.link.torque[part]]
        for part in moving:
            self.teleop.go_to(part, pose[part])
        logger.info("去位姿 {}：{}", name, [RobotConfig.PARTS[part] for part in moving])
        result = {"pose": name, "moving": moving}
        return result

    def camera_jpeg(self, name: str) -> bytes | None:
        data = self.cameras.jpeg.get(name)
        return data


def demo_teleop_application() -> None:
    """Real robot, no torque: start, status twice, a camera frame, shut down."""
    application = RobotApplication()
    application.start()
    time.sleep(1.0)
    config = application.page_config()
    logger.info("机器人 {}，部件 {}，相机 {}，按键组 {}", config["robot"], list(config["parts"]), list(config["cameras"]), list(config["keymap"]))
    status = application.status()
    logger.info("状态：连接 {}，力矩 {}，相机帧 {}，底盘档 {}", status["connected"], status["torque"], status["camera_frames"], status["base_level"])
    logger.info("头部画面 {} 字节", len(application.camera_jpeg("head")))
    application.shutdown()


def main() -> None:
    use_utf8_output()
    demo_teleop_application()


if __name__ == "__main__":
    main()
