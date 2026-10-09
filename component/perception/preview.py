"""深度实时预览：头部相机传感器自己的深度，和规划用的深度（FFS 双目，或者再经 LingBot-Depth 修正），持续生成两张同一色标的
JPEG，给抓取页看；抓取放置页还要头部 RGB 上框出瓶子和盒子。"""

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
from loguru import logger

from config.perception import PerceptionConfig

from config.settings import use_utf8_output

from config.task import PlaceConfig
from util.depth_refine_helper import DepthRefineHelper
from util.teleop_helper import TeleopHelper


class DepthPreview:
    """Its own thread, and only while the page asks (a picture fetched within PREVIEW_IDLE_S): otherwise the GPU is left
    to detection and planning. The refiner is the one detection uses; its lock keeps the two from running the model at
    once. gpu_lock: the planning step's, held while cuRobo records CUDA graphs; the preview waits for it (no other GPU
    work may run during a recording). One fixed colour scale (PREVIEW_RANGE_M), so the live pictures do not flicker
    and the two compare."""

    def __init__(self, teleop: TeleopHelper, refiner: DepthRefineHelper | None, **kwargs) -> None:
        self.teleop = teleop
        self.refiner = refiner
        # "raw" is always the sensor's depth; "refined" is what planning uses when it differs: Fast-FoundationStereo's
        # (stereo given) and/or LingBot-Depth's refinement of it (refiner given).
        self.stereo = kwargs.get("stereo")
        self.gpu_lock: threading.Lock = kwargs.get("gpu_lock") or threading.Lock()
        # "raw" / "refined" / "detect" -> the latest JPEG
        self.jpeg: dict[str, bytes] = {}
        self.asked_at = 0.0
        # "raw" / "refined" asked: only then does the depth loop run the stereo.
        self.depth_asked_at = 0.0
        # "detect" (the head RGB with the bottle and the box framed) only while a page asks for it; its own YOLOE,
        # loaded on first use: detection's switches prompts between the bottle and the box.
        self.detect_asked_at = 0.0
        self.detector = None
        # The interactive page's own: switching one YOLOE between the two prompt sets re-encodes the text (~0.5 s a frame).
        self.target_detector = None
        # "place" (the pick-place page): the target view with the boxes framed in orange too; its own YOLOE for them.
        self.place_asked_at = 0.0
        self.place_detector = None
        # "target": the head RGB with the interactive page's choice (target() -> {prompt, point, ...}) framed, the one
        # at its point marked; only while that page asks.
        self.target = kwargs.get("target")
        self.target_asked_at = 0.0
        # frame -> {side: the head-image mask of where on the desk that arm can grasp, or None}, drawn on "target".
        self.reach_overlay = kwargs.get("reach_overlay")
        self.running = False
        self.thread: threading.Thread | None = None
        self.overlay_thread: threading.Thread | None = None

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self.loop, name="depth_preview", daemon=True)
        self.thread.start()
        self.overlay_thread = threading.Thread(target=self.overlay_loop, name="depth_preview_overlay", daemon=True)
        self.overlay_thread.start()

    def latest(self, kind: str) -> bytes | None:
        """The newest picture of kind ("raw" or "refined"); asking keeps the preview running."""
        self.asked_at = time.monotonic()
        if kind == "detect":
            self.detect_asked_at = self.asked_at
        elif kind == "target":
            self.target_asked_at = self.asked_at
        elif kind == "place":
            self.place_asked_at = self.asked_at
        else:
            self.depth_asked_at = self.asked_at
        picture = self.jpeg.get(kind)
        return picture

    def loop(self) -> None:
        period = 1.0 / PerceptionConfig.PREVIEW_HZ
        while self.running:
            if time.monotonic() - self.depth_asked_at > PerceptionConfig.PREVIEW_IDLE_S:
                time.sleep(0.2)
                continue
            started = time.monotonic()
            try:
                frame = self.teleop.rgbd(infrared=self.stereo is not None)
                sensor = frame["depth"] * frame["depth_scale"]
                depths = {"raw": sensor}
                planned = sensor
                if self.stereo is not None:
                    with self.gpu_lock:
                        planned = self.stereo.depth(frame)
                if self.refiner is not None:
                    with self.gpu_lock:
                        planned = self.refiner.refine(frame["color"], planned)
                if planned is not sensor:
                    depths["refined"] = planned
                for kind, depth in depths.items():
                    self.jpeg[kind] = self.encode(depth)
            except Exception as error:
                # The teleop service restarting, say: try again shortly.
                logger.warning("深度预览这一帧没做出来：{}", error)
                time.sleep(1.0)
            time.sleep(max(0.0, period - (time.monotonic() - started)))

    def overlay_loop(self) -> None:
        """The framed head RGB ("detect" / "target") while a page asks: the colour alone (no infrared, no stereo), so
        it keeps up with the camera. Each frame's YOLOE holds the GPU lock (cuRobo must not see other GPU work while it
        records CUDA graphs: without it planning failed with "operation failed due to a previous error during capture",
        2026-10-09); while planning or the stereo has it, the frame is skipped and the last picture stays."""
        period = 1.0 / PerceptionConfig.OVERLAY_HZ
        while self.running:
            now = time.monotonic()
            detect = now - self.detect_asked_at < PerceptionConfig.PREVIEW_IDLE_S
            target = self.target is not None and now - self.target_asked_at < PerceptionConfig.PREVIEW_IDLE_S
            place = self.target is not None and now - self.place_asked_at < PerceptionConfig.PREVIEW_IDLE_S
            if not (detect or target or place):
                time.sleep(0.1)
                continue
            if not self.gpu_lock.acquire(timeout=0.05):
                time.sleep(0.05)
                continue
            try:
                steps = [("frame", time.monotonic())]
                frame = self.teleop.rgbd()
                steps.append(("rgbd", time.monotonic()))
                if detect:
                    self.jpeg["detect"] = self.framed(frame["color"])
                    steps.append(("detect", time.monotonic()))
                if target or place:
                    reach = self.reach_overlay(frame) if self.reach_overlay is not None else None
                    steps.append(("reach", time.monotonic()))
                    choice = self.target()
                    steps.append(("target()", time.monotonic()))
                    if target:
                        self.jpeg["target"] = self.targeted(frame["color"], choice, reach)
                    if place:
                        self.jpeg["place"] = self.targeted(frame["color"], choice, reach, boxes=True)
                    steps.append(("draw", time.monotonic()))
                if steps[-1][1] - steps[0][1] > 1.0:
                    logger.info("头部画面标注慢：{}", "，".join(f"{name} {(t - steps[k][1]) * 1000:.0f} ms" for k, (name, t) in enumerate(steps[1:])))
            except Exception as error:
                logger.warning("头部画面标注这一帧没做出来：{}", error)
                time.sleep(1.0)
            finally:
                self.gpu_lock.release()
            time.sleep(max(0.0, period - (time.monotonic() - now)))

    def framed(self, rgb: np.ndarray) -> bytes:
        """The head RGB with every bottle (green) and box (orange) YOLOE finds at the confidences detection uses: mask
        tinted, frame, label and score."""
        if self.detector is None:
            from util.detection_helper import DetectionHelper

            self.detector = DetectionHelper()
        prompts = (*PerceptionConfig.PROMPTS, *PlaceConfig.BOX_PROMPTS)
        detections = self.detector.detect(rgb, prompts, conf=min(PerceptionConfig.CONFIDENCE, PlaceConfig.BOX_CONFIDENCE))
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        for detection in reversed(detections):
            is_box = detection.label in PlaceConfig.BOX_PROMPTS
            if detection.score < (PlaceConfig.BOX_CONFIDENCE if is_box else PerceptionConfig.CONFIDENCE):
                continue
            colour = np.array((0, 140, 255) if is_box else (0, 200, 0))
            image[detection.mask] = (0.65 * image[detection.mask] + 0.35 * colour).astype(np.uint8)
            x1, y1, x2, y2 = detection.box.astype(int)
            cv2.rectangle(image, (x1, y1), (x2, y2), colour.tolist(), 2)
            text = f"{'box' if is_box else 'bottle'} {detection.score:.2f}"
            cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour.tolist(), 2)
        picture = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        return picture

    def targeted(self, rgb: np.ndarray, target: dict, reach: dict | None = None, boxes: bool = False) -> bytes:
        """Every detection of the target's prompt (green), the target in magenta with "TARGET" (its last SAM mask when it
        has one, else the detection at its point), and the point as a cross."""
        from util.detection_helper import Detection

        if self.target_detector is None:
            from util.detection_helper import DetectionHelper

            self.target_detector = DetectionHelper()
        detections = self.target_detector.detect(rgb, (target["prompt"],), conf=PerceptionConfig.CONFIDENCE)
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        # Where each arm can grasp, under everything else: the right one light green, the left one light blue.
        for side, colour in (("right", (80, 220, 80)), ("left", (230, 160, 60))):
            mask = (reach or {}).get(side)
            if mask is None:
                continue
            image[mask] = (0.82 * image[mask] + 0.18 * np.array(colour)).astype(np.uint8)
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image, contours, -1, colour, 1)
        midline = (reach or {}).get("midline")
        if midline is not None:
            # Halfway between the shoulders: left of it the left arm grasps, right of it the right arm.
            cv2.polylines(image, [np.round(midline).astype(np.int32)], False, (255, 255, 255), 2)
        point = target.get("point")
        chosen = None
        mask = target.get("mask")
        if mask is not None:
            # The SAM mask (refreshed at the click and at planning), not segmented again per frame (~0.65 s each).
            detections = [d for d in detections if (d.mask & mask).sum() < 0.5 * min(d.mask.sum(), mask.sum())]
            rows, cols = np.nonzero(mask)
            chosen = Detection(label=target["label"], score=target.get("score") or 1.0,
                               box=np.array([cols.min(), rows.min(), cols.max(), rows.max()], float), mask=mask)
            detections.append(chosen)
        elif point:
            u, v = int(point[0]), int(point[1])
            inside = [d for d in detections if 0 <= v < d.mask.shape[0] and 0 <= u < d.mask.shape[1] and d.mask[v, u]]
            chosen = min(inside, key=lambda d: d.mask.sum()) if inside else None
        for detection in reversed(detections):
            selected = detection is chosen
            colour = np.array((255, 0, 255) if selected else (0, 200, 0))
            image[detection.mask] = (0.6 * image[detection.mask] + 0.4 * colour).astype(np.uint8)
            x1, y1, x2, y2 = detection.box.astype(int)
            cv2.rectangle(image, (x1, y1), (x2, y2), colour.tolist(), 3 if selected else 1)
            text = f"{'TARGET ' if selected else ''}{target['prompt']} {detection.score:.2f}"
            cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
            cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour.tolist(), 2)
        if boxes:
            # Where pick-place puts it: every open box YOLOE sees, in orange (the backend picks the nearest one).
            if self.place_detector is None:
                from util.detection_helper import DetectionHelper

                self.place_detector = DetectionHelper()
            for box in self.place_detector.detect(rgb, PlaceConfig.BOX_PROMPTS, conf=PlaceConfig.BOX_CONFIDENCE):
                x1, y1, x2, y2 = box.box.astype(int)
                cv2.rectangle(image, (x1, y1), (x2, y2), (0, 140, 255), 3)
                text = f"PLACE: box {box.score:.2f}"
                cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
                cv2.putText(image, text, (x1 + 3, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 140, 255), 2)
        spot = target.get("spot") if boxes else None
        if spot:
            # 抓取摆放's desk point: an orange cross with a ring.
            centre = (int(spot["uv"][0]), int(spot["uv"][1]))
            cv2.drawMarker(image, centre, (0, 140, 255), cv2.MARKER_CROSS, 30, 3)
            cv2.circle(image, centre, 16, (0, 140, 255), 2)
            cv2.putText(image, "PLACE", (centre[0] + 18, centre[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
            cv2.putText(image, "PLACE", (centre[0] + 18, centre[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 140, 255), 2)
        if point:
            cv2.drawMarker(image, (int(point[0]), int(point[1])), (0, 0, 255), cv2.MARKER_CROSS, 22, 2)
        if not detections:
            cv2.putText(image, f"no '{target['prompt']}' found", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if target.get("by"):
            cv2.putText(image, target["by"], (image.shape[1] - 110, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        picture = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        return picture

    @staticmethod
    def encode(depth: np.ndarray) -> bytes:
        """TURBO from near (blue) to far (red) over PREVIEW_RANGE_M, black where there is no depth."""
        near, far = PerceptionConfig.PREVIEW_RANGE_M
        image = cv2.applyColorMap(np.clip((depth - near) / (far - near) * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        image[depth <= 0] = 0
        text = f"{100 * (depth > 0).mean():.0f}% valid, {near:.2f}-{far:.2f} m"
        cv2.putText(image, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(image, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        picture = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        return picture


def demo_depth_preview() -> None:
    """Needs the teleop service; reads only. A few seconds of preview, the two pictures saved under output/demo."""
    from config.settings import PathConfig

    preview = DepthPreview(TeleopHelper(), DepthRefineHelper() if PerceptionConfig.DEPTH_REFINE else None)
    preview.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and len(preview.jpeg) < (2 if preview.refiner else 1):
        preview.latest("raw")
        time.sleep(0.2)
    out = PathConfig.DEMO_DIR / "depth_preview"
    out.mkdir(parents=True, exist_ok=True)
    for kind, picture in preview.jpeg.items():
        (out / f"{kind}.jpg").write_bytes(picture)
    logger.info("深度预览：{}，存到 {}", list(preview.jpeg), out)


def main() -> None:
    use_utf8_output()
    demo_depth_preview()


if __name__ == "__main__":
    main()
