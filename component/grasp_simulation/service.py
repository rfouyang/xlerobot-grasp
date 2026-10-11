"""机器人仿真：MuJoCo 里的 XLeRobot 跟随 ros2_control 的关节指令，桌上的物体由接触真实受力；录制执行视频。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time
from collections.abc import Callable
from threading import Event, Thread

import imageio.v3 as iio
import numpy as np
from loguru import logger

from component.grasp_generation.placed_object import PlacedObject
from component.grasp_generation.tabletop_scene import TabletopScene
from config.settings import use_utf8_output
from config.simulation import SimConfig, ViewerConfig
from util.physics_sim_helper import PhysicsSimHelper


class RobotSimulation:
    """All MuJoCo work happens on one thread (EGL contexts belong to the thread that made them);
    the other methods only leave requests for it."""

    def __init__(self, scene: TabletopScene, **kwargs) -> None:
        self.scene = scene
        self.sim = PhysicsSimHelper()
        self.joint_source: Callable[[], dict[str, float]] | None = None
        self.pending_object: PlacedObject | None = None
        self.has_object = False
        # base_link <- object, updated every frame.
        self.object_pose: np.ndarray | None = None
        self.object_start_z = 0.0
        # (wall time, qpos) while recording; the clip is rendered from these afterwards.
        self.states: list[tuple[float, np.ndarray]] | None = None
        self.video_written = Event()
        self.video_path: Path | None = None
        self.object_settled = Event()

    def start(self, joint_source: Callable[[], dict[str, float]]) -> None:
        self.joint_source = joint_source
        self.build(None)
        Thread(target=self.loop, daemon=True).start()

    def place_object(self, placed: PlacedObject) -> np.ndarray:
        """Rebuilds the world with the object, lets it settle and returns where it came to rest."""
        self.object_settled.clear()
        self.pending_object = placed
        self.object_settled.wait()
        pose = self.object_pose.copy()
        return pose

    def start_recording(self) -> None:
        self.video_written.clear()
        self.states = []

    def stop_recording(self, name: str) -> Path:
        """Blocks until the loop has written the clip."""
        path = SimConfig.VIDEO_DIR / f"{name}.mp4"
        self.video_path = path
        self.video_written.wait()
        return path

    def lift_height(self) -> float:
        """How far the object's centre has risen since it was placed."""
        height = float(self.object_pose[2, 3] - self.object_start_z)
        return height

    def build(self, placed: PlacedObject | None) -> None:
        boxes = [{"name": "table", "size": self.scene.table_size, "pose": self.scene.table_pose,
                  "rgba": (*np.array(ViewerConfig.TABLE_COLOR) / 255, 1.0)}]
        objects = []
        if placed is not None:
            objects.append({"name": "object", "file": placed.file, "scale": placed.scale, "pose": placed.pose,
                            "mass": placed.mass, "rgba": placed.rgba})
        self.sim.build(boxes, objects, {"observer": SimConfig.OBSERVER_CAMERA, "closeup": SimConfig.CLOSEUP_CAMERA})
        self.sim.set_joints(self.joint_source())
        self.has_object = placed is not None
        if self.has_object:
            # Let it settle on the table before anything touches it.
            self.sim.step(int(SimConfig.SETTLE_S / SimConfig.TIMESTEP))
            self.object_pose = self.sim.body_pose("object")
            self.object_start_z = self.object_pose[2, 3]

    def loop(self) -> None:
        """Locked to the wall clock: each pass steps physics by the real time since the last one, since the
        joint targets come from controllers running in real time. Nothing is rendered here (rendering in
        the loop held it to ~19 passes a second); a recording only logs states."""
        last = time.time()
        while True:
            started = time.time()
            if self.pending_object is not None:
                placed, self.pending_object = self.pending_object, None
                self.build(placed)
                logger.info("仿真：{} 已放上桌，静止后挪动 {:.1f} mm", placed.label,
                            1000 * np.linalg.norm(self.object_pose[:3, 3] - placed.pose[:3, 3]))
                self.object_settled.set()
                last = time.time()
            self.sim.set_targets(self.joint_source())
            now = time.time()
            self.sim.step(min(SimConfig.MAX_STEPS_PER_PASS, max(1, round((now - last) / SimConfig.TIMESTEP))))
            last = now
            if self.has_object:
                self.object_pose = self.sim.body_pose("object")
            if self.states is not None:
                self.states.append((now, self.sim.state()))
                if self.video_path is not None:
                    self.write_video()
                    last = time.time()
            time.sleep(max(0.0, SimConfig.LOOP_S - (time.time() - started)))

    def write_video(self) -> None:
        """Replays the logged states at an even VIDEO_FPS: the side view, with the close-up inset top right.
        Physics waits meanwhile and resumes from where it was."""
        current = self.sim.state()
        times = np.array([moment for moment, _ in self.states])
        # The run opens with seconds of reachability checks and planning, the robot standing still; the clip
        # starts just before the first motion instead.
        moved = [index for index, (_, positions) in enumerate(self.states)
                 if np.abs(positions - self.states[0][1]).max() > SimConfig.MOTION_EPS]
        start = times[moved[0]] - SimConfig.LEAD_IN_S if moved else times[0]
        frames = []
        for moment in np.arange(max(start, times[0]), times[-1], 1 / SimConfig.VIDEO_FPS):
            index = min(int(np.searchsorted(times, moment)), len(times) - 1)
            self.sim.set_state(self.states[index][1])
            frames.append(self.compose(self.sim.render("observer"), self.sim.render("closeup")))
        self.sim.set_state(current)
        self.video_path.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(self.video_path, frames, fps=SimConfig.VIDEO_FPS)
        logger.info("仿真视频 {}（{} 帧，{:.1f} s）", self.video_path, len(frames), len(frames) / SimConfig.VIDEO_FPS)
        self.states = None
        self.video_path = None
        self.video_written.set()

    @staticmethod
    def compose(main: np.ndarray, closeup: np.ndarray) -> np.ndarray:
        frame = main.copy()
        inset = closeup[:: SimConfig.INSET_SHRINK, :: SimConfig.INSET_SHRINK]
        height, width = inset.shape[:2]
        margin, border = 16, 3
        top, left = margin, frame.shape[1] - margin - width
        frame[top - border: top + height + border, left - border: left + width + border] = 255
        frame[top: top + height, left: left + width] = inset
        return frame


def demo_robot_simulation() -> None:
    """A scripted joint source (no ROS): the right arm dips over the pudding box; writes a 3 s clip."""
    scene = TabletopScene()
    simulation = RobotSimulation(scene)
    started = time.time()

    def joints() -> dict[str, float]:
        phase = min(1.0, (time.time() - started) / 3.0)
        positions = {"Pitch": 0.3 + 0.3 * phase, "Elbow": 0.3 + 0.4 * phase, "Wrist_Pitch": 0.4, "Jaw": 1.0}
        return positions

    simulation.start(joints)
    logger.info("静止位姿 {}", np.round(simulation.place_object(scene.place("sprite_bottle"))[:3, 3], 3))
    time.sleep(1.0)
    simulation.start_recording()
    time.sleep(3.0)
    path = simulation.stop_recording("demo_robot_simulation")
    logger.info("视频 {}；物体升高 {:.3f} m", path, simulation.lift_height())


def main() -> None:
    use_utf8_output()
    demo_robot_simulation()


if __name__ == "__main__":
    main()
