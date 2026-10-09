"""真机任务的步骤机：一步一个按钮，或者一次跑完。物体抓取：准备 → 检测规划 → 到瓶子后方 → 腕部相机对准 → 张开 → 伸进去 →
闭合 → 抬起 → 放回 → 松开 → 退出 → 回起始位；抓取摆放和瓶子右移的步骤见 tasks.py。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

import numpy as np
from loguru import logger

from component.calibration.check import CalibrationCheck
from component.perception.target import DEFAULT_TARGET, TargetSelector
from component.planning.arm import ARMS, RIGHT, Arm
from component.planning.arm_choice import ArmChoice
from component.planning.box_placement import BoxPlacement
from component.planning.grasp_planning import RealGraspPlanning, RealScene
from component.task.checks import TaskChecks
from component.task.motion import ArmMotion
from component.task.tasks import ACTIONS, JAWS, SEGMENTS
from component.task.wrist_alignment import WristAlignment
from config.planning import RealPlanningConfig
from config.settings import PathConfig, use_utf8_output
from config.task import MoveRightConfig, RealRobotConfig, WristAlignConfig
from util.joint_mapping_helper import JointMappingHelper
from util.moveit_helper import MoveItHelper
from util.speech_helper import SpeechHelper
from util.teleop_helper import TeleopHelper


class TaskRunner:
    """MoveIt runs each planned segment on the mock hardware and the bridge makes the real arm follow it (motion.py); a
    step ends when the real arm has caught up. A stop on the teleop side (Esc / its button) fails the running step and
    ends the sequence; the page's torque switch then takes the arm back to the rest pose."""

    def __init__(self, moveit: MoveItHelper, teleop: TeleopHelper, **kwargs) -> None:
        self.moveit = moveit
        self.teleop = teleop
        self.mapping = JointMappingHelper()
        self.planning = RealGraspPlanning(teleop, prediction=kwargs.get("prediction"))
        self.check = CalibrationCheck(teleop)
        self.alignment = WristAlignment(teleop, self.planning.perception.detector)
        self.box = BoxPlacement(self.planning)
        # Held for the whole planning step: cuRobo records CUDA graphs there, and any other GPU work meanwhile broke the
        # recording and left the process's CUDA unusable (2026-10-06; the live preview's labelling takes it too).
        self.gpu_lock = threading.Lock()
        self.motion = ArmMotion(moveit, teleop, self.planning.guard, self.report)
        self.checks = TaskChecks(self.motion, self.planning, self.check, teleop, SpeechHelper(), self.report)
        # Which arm (the nearer shoulder) and both arms' reach maps, solved (or read from disk) in the background.
        self.choice = ArmChoice()
        self.selector = TargetSelector(self.planning, teleop, self.choice, self.gpu_lock, self.report)
        self.next = 0
        self.running = False
        # A full run is going (run_all): single steps are refused meanwhile.
        self.auto = False
        self.failed = False
        self.progress: list[str] = []
        self.plan_image: Path | None = None
        self.align_image: Path | None = None
        self.start_jaw = 0.0
        self.scene: RealScene | None = None
        self.grasp = None
        # Between the bottle and the fixed finger at the alignment's last look (m).
        self.align_gap = 0.0
        # ACTIONS' key; a grasp stops at planning when the bottle stands too far left, unless ignore_left (the user
        # chose to grasp anyway).
        self.action = "grasp"
        self.ignore_left = False
        # Set when a grasp stopped for that: the bottle's axis y and how far the move would take it right (m).
        self.too_left: dict | None = None
        # How far the shift step turns the bottle right (m), set at planning.
        self.shift_m = 0.0
        # The page's arm choice for the interactive grasp: None = the nearer shoulder (ArmConfig.CHOICE), else a side.
        self.arm_override: str | None = None
        # Each finished step's duration (s), and the wrist camera's last look at the alignment (cm), for the page.
        self.stage_seconds: dict[str, float] = {}
        self.align_info: dict | None = None

    # --- what the page sees ---

    @property
    def stages(self) -> tuple[tuple[str, str], ...]:
        stages = ACTIONS[self.action]
        return stages

    @property
    def arm(self) -> Arm:
        arm = self.motion.arm
        return arm

    @property
    def target(self) -> dict:
        target = self.selector.target
        return target

    @property
    def target_mask(self) -> np.ndarray | None:
        mask = self.selector.mask
        return mask

    @property
    def reaches(self) -> dict:
        reaches = self.choice.reaches
        return reaches

    def midline_pixels(self, frame: dict) -> np.ndarray | None:
        pixels = self.choice.midline_pixels(frame)
        return pixels

    def report(self, text: str) -> None:
        self.progress.append(f"{time.strftime('%H:%M:%S')} {text}")
        logger.info("真机抓取：{}", text)

    def status(self) -> dict:
        status = {"stages": [{"name": name, "label": label} for name, label in self.stages], "next": self.next,
                  "running": self.running, "auto": self.auto, "failed": self.failed, "progress": self.progress[-40:],
                  "plan_image": self.plan_image is not None, "align_image": self.align_image is not None,
                  "action": self.action, "too_left": self.too_left, "target": self.target, "arm": self.arm.side,
                  "arm_override": self.arm_override or "auto", "stage_seconds": self.stage_seconds, "align": self.align_info}
        return status

    # --- choosing ---

    def reset(self, action: str = "grasp", ignore_left: bool = False) -> None:
        """Back to step 0 (after a failure, or to grasp again) of action (ACTIONS); not while a step or a full run
        is going."""
        if self.running or self.auto:
            return
        self.action = action
        self.ignore_left = ignore_left
        self.too_left = None
        self.shift_m = 0.0
        self.next = 0
        self.failed = False
        self.motion.segments = {}
        self.plan_image = None
        self.align_image = None
        self.motion.pan_offset = 0.0
        self.align_gap = 0.0
        self.stage_seconds = {}
        self.align_info = None
        self.use_arm(RIGHT)
        self.apply_target()
        self.report("流程重置：从「准备」开始" + ("（把瓶子往右移）" if action == "move_right" else "（照常抓取，不看是否太靠左）" if ignore_left else ""))

    def apply_target(self) -> None:
        """Perception and the wrist camera look for the chosen object in the interactive grasp and in pick-place (both
        pages let the user pick it); every other task for the nearest of the default prompt."""
        chosen = self.target if self.action in ("pick_target", "place_in_box") else DEFAULT_TARGET
        self.planning.perception.prompts = (chosen["prompt"],)
        self.planning.perception.point = tuple(chosen["point"]) if chosen["point"] else None
        self.planning.perception.point_area = None
        self.alignment.prompts = (chosen["prompt"],)

    def set_target(self, prompt: str | None = None, point: list[float] | None = None, clear: bool = False, side: str | None = None) -> dict:
        """The task pages' choice (perception/target.py): a click, a name, a name on one side of the desk, or clear; not
        while a step or a full run is going."""
        if self.running or self.auto:
            result = {"ok": False, "error": "正在执行，等这一轮结束再选"}
            return result
        result = self.selector.choose_side(prompt, side) if prompt and side in ("left", "right") else self.selector.choose(prompt, point, clear)
        if result["ok"]:
            self.apply_target()
        return result

    def set_arm(self, side: str) -> dict:
        """The page's arm choice for the interactive grasp: "auto" (the nearer shoulder), "left" or "right"."""
        if self.running or self.auto:
            result = {"ok": False, "error": "正在执行，等这一轮结束再选"}
            return result
        self.arm_override = None if side == "auto" else side
        self.report("手臂：" + ("自动（离哪边肩膀近用哪只手）" if side == "auto" else ARMS[side].label))
        result = {"ok": True, "arm": self.arm_override or "auto"}
        return result

    def use_arm(self, arm: Arm) -> None:
        """Every arm-specific part follows arm: the motion and its bridge, the wrist camera and its fixed-finger column."""
        self.motion.use_arm(arm)
        self.alignment.camera = arm.wrist_camera
        self.alignment.contact_column = arm.contact_column_px

    # --- running ---

    def run_all(self) -> dict:
        """Every step left, in order, in a thread of its own; then torque off, which walks the robot to its rest pose.
        The first step that fails ends it where it is."""
        if self.running or self.auto or self.failed or self.next >= len(self.stages):
            result = {"ok": False, "error": "正在执行" if self.running or self.auto else "上一步失败，先重置" if self.failed else "已经做完，先重置"}
            return result
        self.auto = True
        threading.Thread(target=self.run_rest, name="real_grasp_run_all", daemon=True).start()
        result = {"ok": True}
        return result

    def run_rest(self) -> None:
        try:
            self.report("完整运行：剩下的步骤依次执行，最后关力矩（停在抓取位）")
            while self.next < len(self.stages):
                if not self.run(self.stages[self.next][0], auto=True)["ok"]:
                    self.report("完整运行停止")
                    if self.too_left:
                        # Nothing moved but the head-down start pose: torque off there (a dock) until the user decides.
                        self.teleop.torque(False)
                    return
            result = self.teleop.torque(False)
            self.report("完整运行结束：停在抓取位，力矩关" if not result.get("cancelled") else f"回停靠位被停止：{result['cancelled']} 的力矩还开着")
        finally:
            self.auto = False

    def run(self, name: str, **kwargs) -> dict:
        """Runs step `name` if it is the next one; blocks until it is done. auto: called by run_all."""
        expected = self.stages[self.next][0] if self.next < len(self.stages) else None
        if self.running or (self.auto and not kwargs.get("auto")) or self.failed or name != expected:
            error = "正在执行" if self.running or self.auto else "上一步失败，先重置" if self.failed else f"下一步应是 {expected}"
            result = {"ok": False, "error": error}
            return result
        self.running = True
        label = dict(self.stages)[name]
        self.report(f"开始：{label}")
        started = time.monotonic()
        ok = False
        motion, checks = self.motion, self.checks
        try:
            if name == "prepare":
                ok = self.prepare()
            elif name == "plan":
                with self.gpu_lock:
                    ok = self.plan()
            elif name == "return":
                # Back along the approach, then the jaw as it was at the start: the arm ends at its rest pose. With the
                # arm out of the way and the head still on the desk, wait until the bottle stands.
                ok = motion.move(*SEGMENTS[name], unwind=True) and motion.jaw(self.start_jaw) and checks.wait_upright(self.scene)
            elif name == "approach":
                # Behind the bottle the arm is stretched out, where a zero error moves the jaw most: look before going in.
                ok = motion.move(*SEGMENTS[name])
                if ok and not self.arm.head_check:
                    self.report(f"{self.arm.label}不用头部相机检查位置（ArmConfig.HEAD_CHECK），靠桌面守卫和腕部相机对准")
                elif ok:
                    corrected = False
                    if RealPlanningConfig.CORRECT_ENABLED:
                        ok, corrected = checks.correct_approach(self.scene, self.grasp)
                    ok = ok and (corrected or checks.check_jaw())
            elif name == "align":
                ok = self.align()
            elif name == "close":
                # Up to the bottle with the fixed finger first, so closing hardly pushes it.
                closer = max(0.0, self.align_gap - WristAlignConfig.CLOSE_GAP_M)
                self.report(f"右移 {closer * 100:.1f} cm，定指贴近瓶子（留 {WristAlignConfig.CLOSE_GAP_M * 100:.1f} cm）再闭合")
                ok = motion.turn(closer / self.reach()) and motion.jaw(JAWS[name]) and checks.holding(self.start_jaw)
            elif name == "carry":
                # Over the box: the wrist camera's pan correction (for where the bottle stood) goes back out on the
                # way; the box place is where the plan put it.
                ok = motion.move("carry", 1, unwind=True)
                if ok:
                    motion.pan_offset = 0.0
            elif name in ("lower", "back_out"):
                ok = motion.move(name, 1)
            elif name == "home":
                ok = motion.move("home", 1, unwind=True) and motion.jaw(self.start_jaw) and checks.wait_upright(self.scene)
            elif name == "shift":
                # The bottle up in the jaw, the shoulder turns it right; place and the rest follow the turned pan.
                self.report(f"往右移瓶子 {self.shift_m * 100:.1f} cm")
                ok = motion.turn(self.shift_m / self.reach())
            elif name == "release":
                # The bottle leans on the fixed finger (closing pushed it there): the jaw opens and moves left at once.
                ok = motion.release(-WristAlignConfig.RELEASE_CLEAR_M / self.reach())
            elif name in SEGMENTS:
                ok = motion.move(*SEGMENTS[name])
            else:
                ok = motion.jaw(JAWS[name])
        except Exception as error:
            # The step failed (e.g. the robot process was unreachable): the sequence stops, the page says why.
            self.report(f"出错：{error}")
        finally:
            self.running = False
            if motion.bridge.running:
                motion.bridge.stop()
        if ok:
            self.next += 1
            self.stage_seconds[name] = round(time.monotonic() - started, 1)
            self.report(f"完成：{label}")
        else:
            self.failed = True
            self.report(f"失败：{label}（真机停在原地；重置后从准备开始，或用顶栏的力矩开关回停靠位）")
        result = {"ok": ok, "next": self.next}
        return result

    # --- steps ---

    def prepare(self) -> bool:
        """Torque on (only possible at the rest pose), then the grasp start pose, and wait until it is there."""
        if not all(self.teleop.status()["torque"].values()):
            result = self.teleop.torque(True)
            if not result.get("on"):
                self.report(f"开不了力矩：{result.get('away_from_rest') or result.get('error')}")
                return False
        self.teleop.go_to_pose("grasp_start")
        arrived = self.teleop.wait_still()
        self.report("已到抓取位" if arrived else "没走到抓取位")
        return arrived

    def plan(self) -> bool:
        real_scene = self.planning.observe()
        self.scene = real_scene
        perception = self.planning.perception
        if perception.point is not None:
            # Later looks (the upright check) follow the object where it was found, not where the click landed.
            rows, cols = np.nonzero(real_scene.target.detection.mask)
            perception.point = (float(cols.mean()), float(rows.mean()))
            perception.point_area = int(real_scene.target.detection.mask.sum())
            if self.action == "pick_target":
                self.selector.mask = real_scene.target.detection.mask
        self.motion.pan_offset = 0.0
        self.align_gap = 0.0
        self.align_image = None
        axis_y = float(real_scene.placed.mesh.bounds.mean(axis=0)[1])
        shift = min(max(axis_y - MoveRightConfig.HOME_Y, 0.0), MoveRightConfig.MAX_SHIFT_M)
        if self.action == "grasp" and not self.ignore_left and axis_y > MoveRightConfig.LEFT_LIMIT_Y:  # not pick_target
            # The user decides: move it right first, or grasp anyway.
            self.too_left = {"axis_y": round(axis_y, 3), "shift_m": round(shift, 3)}
            self.report(f"瓶子太靠左（y {axis_y:+.3f} m，超过 {MoveRightConfig.LEFT_LIMIT_Y:+.3f} m），机器人没动："
                        f"要先「把瓶子往右移」{shift * 100:.1f} cm，还是照常抓取？")
            return False
        self.shift_m = shift if self.action == "move_right" else 0.0
        box = None
        if self.action == "place_in_box":
            box = self.box.observe(real_scene)
            if box is None:
                self.report("头部画面里没找到开口朝上的盒子")
                return False
            self.report(f"盒子：中心 {np.round(box.centre, 3).tolist()}，{box.half[0] * 200:.1f} x {box.half[1] * 200:.1f} cm，"
                        f"盒沿高 {(box.rim_z - box.table_z) * 100:.1f} cm，盒底高 {(box.floor_z - box.table_z) * 100:.1f} cm")
        bounds = real_scene.placed.mesh.bounds
        axis = bounds.mean(axis=0)[:2]
        sides = [self.arm_override] if self.arm_override and self.action == "pick_target" else self.choice.sides(self.action)
        reach = self.choice.advice(float(axis[0]), float(axis[1]), float(bounds[1, 2] - real_scene.scene.table_top_z),
                                   real_scene.scene.table_top_z, sides)
        # The arms the reach maps say reach it, preferred first; if none, the first allowed one tries anyway.
        result = None
        for side in reach["arms"] or sides[:1]:
            self.use_arm(ARMS[side])
            if len(sides) > 1:
                self.report(f"用{self.arm.label}抓：{reach['text']}")
            result = self.planning.plan(real_scene, self.arm)
            if result is not None:
                break
        if result is None:
            self.report(f"没有规划出{self.arm.label}的抓取" + ("" if reach["ok"] else f"：{reach['text']}"))
            return False
        grasp, segments = result
        self.motion.segments = segments
        self.grasp = grasp
        self.plan_image = self.planning.draw(real_scene, grasp, segments, self.check)
        if box is not None:
            placed = self.box.plan(real_scene, grasp, segments, box)
            if placed is None:
                self.report("盒子测出来放不下瓶子（可能没测准，看头部画面里盒子的框）" if self.box.failure == "small"
                            else "没有规划出放进盒子的路径（右臂够不到盒子里能放的点）")
                return False
            segments.update(placed["segments"])
            self.report(f"放到盒子里：瓶轴到 {np.round(placed['point'], 3).tolist()}，夹爪转 {placed['yaw_deg']:+.1f}°，"
                        f"放下时指尖中心 {np.round(placed['place_tool'], 3).tolist()}")
        self.start_jaw = float(self.mapping.to_urdf({self.arm.part: self.motion.real_arm()})[self.arm.jaw])
        self.motion.sync_mock()
        self.report(f"抓取 #{grasp.index}（分数 {grasp.score:.3f}），指尖中心 {np.round(grasp.tool_pose[:3, 3], 3).tolist()}；"
                    + "，".join(f"{name} {len(segment.positions) * segment.dt * RealRobotConfig.TIME_SCALE:.1f}s"
                               for name, segment in segments.items()))
        real = self.planning.to_real(segments)
        rolls = np.concatenate([rows[:, 4] for rows in real.values()])
        self.report("抓取位真机关节（pan, lift, elbow, wrist_flex, wrist_roll）" + str(np.round(real["grasp"][-1], 1).tolist())
                    + f"；全程 wrist_roll {rolls.min():.1f}° ~ {rolls.max():.1f}°（起始 {self.motion.real_arm()['wrist_roll']:.1f}°）")
        return True

    def wrist_look(self, radius: float):
        """The wrist camera's look, up to LOOKS times while it finds nothing: on 2026-10-09 it missed a bottle that the
        same picture showed at 0.84 when looked at again. Each look holds the GPU lock."""
        for look in range(WristAlignConfig.LOOKS):
            with self.gpu_lock:
                view = self.alignment.look(radius)
            if view.shift_m is not None:
                break
            logger.info("{}腕相机第 {} 次没找到 {}", self.arm.label[0], look + 1, self.alignment.prompts)
            time.sleep(WristAlignConfig.LOOK_PAUSE_S)
        return view

    def align(self) -> bool:
        """Jaw closed, behind the bottle: turn the shoulder until the wrist camera has the bottle's left edge
        SIDE_GAP_M right of the fixed finger. Turning by a moves the jaw sideways by a times the distance from the
        shoulder's yaw axis to the bottle."""
        placed = self.scene.placed
        radius = (placed.mesh.bounds[1, 0] - placed.mesh.bounds[0, 0]) / 2
        reach = self.reach()
        for attempt in range(WristAlignConfig.MAX_TRIES + 1):
            view = self.wrist_look(radius)
            self.align_image = self.alignment.draw(view, PathConfig.OUTPUT_DIR / "real_grasp" / "align.jpg")
            if view.shift_m is None:
                self.report(f"{self.arm.label[0]}腕相机里没找到「{self.alignment.prompts[0]}」（看了 {WristAlignConfig.LOOKS} 次）")
                return False
            self.align_info = {"gap_cm": round((WristAlignConfig.SIDE_GAP_M - view.shift_m) * 100, 1), "shift_cm": round(view.shift_m * 100, 1)}
            self.report(f"{self.arm.label[0]}腕相机：瓶子左边缘离定指 {(WristAlignConfig.SIDE_GAP_M - view.shift_m) * 100:+.1f} cm，"
                        f"还要左移 {view.shift_m * 100:+.1f} cm")
            if abs(view.shift_m) <= WristAlignConfig.TOLERANCE_M:
                self.align_gap = WristAlignConfig.SIDE_GAP_M - view.shift_m
                self.report(f"已对准（肩部 pan 共修正 {np.degrees(self.motion.pan_offset):+.1f}°）")
                return True
            if attempt == WristAlignConfig.MAX_TRIES:
                break
            # Left is a smaller pan (real and URDF agree in sign).
            turn = -view.shift_m / reach
            if abs(np.degrees(self.motion.pan_offset + turn)) > WristAlignConfig.MAX_PAN_DEG:
                self.report(f"要修正 {np.degrees(self.motion.pan_offset + turn):+.1f}°，超过 {WristAlignConfig.MAX_PAN_DEG:.0f}°，停下")
                return False
            if not self.motion.turn(turn):
                return False
            time.sleep(WristAlignConfig.SETTLE_S)
        self.report(f"{WristAlignConfig.MAX_TRIES} 次没对准，停在瓶子后方")
        return False

    def reach(self) -> float:
        """From the shoulder's yaw axis to the bottle's axis (m): a pan turn moves the jaw sideways by this much
        per radian."""
        placed = self.scene.placed
        reach = float(np.linalg.norm(placed.mesh.bounds.mean(axis=0)[:2] - np.array(self.arm.shoulder_xy)))
        return reach


def demo_task_runner() -> None:
    """Needs the bringup and the robot process; runs only 'prepare' and 'plan' (the head moves to the grasp start pose,
    the arms stay at rest)."""
    runner = TaskRunner(MoveItHelper(node_name="task_runner_demo"), TeleopHelper())
    for name in ("prepare", "plan"):
        logger.info("{}：{}", name, runner.run(name))
    logger.info("\n{}", "\n".join(runner.progress))


def main() -> None:
    use_utf8_output()
    demo_task_runner()


if __name__ == "__main__":
    main()
