"""XLeRobot 网页进程的门面：页面的接口和命令行都只调这里。仿真（3D 视图、GraspGenX 在仿真桌上）、真机任务（物体抓取、
抓取摆放的步骤、选择）、任务页的对话和语音、抓取规划页、相机标定页、系统设置页。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import io
import threading
import time
import wave

from loguru import logger

from component.grasp_simulation.scene_display import SceneDisplay
from config.settings import use_utf8_output
from config.simulation import SimConfig
from config.voice import ByteplusAsrConfig, ByteplusTtsConfig


class GraspApplication:
    """Construction is cheap (tests build it without a GPU); start() connects to ROS, loads GraspGenX and cuRobo,
    starts the MuJoCo world (which follows the joint commands), the real-robot task and the head previews."""

    def __init__(self) -> None:
        self.robot = None
        self.generation = None
        self.execution = None
        self.simulation = None
        # The real robot (component/task_execution TaskRunner), through the robot process.
        self.real = None
        # The head views live on the task pages: depth raw and refined, the target and the place drawn.
        self.depth_preview = None
        self.display = SceneDisplay()
        self.last_result: dict | None = None
        self.busy = False
        # The task pages' chat (component/task_chat TaskChat), the speech services, made on first use.
        self.chat = None
        self.dialog = None
        self.asr = None
        self.tts = None
        # The page that speaks: the one last used (a tap, a click, a key on it); every other page stays silent.
        self.speaker_client: str | None = None
        # 相机标定: the session (made on first use) and the live board detection.
        self.calibration = None
        self.live_detection = None
        # 系统设置: the robot process and the bringup's supervisor (component/process_supervision), set by app/main.py.
        self.supervisor = None

    @property
    def viewer_port(self) -> int:
        port = self.display.port
        return port

    def start(self) -> None:
        from component.grasp_generation.service import GraspGeneration
        from component.grasp_simulation.grasp_execution import GraspExecution
        from component.grasp_simulation.robot_operation import RobotOperation
        from component.grasp_simulation.service import RobotSimulation
        from component.head_preview.service import DepthPreview
        from component.task_execution.service import TaskRunner
        from util.moveit_helper import MoveItHelper
        from util.teleop_helper import TeleopHelper

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
                                          stereo=perception.stereo if perception.source == "stereo" else None,
                                          gpu_lock=self.real.gpu_lock, target=self.head_view_target,
                                          reach_overlay=self.reach_overlay)
        self.depth_preview.start()
        # A click takes the preview's fresh frame (no stereo of its own); the click's models load now,
        # not at the first click.
        self.real.selector.latest_frame = self.depth_preview.fresh_frame
        threading.Thread(target=self.real.selector.warm_up, name="click_warm_up", daemon=True).start()

    def head_view_target(self) -> dict:
        """What the task pages' head view draws: the target (its mask) and where pick-place puts it."""
        target = {**self.real.target, "mask": self.real.target_mask, "place": self.real.place.status()}
        return target

    def reach_overlay(self, frame: dict) -> dict:
        """Where each arm can grasp, and the midline between the shoulders, in the head image."""
        overlay = {**{side: reach.overlay(frame) for side, reach in self.real.reaches.items()},
                   "midline": self.real.midline_pixels(frame)}
        return overlay

    # --- robot operation (simulation) ---

    def joint_state(self) -> dict[str, float]:
        state = self.robot.joint_state()
        return state

    def robot_poses(self) -> dict[str, dict]:
        poses = self.robot.poses()
        return poses

    def move_robot(self, arm: str, part: str, pose: str) -> bool:
        moved = self.robot.move(arm, part, pose)
        return moved

    # --- grasp generation (simulation) ---

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
        result = {"index": index, "score": round(candidate.score, 3),
                  "position": [round(float(v), 3) for v in candidate.pose[:3, 3]]}
        return result

    # --- grasp execution (simulation) ---

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

    def execution_status(self) -> dict:
        """busy covers the whole run including the video render, so the page never shows the previous
        clip as this run's result."""
        status = {"running": self.busy, "progress": self.execution.progress, "result": self.last_result}
        return status

    # --- the real-robot task (物体抓取, 抓取摆放) ---

    def task_status(self) -> dict:
        status = self.real.status()
        return status

    def run_stage(self, name: str) -> dict:
        """Blocks until the step is done."""
        result = self.real.run(name)
        return result

    def run_rest(self) -> dict:
        """Returns at once; the page follows the progress in the status."""
        result = self.real.run_all()
        return result

    def start_task(self, action: str, ignore_left: bool = False) -> dict:
        """From step 0, the whole of action; returns at once."""
        result = self.real.start(action, ignore_left)
        return result

    def reset_task(self, action: str) -> dict:
        self.real.reset(action)
        status = self.real.status()
        return status

    def choose_target(self, prompt: str | None, point: list[float] | None, clear: bool) -> dict:
        """The task pages' object: a click (point), a name (prompt), or clear."""
        result = self.real.set_target(prompt, point, clear)
        return result

    def choose_place(self, kind: str | None, point: list[float] | None, by: str) -> dict:
        """抓取摆放's place: a kind alone, a desk point or a point in a box clicked, or a box by words (by "words")."""
        result = self.real.set_place(kind, point, by)
        return result

    def choose_arm(self, side: str) -> dict:
        """物体抓取's arm: auto (the nearer shoulder), left or right."""
        result = self.real.set_arm(side)
        return result

    def stop(self) -> dict:
        """The robot process's stop: the base halts and every part freezes; the running step then fails."""
        result = self.real.teleop.stop()
        return result

    def plan_image(self) -> Path | None:
        path = self.real.state.plan_image
        return path

    def align_image(self) -> Path | None:
        path = self.real.state.align_image
        return path

    def depth_preview_jpeg(self, kind: str) -> bytes | None:
        """The live head views: "raw", "refined" (depth), "detect", "target", "place"; None until the first one."""
        picture = self.depth_preview.latest(kind)
        return picture

    def depth_preview_valid(self, kind: str) -> float | None:
        """The share of pixels with a depth in the newest depth picture of kind; None for the other pictures."""
        valid = self.depth_preview.valid.get(kind)
        return valid

    # --- the task pages' chat and voice ---

    def task_chat(self):
        if self.chat is None:
            from component.task_chat.service import TaskChat
            from component.voice_dialog.service import VoiceDialog

            self.dialog = VoiceDialog()
            self.chat = TaskChat(self.real, self.dialog)
        return self.chat

    def chat_turn(self, task: str, text: str, history: list[dict], awaiting: str) -> dict:
        """One turn of a task page's chat: the task's choices made, confirmed, the run started;
        asked back when not sure."""
        result = self.task_chat().turn(task, text, history, awaiting)
        return result

    def chat_next(self, task: str) -> dict:
        """What a task page's chat says after the page itself chose (a click, a tapped answer)."""
        result = self.task_chat().next(task)
        return result

    def transcribe(self, pcm: bytes) -> dict:
        """The page's recording (16 kHz mono int16 PCM) -> its words (BytePlus ASR)."""
        if len(pcm) < ByteplusAsrConfig.SAMPLE_RATE * 2 * 0.3:
            raise ValueError("录音太短")
        if self.asr is None:
            from util.byteplus_asr_helper import ByteplusAsrHelper

            self.asr = ByteplusAsrHelper()
        try:
            text = self.asr.transcribe(pcm)
        except Exception as error:
            raise RuntimeError(f"语音识别失败：{error}") from error
        if not text:
            raise ValueError("没听清，请再说一次")
        result = {"text": text, "seconds": round(len(pcm) / 2 / ByteplusAsrConfig.SAMPLE_RATE, 1)}
        return result

    def speech(self, text: str) -> bytes:
        """text spoken (BytePlus TTS), as WAV for the page to play."""
        text = text.strip()[:200]
        if not text:
            raise ValueError("没有要说的")
        if self.tts is None:
            from util.byteplus_tts_helper import ByteplusTtsHelper

            self.tts = ByteplusTtsHelper()
        try:
            pcm = self.tts.synthesize(text, voice=ByteplusTtsConfig.REPLY_VOICE)
        except Exception as error:
            raise RuntimeError(f"语音合成失败：{error}") from error
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(ByteplusTtsConfig.SAMPLE_RATE)
            out.writeframes(pcm)
        wav = buffer.getvalue()
        return wav

    def claim_speaker(self, client: str) -> dict:
        self.speaker_client = client
        speaker = {"client": self.speaker_client}
        return speaker

    # --- 抓取规划 ---

    def planning_report(self) -> dict:
        """The last plan: its candidates' funnel, their re-ranking, the pictures there are; ready False
        before start()."""
        if self.real is None:
            return {"ready": False}
        from util.demo_preferences import MODES

        planning, preferences = self.real.planning, self.real.planning.preferences
        status = self.real.status()
        report = {"ready": True, "modes": list(MODES), "options": preferences.options, "report": preferences.report,
                  "stats": planning.stats, "action": status["action"], "next": status["next"],
                  "running": status["running"] or status["auto"], "plan_image": status["plan_image"],
                  "align_image": status["align_image"], "align": status["align"],
                  "semantic_image": bool(preferences.report.get("observation_image")), "target": status["target"]}
        return report

    def planning_records(self, limit: int) -> dict:
        """The evaluation records: the latest attempts first, and the success counts."""
        if self.real is None:
            return {"recent": [], "summary": {}}
        records = {"recent": self.real.records.recent(limit), "summary": self.real.records.summary()}
        return records

    def set_ranking(self, options: dict) -> dict:
        """The next plan's candidate ranking (util/demo_preferences), before planning only."""
        from component.task_execution.busy_error import TaskBusyError

        real = self.real
        if not real.gpu_lock.acquire(blocking=False):
            raise TaskBusyError("GPU 正在使用，稍后设置算法")
        try:
            if real.running or real.auto or real.next > 1:
                raise TaskBusyError("请在规划之前设置算法")
            real.planning.preferences.configure(options)
            result = {"options": real.planning.preferences.options}
            return result
        finally:
            real.gpu_lock.release()

    def semantic_picture(self) -> bytes:
        """The semantic suggestion on the planning frame: the prior's pixel, and the top five ranked."""
        import cv2
        import numpy as np

        preferences = self.real.planning.preferences
        report = preferences.report
        if not report.get("observation_image") or not preferences.directory:
            raise FileNotFoundError("尚未生成语义建议")
        with np.load(Path(preferences.directory.name) / "frame.npz", allow_pickle=False) as archive:
            image = cv2.cvtColor(archive["color"], cv2.COLOR_RGB2BGR)
        pixel = report.get("prior", {}).get("pixel")
        if pixel:
            centre = tuple(np.round(pixel).astype(int))
            cv2.drawMarker(image, centre, (255, 0, 255), cv2.MARKER_CROSS, 28, 3)
            cv2.circle(image, centre, 20, (255, 0, 255), 2)
        for rank, row in enumerate(report.get("ranked", [])[:5]):
            centre = tuple(np.round(row["pixel"]).astype(int))
            cv2.circle(image, centre, 6, (0, 255, 255), 2)
            cv2.putText(image, str(rank + 1), (centre[0] + 8, centre[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (0, 255, 255), 1)
        picture = cv2.imencode(".jpg", image)[1].tobytes()
        return picture

    # --- 相机标定 ---

    def calibration_session(self):
        if self.calibration is None:
            from component.camera_calibration.service import CalibrationSession

            self.calibration = CalibrationSession()
        return self.calibration

    def calibration_results(self) -> dict:
        """The calibrations in use and saved, and the board the page describes."""
        from component.camera_calibration.versions import calibrations
        from config.charuco import CharucoConfig

        board = {"squares": CharucoConfig.SQUARES, "square_mm": CharucoConfig.SQUARE_M * 1000,
                 "marker_mm": CharucoConfig.MARKER_M * 1000, "dictionary": CharucoConfig.DICTIONARY}
        results = {"results": calibrations(), "board": board}
        return results

    def board_picture(self, camera: str) -> tuple[bytes, int, float | None]:
        """The camera's picture with the board's corners drawn, how many, and the reprojection error (px or None)."""
        if self.live_detection is None:
            from component.camera_calibration.live_detection import LiveDetection

            self.live_detection = LiveDetection()
        try:
            picture = self.live_detection.picture(camera)
        except Exception as error:
            raise RuntimeError(f"{camera} 没有画面：{error}") from error
        return picture

    # --- 系统设置 ---

    def system_check(self) -> dict:
        """The five stages, read-only (no torque, no motion); ~0.5 s."""
        from component.system_check.service import SelfCheck

        report = SelfCheck(supervisor=self.supervisor, reaches=self.real.reaches if self.real else None).run()
        return report

    def restart_robot(self) -> dict:
        """The robot process again: it takes over parts that still have torque, so an arm holding stays where it is."""
        logger.warning("页面要求重启机器人进程")
        if not self.supervisor.restart_robot():
            raise RuntimeError("机器人进程没有重启起来：看 log/robot_stdout.log")
        result = {"restarted": True}
        return result

    def devices(self) -> dict:
        from component.device_matching.service import status

        devices = status()
        return devices

    def swap_devices(self) -> dict:
        """Left and right wrist swapped, then the robot process again so the cameras open by the new roles."""
        from component.device_matching.service import swap

        swap()
        self.restart_robot()
        devices = self.devices()
        return devices

    def confirm_devices(self) -> dict:
        from component.device_matching.service import confirm

        confirm()
        devices = self.devices()
        return devices


def demo_application() -> None:
    """Command line: start, pick up the water bottle in the simulator end to end, print where the video went.
    Needs the bringup launch."""
    application = GraspApplication()
    application.start()
    logger.info("viser：http://localhost:{}", application.viewer_port)
    application.move_robot("right", "arm", "ready")
    result = application.generate_grasps("water_bottle")
    logger.info("{}：{} 个抓取，用时 {}s", result["object"], len(result["grasps"]), result["seconds"])
    time.sleep(1.0)
    result = application.execute_grasp()
    logger.info("抓取 {}，升高 {} cm，视频 {}", "成功" if result["grasped"] else "失败", result["lifted_cm"],
                result["video"])


def main() -> None:
    use_utf8_output()
    demo_application()


if __name__ == "__main__":
    main()
