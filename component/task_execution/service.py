"""真机任务的步骤机：一步一个按钮，或者一次跑完。物体抓取：准备 → 检测规划 → 到瓶子后方 → 腕部相机对准 → 张开 → 伸进去 →
闭合 → 抬起 → 放回 → 松开 → 退出 → 回起始位；抓取摆放和瓶子右移的步骤见 stages.py。检测规划在 plan_step.py，
腕部相机对准在 align_step.py，评估记录在 attempt_log.py，步骤之间传的东西在 run_state.py。
页面的选择（抓哪个、放哪里、用哪只手）也经过这里：执行中不能改（TaskBusyError），选不成时抛 ValueError。"""

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

from component.box_placement.service import BoxPlacement
from component.camera_calibration.check import CalibrationCheck
from component.grasp_planning.arm import ARMS, RIGHT, Arm
from component.grasp_planning.arm_choice import ArmChoice
from component.grasp_planning.service import RealGraspPlanning
from component.place_choice.service import PlaceChoice
from component.target_selection.service import DEFAULT_TARGET, TargetSelector
from component.task_execution.align_step import AlignStep
from component.task_execution.attempt_log import AttemptLog
from component.task_execution.busy_error import TaskBusyError
from component.task_execution.checks import TaskChecks
from component.task_execution.motion import ArmMotion
from component.task_execution.plan_step import PlanStep
from component.task_execution.records import GraspRecords
from component.task_execution.run_state import RunState
from component.task_execution.stages import ACTIONS, JAWS, SEGMENTS
from component.task_execution.wrist_alignment import WristAlignment
from config.planning import RealPlanningConfig
from config.settings import use_utf8_output
from config.task import WristAlignConfig
from util.moveit_helper import MoveItHelper
from util.speech_helper import SpeechHelper
from util.teleop_helper import TeleopHelper

# The tasks whose object the page chooses (every other one takes the nearest of the default prompt).
CHOSEN_ACTIONS = ("pick_target", "place_in_box", "place_at_spot")


