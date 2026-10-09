"""XLeRobot 抓取应用门面：页面和命令行都只调这里。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time

from loguru import logger

from component.perception.preview import DepthPreview
from component.simulation.grasp_execution import GraspExecution
from component.grasp_generation.service import GraspGeneration
from component.task.runner import TaskRunner
from component.simulation.robot_operation import RobotOperation
from component.simulation.robot_simulation import RobotSimulation
from component.simulation.scene_display import SceneDisplay
from config.settings import use_utf8_output
from config.simulation import SimConfig
from util.moveit_helper import MoveItHelper
from util.teleop_helper import TeleopHelper


class GraspApplication:
    """Construction is cheap; start() connects to ROS, loads GraspGenX and cuRobo, starts the MuJoCo world
    (which follows the joint commands) and opens the 3D view."""

    def __init__(self) -> None:
        self.robot = None
        self.generation = None
        self.execution = None
        self.simulation = None
        # The real robot, through the xlerobot-teleop service.
        self.real: TaskRunner | None = None
        # The head depth live on the grasp page, raw and refined.
        self.depth_preview: DepthPreview | None = None
        self.display = SceneDisplay()
        self.last_result: dict | None = None
        self.busy = False

    @property
    def viewer_port(self) -> int:
        port = self.display.port
        return port

    def start(self) -> None:
        moveit = MoveItHelper()
        self.robot = RobotOperation(moveit)
        self.generation = GraspGeneration()
        self.execution = GraspExecution(moveit)
        self.simulation = RobotSimulation(self.generation.scene)
        self.simulation.start(self.robot.joint_state)
        self.display.start()
        self.display.follow(self.robot.joint_state, lambda: self.simulation.object_pose)
        # Shares GraspGenX with the simulation; MoveIt's mock is what the real arm follows.
        self.real = TaskRunner(moveit, TeleopHelper(), prediction=self.generation.prediction)
        # Shares detection's LingBot-Depth (None when DEPTH_REFINE is off: then only the raw depth).
        perception = self.real.planning.perception
        self.depth_preview = DepthPreview(TeleopHelper(), perception.refiner if perception.refine else None,
                                          stereo=perception.stereo if perception.source == "stereo" else None, gpu_lock=self.real.gpu_lock,
                                          target=lambda: {**self.real.target, "mask": self.real.target_mask},
                                          reach_overlay=lambda frame: {**{side: reach.overlay(frame) for side, reach in self.real.reaches.items()},
                                                                       "midline": self.real.midline_pixels(frame)})
        self.depth_preview.start()

    # --- robot operation ---

    def joint_state(self) -> dict[str, float]:
        state = self.robot.joint_state()
        return state

    def robot_poses(self) -> dict[str, dict]:
        poses = self.robot.poses()
        return poses

    def move_robot(self, arm: str, part: str, pose: str) -> bool:
        moved = self.robot.move(arm, part, pose)
        return moved

    # --- grasp generation ---

    def objects(self) -> list[dict]:
        catalog = self.generation.objects()
        return catalog

    def generate_grasps(self, name: str) -> dict:
        """Puts the object on the simulator's table, lets it come to rest, runs GraspGenX on it there,
        draws everything."""
        started = time.time()
        # The arm leaves the last run's pose first: on the way to "ready" it would sweep through the new object.
        self.execution.motion.go_ready()
        self.generation.settle(self.simulation.place_object(self.generation.place(name)))
        candidates = self.generation.predict()
        placed = self.generation.placed
        self.display.show_tabletop(self.generation.scene.table_pose, self.generation.scene.table_size,
                                   placed.mesh, placed.pose, placed.rgba, self.generation.points)
        self.display.show_grasps([c.pose for c in candidates], [c.score for c in candidates],
                                 self.generation.prediction.gripper_config)
        result = {
            "object": placed.label,
            "seconds": round(time.time() - started, 2),
            "grasps": [{"index": c.index, "score": round(c.score, 3)} for c in candidates],
        }
        return result

    def select_grasp(self, index: int) -> dict:
        candidate = self.generation.candidates[index]
        self.display.show_selected_grasp(candidate.pose)
        result = {"index": index, "score": round(candidate.score, 3), "position": [round(float(v), 3) for v in candidate.pose[:3, 3]]}
        return result

    # --- grasp execution ---

    def execute_grasp(self) -> dict:
        """Plans and runs the best reachable grasp on the object generated last, recording the simulator;
        blocks until done. Success is judged in the simulator: did the object rise with the jaw?"""
        self.busy = True
        # The candidate forks would hide the object while it is lifted; the robot model shows the real jaw.
        self.display.show_grasps([], [], self.generation.prediction.gripper_config)
        self.simulation.start_recording()
        done = self.execution.run(self.generation.scene, self.generation.placed, self.generation.candidates)
        time.sleep(SimConfig.TAIL_S)
        self.execution.report("正在渲染仿真视频（30 fps，侧视 + 近景）")
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_{self.generation.placed.name}"
        video = self.simulation.stop_recording(name)
        lifted = self.simulation.lift_height()
        grasped = done and lifted >= SimConfig.LIFTED_M
        self.execution.report(f"仿真：物体升高 {lifted * 100:.1f} cm → {'抓取成功' if grasped else '抓取失败'}")
        chosen = self.execution.chosen
        result = {"done": done, "grasped": grasped, "lifted_cm": round(lifted * 100, 1),
                  "index": None if chosen is None else chosen.index, "video": f"/videos/{video.name}",
                  "progress": self.execution.progress}
        self.last_result = result
        self.busy = False
        return result

    # --- the real robot ---

    def real_status(self) -> dict:
        status = self.real.status()
        return status

    def real_run(self, name: str) -> dict:
        """Blocks until the step is done."""
        result = self.real.run(name)
        return result

    def real_run_all(self) -> dict:
        """Returns at once; the page follows the progress in the status."""
        result = self.real.run_all()
        return result

    def real_reset(self, action: str = "grasp") -> dict:
        self.real.reset(action)
        status = self.real.status()
        return status

    def real_action(self, action: str, ignore_left: bool = False) -> dict:
        """From step 0, the whole of action: "move_right" (the bottle moved right), "place_in_box" (into the open box on
        the desk), or "grasp" (ignore_left: grasp even though the bottle stands too far left). Returns at once, like
        run_all."""
        if self.real.running or self.real.auto:
            result = {"ok": False, "error": "正在执行"}
            return result
        self.real.reset(action, ignore_left)
        result = self.real.run_all()
        return result

    def real_target(self, prompt: str | None, point: list[float] | None, clear: bool) -> dict:
        """The interactive grasp page's target: a click (point), a name (prompt), or clear (the default bottle)."""
        result = self.real.set_target(prompt, point, clear)
        return result

    def real_set_arm(self, side: str) -> dict:
        """The interactive grasp page's arm: auto (the nearer shoulder), left or right."""
        result = self.real.set_arm(side)
        return result

    def real_stop(self) -> dict:
        """The teleop service's stop: the base halts and every part freezes; the running step then fails."""
        result = self.real.teleop.stop()
        return result

    def real_plan_image(self) -> Path | None:
        path = self.real.plan_image
        return path

    def real_align_image(self) -> Path | None:
        path = self.real.align_image
        return path

    def depth_preview_jpeg(self, kind: str) -> bytes | None:
        """The live head depth: "raw" (the sensor's) or "refined" (LingBot-Depth's); None until the first one is made."""
        picture = self.depth_preview.latest(kind)
        return picture

    def execution_status(self) -> dict:
        """busy covers the whole run including the video render, so the page never shows the previous
        clip as this run's result."""
        status = {"running": self.busy, "progress": self.execution.progress, "result": self.last_result}
        return status


def demo_application() -> None:
    """Command line: start, pick up the water bottle end to end, print where the video went.
    Needs the bringup launch."""
    application = GraspApplication()
    application.start()
    logger.info("viser：http://localhost:{}", application.viewer_port)
    application.move_robot("right", "arm", "ready")
    result = application.generate_grasps("water_bottle")
    logger.info("{}：{} 个抓取，用时 {}s", result["object"], len(result["grasps"]), result["seconds"])
    time.sleep(1.0)
    result = application.execute_grasp()
    logger.info("抓取 {}，升高 {} cm，视频 {}", "成功" if result["grasped"] else "失败", result["lifted_cm"], result["video"])


def main() -> None:
    use_utf8_output()
    demo_application()


if __name__ == "__main__":
    main()
