"""相机画面：后台线程从三个相机取最新帧，编码成 JPEG 给页面。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

import cv2
import numpy as np
import pyrealsense2 as rs
from loguru import logger

from config.robot import CameraStreamConfig

from config.settings import use_utf8_output


class CameraStream:
    """Its own thread, so the control loop never waits for a frame. The wrist cameras come from lerobot's reader
    threads (async_read: the newest frame, RGB). The head is read here as aligned colour + depth from one
    frameset (lerobot does not align), so the page's head RGB, its depth view and /api/rgbd all show the same
    moment; lerobot's own head reader keeps running on the same pipeline and simply gets the other framesets."""

    def __init__(self, cameras: dict, **kwargs) -> None:
        self.cameras = cameras
        self.quality = kwargs.get("quality", CameraStreamConfig.JPEG_QUALITY)
        self.period = 1.0 / kwargs.get("hz", CameraStreamConfig.STREAM_HZ)
        # view (a camera, or head_depth) -> latest JPEG bytes
        self.jpeg: dict[str, bytes] = {}
        self.frames = dict.fromkeys(CameraStreamConfig.CAMERAS, 0)
        # view -> time.time() of its latest JPEG (how stale a view is shows in the status).
        self.times: dict[str, float] = {}
        # The head's latest aligned colour + depth (+ the infrared pair when streamed).
        self.head_frame: dict | None = None
        # The infrared pair's geometry, read from the first frameset that has it: K_ir, baseline_m, color_from_ir.
        self.infrared_geometry: dict | None = None
        # The head colour stream's last frame number and when it last changed: on 2026-10-09 the colour froze (the same
        # picture again and again) while the depth went on, and every picture looked fresh.
        self.color_number = -1
        self.color_changed = time.time()
        self.running = False
        self.thread: threading.Thread | None = None

    def read_rgbd(self, name: str = "head") -> dict:
        """One colour + depth pair from the RealSense, depth aligned to the colour image, colour intrinsics."""
        camera = self.cameras[name]
        frames = camera.rs_pipeline.wait_for_frames(1000)
        aligned = rs.align(rs.stream.color).process(frames)
        color_frame, depth_frame = aligned.get_color_frame(), aligned.get_depth_frame()
        number = color_frame.get_frame_number()
        if number != self.color_number:
            self.color_number, self.color_changed = number, time.time()
        elif time.time() - self.color_changed > CameraStreamConfig.FROZEN_S:
            raise RuntimeError(f"头部彩色画面卡住了（帧号 {number} 已 {time.time() - self.color_changed:.1f} s 没变）")
        intrinsics = color_frame.profile.as_video_stream_profile().intrinsics
        depth_scale = camera.rs_profile.get_device().first_depth_sensor().get_depth_scale()
        frame = {
            "color": np.asanyarray(color_frame.get_data()).copy(),
            "depth": np.asanyarray(depth_frame.get_data()).copy(),
            "K": np.array([[intrinsics.fx, 0, intrinsics.ppx], [0, intrinsics.fy, intrinsics.ppy], [0, 0, 1]]),
            "depth_scale": depth_scale,
            "time": time.time(),
        }
        left, right = frames.get_infrared_frame(1), frames.get_infrared_frame(2)
        if left and right:
            if self.infrared_geometry is None:
                self.infrared_geometry = self.infrared_pair_geometry(left, right, color_frame)
            frame.update({"ir_left": np.asanyarray(left.get_data()).copy(), "ir_right": np.asanyarray(right.get_data()).copy(),
                          **self.infrared_geometry})
        return frame

    @staticmethod
    def infrared_pair_geometry(left, right, color_frame) -> dict:
        """The left infrared camera's intrinsics (the pair is rectified: both share them), the baseline (m), and the
        transform from the left infrared camera to the colour camera (4x4; librealsense's rotation is column-major)."""
        left_profile = left.profile.as_video_stream_profile()
        intrinsics = left_profile.intrinsics
        baseline = abs(left_profile.get_extrinsics_to(right.profile).translation[0])
        to_color = left_profile.get_extrinsics_to(color_frame.profile)
        color_from_ir = np.eye(4)
        color_from_ir[:3, :3] = np.array(to_color.rotation).reshape(3, 3).T
        color_from_ir[:3, 3] = to_color.translation
        geometry = {"K_ir": np.array([[intrinsics.fx, 0, intrinsics.ppx], [0, intrinsics.fy, intrinsics.ppy], [0, 0, 1]]),
                    "baseline_m": np.float64(baseline), "color_from_ir": color_from_ir}
        logger.info("头部红外双目：fx {:.1f}，基线 {:.1f} mm，到彩色相机 {} mm", intrinsics.fx, baseline * 1000,
                    np.round(color_from_ir[:3, 3] * 1000, 1).tolist())
        return geometry

    def rgbd(self) -> dict:
        frame = dict(self.head_frame)
        return frame

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self.loop, name="camera_stream", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.running = False
        self.thread.join()

    def encode(self, view: str, image_bgr: np.ndarray) -> None:
        ok, encoded = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        self.jpeg[view] = encoded.tobytes()
        self.frames[view] += 1
        self.times[view] = time.time()

    def loop(self) -> None:
        """A camera that misses a frame is tried again next round: one RealSense frameset late over usbip (Frame didn't
        arrive within 1000) ended this thread for good on 2026-10-08 and froze every view while the robot moved."""
        misses = dict.fromkeys(self.cameras, 0)
        while self.running:
            started = time.monotonic()
            for name, camera in self.cameras.items():
                try:
                    self.read_view(name, camera)
                    if misses[name]:
                        logger.info("相机 {} 恢复（之前连续 {} 次没取到帧）", name, misses[name])
                    misses[name] = 0
                except Exception as error:
                    misses[name] += 1
                    if misses[name] in (1, 10) or misses[name] % 100 == 0:
                        logger.warning("相机 {} 这一帧没取到（连续 {} 次）：{}", name, misses[name], error)
                    if name == "head" and misses[name] % CameraStreamConfig.RECONNECT_MISSES == 0:
                        self.reconnect(camera)
            time.sleep(max(0.0, self.period - (time.monotonic() - started)))

    def reconnect(self, camera) -> None:
        """The head RealSense closed and opened again (its infrared pair still enabled: the patched pipeline config
        stays on the camera object)."""
        logger.warning("头部相机重新连接")
        try:
            camera.disconnect()
        except Exception as error:
            logger.warning("断开头部相机出错：{}", error)
        try:
            camera.connect()
            self.color_number, self.color_changed = -1, time.time()
            logger.info("头部相机已重新连接")
        except Exception as error:
            logger.warning("重新连接头部相机失败：{}", error)

    def read_view(self, name: str, camera) -> None:
        if name == "head":
            low, high = CameraStreamConfig.DEPTH_RANGE_M
            self.head_frame = self.read_rgbd(name)
            self.encode("head", cv2.cvtColor(self.head_frame["color"], cv2.COLOR_RGB2BGR))
            depth = self.head_frame["depth"] * self.head_frame["depth_scale"]
            scaled = np.clip((depth - low) / (high - low) * 255, 0, 255).astype(np.uint8)
            view = cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
            view[depth <= 0] = 0
            self.encode("head_depth", view)
        else:
            self.encode(name, cv2.cvtColor(camera.async_read(timeout_ms=1000), cv2.COLOR_RGB2BGR))