class TaskRunner:
    """MoveIt runs each planned segment on the mock hardware and the bridge makes the real arm follow it (motion.py); a
    step ends when the real arm has caught up. A stop on the teleop side (Esc / its button) fails the running step and
    ends the sequence; the page's torque switch then takes the arm back to the rest pose."""

    def __init__(self, moveit: MoveItHelper, teleop: TeleopHelper, **kwargs) -> None:
        self.moveit = moveit
        self.teleop = teleop
        self.planning = RealGraspPlanning(teleop, prediction=kwargs.get("prediction"))
        self.check = CalibrationCheck(teleop)
        self.alignment = WristAlignment(teleop, self.planning.perception.detector)
        # Held for the whole planning step: cuRobo records CUDA graphs there, and any other GPU work meanwhile broke the
        # recording and left the process's CUDA unusable (2026-10-06; the live preview's labelling takes it too).
        self.gpu_lock = threading.Lock()
        self.motion = ArmMotion(moveit, teleop, self.planning.guard, self.report)
        self.checks = TaskChecks(self.motion, self.planning, self.check, teleop, SpeechHelper(), self.report)
        # Which arm (the nearer shoulder) and both arms' reach maps, solved (or read from disk) in the background.
        self.choice = ArmChoice()
        self.selector = TargetSelector(self.planning, teleop, self.choice, self.gpu_lock, self.report)
        # 抓取摆放's where it goes (任务步骤 ②).
        self.place = PlaceChoice(self.selector, self.report)
        self.planner = PlanStep(self.planning, self.choice, BoxPlacement(self.planning), self.selector, self.place,
                                self.motion, self.check, self.use_arm, self.report)
        self.aligner = AlignStep(self.alignment, self.motion, self.gpu_lock, self.report)
        # The evaluation records (抓取规划).
        self.records = GraspRecords()
        self.log = AttemptLog(self.records, self.planning)
        self.state = RunState()
        self.next = 0
        self.running = False
        # A full run is going (run_all): single steps are refused meanwhile.
        self.auto = False
        self.failed = False
        self.progress: list[str] = []
        # ACTIONS' key, 物体抓取 (pick_target) until a page asks for another. The old right-arm "grasp" stops at
        # planning when the bottle stands too far left, unless ignore_left (the user chose to grasp anyway).
        self.action = "pick_target"
        self.ignore_left = False
        # The page's arm choice for the interactive grasp: None = the nearer shoulder (ArmConfig.CHOICE), else a side.
        self.arm_override: str | None = None

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
        state = self.state
        status = {"stages": [{"name": name, "label": label} for name, label in self.stages], "next": self.next,
                  "running": self.running, "auto": self.auto, "failed": self.failed, "progress": self.progress[-40:],
                  "plan_image": state.plan_image is not None, "align_image": state.align_image is not None,
                  "action": self.action, "too_left": state.too_left, "target": self.target, "arm": self.arm.side,
                  "arm_override": self.arm_override or "auto", "stage_seconds": state.stage_seconds,
                  "align": state.align_info, **self.place.status()}
        return status

    # --- choosing ---

    def ensure_idle(self) -> None:
        if self.running or self.auto:
            raise TaskBusyError("正在执行，等这一轮结束再说")

    def reset(self, action: str = "pick_target", ignore_left: bool = False) -> None:
        """Back to step 0 of action (ACTIONS), after a failure or to do it again."""
        self.ensure_idle()
        self.action = action
        self.ignore_left = ignore_left
        self.state = RunState()
        self.next = 0
        self.failed = False
        self.motion.segments = {}
        self.motion.pan_offset = 0.0
        self.log.drop()
        self.use_arm(RIGHT)
        self.apply_target()
        self.report("流程重置：从「准备」开始" + ("（把瓶子往右移）" if action == "move_right"
                                         else "（照常抓取，不看是否太靠左）" if ignore_left else ""))

    def apply_target(self) -> None:
        """Perception and the wrist camera look for the chosen object in the tasks the page chooses it for; every
        other task for the nearest of the default prompt."""
        chosen = self.target if self.action in CHOSEN_ACTIONS else DEFAULT_TARGET
        perception = self.planning.perception
        perception.prompts = (chosen["prompt"],)
        perception.point = tuple(chosen["point"]) if chosen["point"] else None
        perception.point_area = None
        # A box the user corrected in the chat (a cap added): SAM at planning takes it instead of the name's box.
        perception.box = tuple(chosen["box"]) if chosen.get("box") else None
        self.alignment.prompts = (chosen["prompt"],)

    def set_target(self, prompt: str | None = None, point: list[float] | None = None, clear: bool = False) -> dict:
        """The task pages' object (target_selection): a click, a name (at a click or the nearest), or clear."""
        self.ensure_idle()
        selector = self.selector
        target = (selector.clear() if clear else selector.choose_named(prompt, point) if prompt
                  else selector.choose_at(point))
        self.apply_target()
        result = {"target": target}
        return result

    def include_part(self, part: str | None) -> dict:
        """The target grown by the part it missed (a cap)."""
        self.ensure_idle()
        target = self.selector.include(part)
        self.apply_target()
        result = {"target": target}
        return result

    def set_place(self, kind: str | None, point: list[float] | None = None, by: str = "click",
                  box: dict | None = None) -> dict:
        """抓取摆放's place: a kind alone (None: not chosen; where exactly is clicked next), a desk point clicked, a
        point clicked in a box, or a box by words (a tapped answer's point, or the chat's box from selector.boxes())."""
        self.ensure_idle()
        place = self.place
        if box is not None:
            status = place.box_by_words(box)
        elif point is None:
            status = place.choose(kind)
        elif kind == "spot":
            status = place.click_spot(point)
        else:
            status = place.click_box(point) if by == "click" else place.box_by_words_at(point)
        return status

    def set_arm(self, side: str) -> dict:
        """The page's arm choice for the interactive grasp: "auto" (the nearer shoulder), "left" or "right"."""
        self.ensure_idle()
        self.arm_override = None if side == "auto" else side
        self.report("手臂：" + ("自动（离哪边肩膀近用哪只手）" if side == "auto" else ARMS[side].label))
        result = {"arm": self.arm_override or "auto"}
        return result

    def use_arm(self, arm: Arm) -> None:
        """Every arm-specific part follows arm: the motion and its bridge, the wrist camera and its fixed-finger
        column."""
        self.motion.use_arm(arm)
        self.alignment.camera = arm.wrist_camera
        self.alignment.contact_column = arm.contact_column_px

    # --- running ---

    def start(self, action: str, ignore_left: bool = False) -> dict:
        """From step 0, the whole of action; returns at once, like run_all."""
        self.reset(action, ignore_left)
        result = self.run_all()
        return result

    def run_all(self) -> dict:
        """Every step left, in order, in a thread of its own; then torque off, which walks the robot to its rest pose.
        The first step that fails ends it where it is."""
        self.ensure_idle()
        if self.failed or self.next >= len(self.stages):
            raise ValueError("上一步失败，先重置" if self.failed else "已经做完，先重置")
        self.auto = True
        threading.Thread(target=self.run_rest, name="real_grasp_run_all", daemon=True).start()
        result = {"started": True}
        return result

    def run_rest(self) -> None:
        try:
            self.report("完整运行：剩下的步骤依次执行，最后关力矩（停在抓取位）")
            while self.next < len(self.stages):
                if not self.run(self.stages[self.next][0], auto=True)["done"]:
                    self.report("完整运行停止")
                    if self.state.too_left:
                        # Nothing moved but the head-down start pose: torque off there (a dock) until the user decides.
                        self.teleop.torque(False)
                    return
            result = self.teleop.torque(False)
            self.report("完整运行结束：停在抓取位，力矩关" if not result.get("cancelled")
                        else f"回停靠位被停止：{result['cancelled']} 的力矩还开着")
        finally:
            self.auto = False

    def run(self, name: str, **kwargs) -> dict:
        """Runs step `name` if it is the next one; blocks until it is done -> {"done", "next"}.
        auto: called by run_all."""
        if self.running or (self.auto and not kwargs.get("auto")):
            raise TaskBusyError("正在执行")
        expected = self.stages[self.next][0] if self.next < len(self.stages) else None
        if self.failed or name != expected:
            raise ValueError("上一步失败，先重置" if self.failed else f"下一步应是 {expected}")
        self.running = True
        label = dict(self.stages)[name]
        self.report(f"开始：{label}")
        started = time.monotonic()
        done = False
        try:
            done = self.step(name)
        except Exception as error:
            # The step failed (e.g. the robot process was unreachable): the sequence stops, the page says why.
            self.report(f"出错：{error}")
        finally:
            self.running = False
            if self.motion.bridge.running:
                self.motion.bridge.stop()
        if done:
            self.next += 1
            self.state.stage_seconds[name] = round(time.monotonic() - started, 1)
            self.report(f"完成：{label}")
        else:
            self.failed = True
            self.report(f"失败：{label}（真机停在原地；重置后从准备开始，或用顶栏的力矩开关回停靠位）")
        self.log.record(name, done, self.next >= len(self.stages), self.state, self.progress, self.attempt_base)
        result = {"done": done, "next": self.next}
        return result

    def step(self, name: str) -> bool:
        """What step name does; True when it is done."""
        motion, checks, state = self.motion, self.checks, self.state
        if name == "prepare":
            done = self.prepare()
        elif name == "plan":
            with self.gpu_lock:
                done = self.planner.run(state, self.action, self.ignore_left, self.arm_override)
        elif name == "return":
            # Back along the approach, then the jaw as it was at the start: the arm ends at its rest pose. With the
            # arm out of the way and the head still on the desk, wait until the bottle stands.
            done = (motion.move(*SEGMENTS[name], unwind=True) and motion.jaw(state.start_jaw)
                    and checks.wait_upright(state.scene))
        elif name == "approach":
            # Behind the bottle the arm is stretched out, where a zero error moves the jaw most: look before going in.
            done = motion.move(*SEGMENTS[name])
            if done and not self.arm.head_check:
                self.report(f"{self.arm.label}不用头部相机检查位置（ArmConfig.HEAD_CHECK），靠桌面守卫和腕部相机对准")
            elif done:
                corrected = False
                if RealPlanningConfig.CORRECT_ENABLED:
                    done, corrected = checks.correct_approach(state.scene, state.grasp)
                done = done and (corrected or checks.check_jaw())
        elif name == "align":
            done = self.aligner.run(state)
        elif name == "close":
            # Up to the bottle with the fixed finger first, so closing hardly pushes it.
            closer = max(0.0, state.align_gap - WristAlignConfig.CLOSE_GAP_M)
            self.report(f"右移 {closer * 100:.1f} cm，定指贴近瓶子"
                        f"（留 {WristAlignConfig.CLOSE_GAP_M * 100:.1f} cm）再闭合")
            done = (motion.turn(closer / self.reach()) and motion.jaw(JAWS[name])
                    and checks.holding(state.start_jaw))
        elif name == "carry":
            # Over the place: the wrist camera's pan correction (for where the bottle stood) goes back out on the way.
            done = motion.move("carry", 1, unwind=True)
            if done:
                motion.pan_offset = 0.0
        elif name in ("lower", "back_out"):
            done = motion.move(name, 1)
        elif name == "home":
            # Looked for where it was put (it is gone from where it stood: a fallen-bottle call over one standing in
            # the box, 10-10).
            done = (motion.move("home", 1, unwind=True) and motion.jaw(state.start_jaw)
                    and checks.wait_upright(state.scene, state.placed_uv))
        elif name == "shift":
            # The bottle up in the jaw, the shoulder turns it right; place and the rest follow the turned pan.
            self.report(f"往右移瓶子 {state.shift_m * 100:.1f} cm")
            done = motion.turn(state.shift_m / self.reach())
        elif name == "release":
            # The bottle leans on the fixed finger (closing pushed it there): the jaw opens and moves left at once.
            done = motion.release(-WristAlignConfig.RELEASE_CLEAR_M / self.reach())
        elif name in SEGMENTS:
            done = motion.move(*SEGMENTS[name])
        else:
            done = motion.jaw(JAWS[name])
        return done

    def reach(self) -> float:
        reach = self.state.reach(self.arm.shoulder_xy)
        return reach

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

    def attempt_base(self) -> dict:
        """What was asked, for the evaluation record."""
        target = self.target
        base = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": self.action, "arm": self.arm.side,
                "arm_choice": self.arm_override or "auto",
                "target": {key: target.get(key) for key in ("label", "prompt", "point", "by")},
                "place_spot": self.place.spot}
        return base


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
