"""xlerobot-teleop 服务的客户端：真机的头部 RGB-D、状态、检测叠加（真机只经它访问）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import io
import time

import httpx
import numpy as np
from loguru import logger

from config.settings import HeadCameraConfig, RealRobotConfig, use_utf8_output


class TeleopHelper:
    """The teleop service owns the serial ports and cameras and enforces the safety rules; this only talks HTTP."""

    def __init__(self, url: str = RealRobotConfig.TELEOP_URL, **kwargs) -> None:
        self.client = httpx.Client(base_url=url, timeout=kwargs.get("timeout", RealRobotConfig.TIMEOUT_S))

    def rgbd(self, infrared: bool = False) -> dict[str, np.ndarray]:
        """color (H, W, 3) RGB uint8, depth (H, W) uint16 aligned to it, K (3, 3), depth_scale (m per unit),
        head_joints (pan, tilt in lerobot degrees), time. depth_scale carries the calibration's correction (the sensor
        reads ~1 % too far). infrared: also the D415's infrared pair, ir_left / ir_right (H, W) uint8, with K_ir,
        baseline_m and color_from_ir (4x4, the left infrared camera to the colour camera)."""
        response = self.get("/api/rgbd?infrared=1" if infrared else "/api/rgbd")
        archive = np.load(io.BytesIO(response.content))
        frame = {key: archive[key] for key in archive.files}
        age = time.time() - float(frame["time"])
        if age > HeadCameraConfig.MAX_FRAME_AGE_S:
            raise RuntimeError(f"头部相机画面已经 {age:.0f} s 没更新（teleop 的相机线程停了？）")
        frame["depth_scale"] = frame["depth_scale"] / HeadCameraConfig.DEPTH_SCALE
        return frame

    def camera(self, name: str) -> np.ndarray:
        """The latest frame of one camera as the teleop page shows it (wrist cameras rotated upright), RGB."""
        import cv2

        age = self.status().get("camera_age_s", {}).get(name)
        if age is not None and age > HeadCameraConfig.MAX_FRAME_AGE_S:
            raise RuntimeError(f"相机 {name} 的画面已经 {age:.0f} s 没更新（teleop 的相机线程停了？）")
        response = self.get(f"/snapshot/{name}")
        bgr = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return rgb

    def status(self) -> dict:
        response = self.get("/api/status")
        status = response.json()
        return status

    def overlay(self, source: str, items: list[dict]) -> dict:
        """Draws detections on the teleop page's head view (head image pixels)."""
        result = self.post("/api/overlay", {"source": source, "items": items})
        return result

    def get(self, path: str) -> httpx.Response:
        """A read, tried once more when the connection drops before any answer: a kept-alive connection the server
        had just closed failed a step once ("Server disconnected without sending a response", 12:06). Reads only;
        posts (torque, poses, stop) are never repeated."""
        try:
            response = self.client.get(path)
        except (httpx.RemoteProtocolError, httpx.ConnectError) as error:
            logger.warning("teleop 连接断了（{}），重试一次：GET {}", error, path)
            response = self.client.get(path)
        response.raise_for_status()
        return response

    def post(self, path: str, body: dict | None = None) -> dict:
        response = self.client.post(path, json=body or {})
        response.raise_for_status()
        result = response.json()
        return result

    def arm_follow(self) -> dict:
        """Following is blocked after every stop; each execution step arms it first."""
        result = self.post("/api/follow/arm")
        return result

    def follow_stream(self):
        """A WebSocket for {"part", "joints"} targets; each send gets {accepted, blocked, torque, present} back."""
        from websockets.sync.client import connect

        url = str(self.client.base_url).replace("http://", "ws://").rstrip("/") + "/ws/follow"
        stream = connect(url)
        return stream

    def stop(self) -> dict:
        result = self.post("/api/stop")
        return result

    def torque(self, on: bool) -> dict:
        """The page's one switch: on only from the rest pose; off walks back to it first (blocks until done)."""
        result = self.post("/api/torque", {"part": "all", "on": on})
        return result

    def go_to_pose(self, name: str) -> dict:
        """A pose saved by the teleop service (rest, grasp_start); only parts with torque move."""
        result = self.post("/api/pose", {"name": name})
        return result

    def wait_still(self, **kwargs) -> bool:
        """Until no part is walking to a pose and every powered arm / head is within tolerance of its target."""
        import time

        tolerance = kwargs.get("tolerance_deg", RealRobotConfig.ARRIVED_DEG)
        # A pose request is picked up on the control loop's next pass: until then nothing is "moving" yet and the
        # targets still equal the joints, which would read as already arrived.
        started = time.monotonic() + kwargs.get("start_timeout", 1.0)
        while time.monotonic() < started and not any(self.status()["moving"].values()):
            time.sleep(0.05)
        deadline = time.monotonic() + kwargs.get("timeout", RealRobotConfig.MOVE_TIMEOUT_S)
        while time.monotonic() < deadline:
            status = self.status()
            moving = any(status["moving"].values())
            error = max(abs(present - target) for part, joints in status["joints"].items() if status["torque"][part]
                        for present, target in joints.values()) if any(status["torque"].values()) else 0.0
            if not moving and error <= tolerance:
                return True
            time.sleep(0.1)
        return False


def demo_teleop_helper() -> None:
    """Needs the teleop service; reads only."""
    helper = TeleopHelper()
    frame = helper.rgbd()
    depth = frame["depth"] * frame["depth_scale"]
    logger.info("RGB-D：彩色 {}，深度 {}（有效 {:.0f}%），头部关节 {}", frame["color"].shape, frame["depth"].shape,
                100 * (depth > 0).mean(), np.round(frame["head_joints"], 1))
    status = helper.status()
    logger.info("状态：循环 {}，力矩 {}", status["loop_alive"], status["torque"])


def main() -> None:
    use_utf8_output()
    demo_teleop_helper()


if __name__ == "__main__":
    main()