def demo_camera_stream() -> None:
    """Real cameras, no motors: 2 s of streaming, one JPEG per camera saved."""
    from component.robot.link import RobotLink
    from config.settings import PathConfig

    link = RobotLink()
    for camera in link.robot.cameras.values():
        camera.connect()
    stream = CameraStream(link.robot.cameras)
    stream.start()
    time.sleep(2.0)
    stream.shutdown()
    out_dir = PathConfig.DEMO_DIR / "camera_stream"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in stream.jpeg.items():
        (out_dir / f"{name}.jpg").write_bytes(data)
        logger.info("{}: {} 帧 / 2 s，JPEG {} KB → {}", name, stream.frames[name], len(data) // 1024, out_dir / f"{name}.jpg")
    frame = stream.rgbd()
    depth_m = frame["depth"] * frame["depth_scale"]
    logger.info("头部 RGB-D：彩色 {}，深度 {}（有效 {:.0f}%，中位 {:.2f} m），fx {:.1f}", frame["color"].shape, frame["depth"].shape,
                100 * (depth_m > 0).mean(), float(np.median(depth_m[depth_m > 0])), frame["K"][0, 0])
    for camera in link.robot.cameras.values():
        camera.disconnect()


def main() -> None:
    use_utf8_output()
    demo_camera_stream()


if __name__ == "__main__":
    main()
