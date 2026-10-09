"""真机抓取执行：一步一个按钮。准备 → 检测规划 → 到瓶子后方 → 腕部相机对准 → 张开 → 伸进去 → 闭合 → 抬起 → 放回 → 松开 → 退出 → 回起始位。"""

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

from component.planning.box_placement import BoxPlacement
from component.planning.arm import ARMS, RIGHT, Arm
from component.task.bridge import ARM_JOINTS, RealRobotBridge
from component.calibration.check import CalibrationCheck, head_camera_pose
from component.planning.grasp_planning import RealGraspPlanning, RealScene
from component.task.wrist_alignment import WristAlignment
from config.perception import PerceptionConfig
from config.planning import ArmConfig, GripperConfig, MotionConfig, ReachMapConfig, RealPlanningConfig, SelectionConfig
from config.settings import PathConfig, use_utf8_output
from config.task import MoveRightConfig, RealRobotConfig, WristAlignConfig
from config.voice import AlertConfig
from util.joint_mapping_helper import JointMappingHelper
from util.motion_plan_helper import JointTrajectory
from util.moveit_helper import MoveItHelper
from util.speech_helper import SpeechHelper
from util.teleop_helper import TeleopHelper

# (step, label) in the only order they may run.
STAGES = (
    ("prepare", "准备：上力矩并到抓取位"), ("plan", "检测并规划"), ("approach", "到瓶子后方"),
    ("align", "腕部相机对准（左移）"), ("open", "张开夹爪"),
    ("grasp", "伸到瓶身中段"), ("close", "闭合夹爪（先右移贴近定指）"), ("lift", "抬起"), ("place", "放回原处"), ("release", "松开夹爪（同时左移让开定指）"),
    ("retreat", "退出"), ("return", "回到起始位"),
)
# The "move the bottle to the right" action: the same grasp, and while the bottle is up the shoulder turns it right,
# back towards MoveRightConfig.HOME_Y (repeated grasps leave it a little further left each time).
MOVE_RIGHT_STAGES = tuple(stage for name, label in STAGES
                          for stage in ([(name, label), ("shift", "往右移瓶子")] if name == "lift" else
                                        [("place", "在右边放下")] if name == "place" else [(name, label)]))
# "Pick and place" (the pick-place page): the same grasp, then the bottle carried over the open box on the desk,
# lowered into it and let go; out and home along their own plans (box_placement).
PLACE_STAGES = tuple(stage for name, label in STAGES if name in ("prepare", "plan", "approach", "align", "open", "grasp", "close", "lift")
                     for stage in [(name, "检测瓶子和盒子并规划" if name == "plan" else label)]) + (
    ("carry", "送到盒子上方"), ("lower", "放进盒子"), ("release", "松开夹爪（同时左移让开定指）"), ("back_out", "抬高后退出"), ("home", "回到起始位"))
# "Interactive grasp" (its own page): the grasp's steps on the object the user picked there, by a click on the head
# image or by name; the other tasks always take the nearest bottle.
ACTIONS = {"grasp": STAGES, "move_right": MOVE_RIGHT_STAGES, "place_in_box": PLACE_STAGES, "pick_target": STAGES}
DEFAULT_TARGET = {"prompt": PerceptionConfig.PROMPTS[0], "point": None, "label": PerceptionConfig.PROMPTS[0], "score": None}
# Motion steps: which planned segment, run forwards or backwards.
SEGMENTS = {"approach": ("approach", 1), "grasp": ("grasp", 1), "lift": ("lift", 1),
            "place": ("lift", -1), "retreat": ("grasp", -1), "return": ("approach", -1)}
# Gripper steps: the jaw's goal (URDF radians); None = back to where it was at the start.
JAWS = {"open": GripperConfig.JAW_OPEN, "close": GripperConfig.JAW_CLOSE, "release": GripperConfig.JAW_OPEN}


class RealGraspExecution:
    """MoveIt runs each planned segment on the mock hardware and the bridge makes the real arm follow it; a step
    ends when the real arm has caught up. Before a step the mock is set to where that step starts and the real arm
    must already be there, so the mock and the real arm never drift apart. A stop on the teleop side (Esc / its
    button) fails the running step and ends the sequence; the teleop page's torque switch then takes the arm back
    to the rest pose."""

    def __init__(self, moveit: MoveItHelper, teleop: TeleopHelper, **kwargs) -> None:
        self.moveit = moveit
        self.teleop = teleop
        self.mapping = JointMappingHelper()
        self.planning = RealGraspPlanning(teleop, prediction=kwargs.get("prediction"))
        self.check = CalibrationCheck(teleop)
        self.bridge = RealRobotBridge(moveit, teleop, guard=self.planning.guard)
        self.alignment = WristAlignment(teleop, self.planning.perception.detector)
        self.box = BoxPlacement(self.planning)
        self.speech = SpeechHelper()
        self.next = 0
        self.running = False
        # A full run is going (run_all): single steps are refused meanwhile.
        self.auto = False
        self.failed = False
        self.progress: list[str] = []
        self.plan_image: Path | None = None
        self.segments: dict[str, JointTrajectory] = {}
        self.start_jaw = 0.0
        self.scene: RealScene | None = None
        # The wrist camera's correction to Rotation (URDF radians), added to every later step; "return" takes it
        # back out along the way.
        self.pan_offset = 0.0
        self.align_image: Path | None = None
        # Between the bottle and the fixed finger at the alignment's last look (m).
        self.align_gap = 0.0
        # Held for the whole planning step: cuRobo records CUDA graphs there, and any other GPU work meanwhile (the live
        # depth preview's LingBot) broke the recording and left the process's CUDA unusable (2026-10-06).
        self.gpu_lock = threading.Lock()
        # "grasp" or "move_right" (ACTIONS); a grasp stops at planning when the bottle stands too far left, unless
        # ignore_left (the user chose to grasp anyway).
        self.action = "grasp"
        self.ignore_left = False
        # Set when a grasp stopped for that: the bottle's axis y and how far the move would take it right (m).
        self.too_left: dict | None = None
        # How far the shift step turns the bottle right (m), set at planning.
        self.shift_m = 0.0
        # The interactive grasp page's choice: prompt (what YOLOE looks for), point (u, v) in the head image or None,
        # label / score as the prompt-free model named it at the click. Used only while the task is pick_target.
        self.target = dict(DEFAULT_TARGET)
        # The prompt-free YOLOE that names what a click is on (loaded on the first click), and SAM 3.1 that segments it
        # (also at planning and the later looks while the target has a point).
        self.picker = None
        self.segmenter = None
        if PerceptionConfig.SEGMENT_BY_POINT:
            from util.segment_helper import SegmentHelper

            self.segmenter = SegmentHelper()
        self.planning.perception.segmenter = self.segmenter
        # The target's mask as last segmented (the click, then planning), for the page's head view; None by name.
        self.target_mask = None
        # Where on the desk the right arm can grasp: solved (or read from disk) in the background at start.
        from component.planning.reach_map import ReachMap

        self.reaches = {side: ReachMap(arm) for side, arm in ARMS.items()}
        for reach in self.reaches.values():
            reach.start(ReachMapConfig.TABLE_Z_M)
        # The arm doing the grasp: chosen at planning (pick_target: by the reach maps), the right one otherwise.
        self.arm: Arm = RIGHT

    @property
    def stages(self) -> tuple[tuple[str, str], ...]:
        stages = ACTIONS[self.action]
        return stages

    def report(self, text: str) -> None:
        self.progress.append(f"{time.strftime('%H:%M:%S')} {text}")
        logger.info("真机抓取：{}", text)

    def status(self) -> dict:
        status = {"stages": [{"name": name, "label": label} for name, label in self.stages], "next": self.next,
                  "running": self.running, "auto": self.auto, "failed": self.failed, "progress": self.progress[-40:],
                  "plan_image": self.plan_image is not None, "align_image": self.align_image is not None,
                  "action": self.action, "too_left": self.too_left, "target": self.target}
        return status

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
        self.segments = {}
        self.plan_image = None
        self.align_image = None
        self.pan_offset = 0.0
        self.align_gap = 0.0
        self.use_arm(RIGHT)
        self.apply_target()
        self.report("流程重置：从「准备」开始" + ("（把瓶子往右移）" if action == "move_right" else "（照常抓取，不看是否太靠左）" if ignore_left else ""))

    def apply_target(self) -> None:
        """Perception and the wrist camera look for the pick_target task's chosen object; every other task for the
        nearest of the default prompt."""
        chosen = self.target if self.action == "pick_target" else DEFAULT_TARGET
        self.planning.perception.prompts = (chosen["prompt"],)
        self.planning.perception.point = tuple(chosen["point"]) if chosen["point"] else None
        self.planning.perception.point_area = None
        self.alignment.prompts = (chosen["prompt"],)

    def set_target(self, prompt: str | None = None, point: list[float] | None = None, clear: bool = False) -> dict:
        """The interactive grasp page's choice. A click alone (point): the prompt-free YOLOE names what is there and
        that name is the prompt; a name (prompt), with or without a click: YOLOE looks for that name (English: its text
        encoder is CLIP-like), at the click if one is given, else the nearest. clear: back to the default bottle."""
        if self.running or self.auto:
            result = {"ok": False, "error": "正在执行，等这一轮结束再选"}
            return result
        prompt = (prompt or "").strip()
        if clear:
            self.target = dict(DEFAULT_TARGET)
            self.target_mask = None
        elif point is not None and not prompt:
            with self.gpu_lock:
                # With the planning depth (Fast-FoundationStereo): where the object stands, for the reach check.
                frame = self.planning.perception.head_frame()
                if self.picker is None:
                    from util.detection_helper import PromptFreeHelper

                    self.picker = PromptFreeHelper()
                segmented = self.segmenter.at(frame["color"], point) if self.segmenter is not None else None
                if segmented is not None and segmented.mask.mean() > PerceptionConfig.PICK_MAX_AREA_SHARE:
                    # A click on the desk or the mat: SAM takes the whole of it (2026-10-09).
                    result = {"ok": False, "error": f"点到的是桌面或垫子（占画面 {segmented.mask.mean() * 100:.0f}%），请点在物体上"}
                    return result
                # SAM's object named by the YOLOE detection overlapping it most; without SAM, YOLOE's own at the click.
                named = self.picker.name_of(frame["color"], segmented.mask) if segmented is not None else self.picker.at(frame["color"], point)
            if segmented is None and named is None:
                result = {"ok": False, "error": "点的位置没认出物体：点在物体中间，或者输入名称"}
                return result
            label = named.label if named is not None else "object"
            self.target_mask = (segmented or named).mask
            self.target = {"prompt": label, "point": [round(point[0]), round(point[1])], "label": label,
                           "score": round((segmented or named).score, 2), "by": "SAM 3.1" if segmented is not None else "YOLOE"}
            if named is None:
                self.report("SAM 分割出了物体，但 YOLOE 叫不出它的名字：腕部对准会找不到它")
            standing = self.on_desk(frame, self.target_mask)
            if standing is not None:
                self.target["reach"] = self.reach_advice(*standing, ReachMapConfig.TABLE_Z_M, self.arm_sides("pick_target"))
                if not self.target["reach"]["ok"]:
                    self.report(self.target["reach"]["text"])
        elif prompt:
            self.target_mask = None
            self.target = {"prompt": prompt, "point": [round(point[0]), round(point[1])] if point is not None else None,
                           "label": prompt, "score": None, "by": "YOLOE"}
        else:
            result = {"ok": False, "error": "要么点一下物体，要么输入名称"}
            return result
        self.apply_target()
        where = f"，位置 {tuple(self.target['point'])}" if self.target["point"] else "，取最近的"
        self.report(f"抓取目标：{self.target['label']}{where}")
        result = {"ok": True, "target": self.target}
        return result

    def arm_sides(self, action: str | None = None) -> list[str]:
        """The arms a task may use, preferred first: the interactive grasp per ArmConfig.CHOICE, the rest the right."""
        if (action or self.action) != "pick_target" or ArmConfig.CHOICE == "right":
            return ["right"]
        sides = ["left"] if ArmConfig.CHOICE == "left" else ["right", "left"]
        return sides

    def reach_advice(self, x: float, y: float, height: float, table_z: float, sides: list[str]) -> dict:
        """Which of sides reaches an object standing at (x, y): "arms" lists those that do (preferred first), "ok" if
        any, "text" for the page; if none, the shorter of their suggested moves."""
        answers = {side: self.reaches[side].advice(x, y, height, table_z) for side in sides}
        arms = [side for side in sides if answers[side]["ok"]]
        if len(sides) == 2:
            # The nearer shoulder's arm first (the user's rule), whether or not both reach.
            near = {side: float(np.hypot(x - ARMS[side].shoulder_xy[0], y - ARMS[side].shoulder_xy[1])) for side in sides}
            arms = sorted(arms, key=lambda side: near[side])
            nearer = min(sides, key=lambda side: near[side])
            if arms:
                note = "更近" if arms[0] == nearer else f"抓（{ARMS[nearer].label}更近但够不着）"
                result = {**answers[arms[0]], "arms": arms,
                          "text": f"{ARMS[arms[0]].label}{note}（离左肩 {near['left'] * 100:.0f} cm、右肩 {near['right'] * 100:.0f} cm）"}
                return result
        if arms:
            other = "左手" if arms[0] == "right" else "右手"
            note = f"（{other}够不着）" if len(sides) == 2 else ""
            result = {**answers[arms[0]], "arms": arms, "text": f"{ARMS[arms[0]].label}够得着{note}"}
            return result
        moves = [answer for answer in answers.values() if answer["move"]]
        best = min(moves, key=lambda answer: float(np.hypot(*answer["move"]))) if moves else answers[sides[0]]
        text = best["text"]
        if len(sides) > 1:
            text = text.replace(ARMS["right"].label, "两只手都").replace(ARMS["left"].label, "两只手都")
        result = {**best, "arms": [], "text": text}
        return result

    def midline_y(self, x: float) -> float:
        """The line halfway between the two shoulders (base y at depth x, m): left of it the left shoulder is nearer."""
        a, b = np.array(ARMS["right"].shoulder_xy), np.array(ARMS["left"].shoulder_xy)
        middle = float((b @ b - a @ a - 2 * x * (b[0] - a[0])) / (2 * (b[1] - a[1])))
        return middle

    def midline_pixels(self, frame: dict) -> np.ndarray | None:
        """The halfway line on the desk as head-image pixels, over the rows where either arm reaches; None before the
        grids."""
        right, left = self.reaches["right"], self.reaches["left"]
        if right.reachable is None or left.reachable is None:
            return None
        rows = [i for i in range(len(right.xs)) if (right.reachable[i] | left.reachable[i]).any()]
        if len(rows) < 2:
            return None
        points = np.array([[right.xs[i], self.midline_y(right.xs[i]), ReachMapConfig.TABLE_Z_M] for i in rows])
        pixels = right.project(frame, points)
        return pixels

    def use_arm(self, arm: Arm) -> None:
        """Every arm-specific part follows arm: the bridge, the wrist camera and its fixed-finger column."""
        self.arm = arm
        self.bridge.arm = arm
        self.alignment.camera = arm.wrist_camera
        self.alignment.contact_column = arm.contact_column_px

    def on_desk(self, frame: dict, mask: np.ndarray) -> tuple[float, float, float] | None:
        """Roughly where an object stands (x, y of its axis in base_link) and how tall it is, from its mask: the median of
        its seen points, pushed back by a typical radius away from the camera (only its front is seen)."""
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in status["joints"].items()}
        real["head"] = {"head_motor_1": float(frame["head_joints"][0]), "head_motor_2": float(frame["head_joints"][1])}
        self.planning.urdf.update_cfg(self.mapping.to_urdf(real))
        base_from_camera = head_camera_pose(self.planning.urdf)
        points = self.planning.back_project(frame, base_from_camera, mask)
        points = points[points[:, 2] > ReachMapConfig.TABLE_Z_M + 0.01]
        if len(points) < 50:
            return None
        middle = np.median(points[:, :2], axis=0)
        away = middle - base_from_camera[:2, 3]
        axis = middle + 0.018 * away / max(np.linalg.norm(away), 1e-6)
        height = float(np.percentile(points[:, 2], 98)) - ReachMapConfig.TABLE_Z_M
        return float(axis[0]), float(axis[1]), height

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
        ok = False
        try:
            if name == "prepare":
                ok = self.prepare()
            elif name == "plan":
                with self.gpu_lock:
                    ok = self.plan()
            elif name == "return":
                # Back along the approach, then the jaw as it was at the start: the arm ends at its rest pose. With the
                # arm out of the way and the head still on the desk, wait until the bottle stands.
                ok = self.move(*SEGMENTS[name], unwind=True) and self.jaw(self.start_jaw) and self.wait_upright()
            elif name == "approach":
                # Behind the bottle the arm is stretched out, where a zero error moves the jaw most: look before going in.
                ok = self.move(*SEGMENTS[name])
                if ok and not self.arm.head_check:
                    self.report(f"{self.arm.label}不用头部相机检查位置（ArmConfig.HEAD_CHECK），靠桌面守卫和腕部相机对准")
                elif ok:
                    self.corrected = False
                    if RealPlanningConfig.CORRECT_ENABLED:
                        ok = self.correct_approach()
                    ok = ok and (self.corrected or self.check_jaw())
            elif name == "align":
                ok = self.align()
            elif name == "close":
                # Up to the bottle with the fixed finger first, so closing hardly pushes it.
                closer = max(0.0, self.align_gap - WristAlignConfig.CLOSE_GAP_M)
                self.report(f"右移 {closer * 100:.1f} cm，定指贴近瓶子（留 {WristAlignConfig.CLOSE_GAP_M * 100:.1f} cm）再闭合")
                ok = self.turn(closer / self.reach()) and self.jaw(JAWS[name]) and self.holding()
            elif name == "carry":
                # Over the box: the wrist camera's pan correction (for where the bottle stood) goes back out on the
                # way; the box place is where the plan put it.
                ok = self.move("carry", 1, unwind=True)
                if ok:
                    self.pan_offset = 0.0
            elif name in ("lower", "back_out"):
                ok = self.move(name, 1)
            elif name == "home":
                ok = self.move("home", 1, unwind=True) and self.jaw(self.start_jaw) and self.wait_upright()
            elif name == "shift":
                # The bottle up in the jaw, the shoulder turns it right; place and the rest follow the turned pan.
                self.report(f"往右移瓶子 {self.shift_m * 100:.1f} cm")
                ok = self.turn(self.shift_m / self.reach())
            elif name == "release":
                # The bottle leans on the fixed finger (closing pushed it there): the jaw opens and moves left at once.
                ok = self.release(-WristAlignConfig.RELEASE_CLEAR_M / self.reach())
            elif name in SEGMENTS:
                ok = self.move(*SEGMENTS[name])
            else:
                ok = self.jaw(JAWS[name])
        except Exception as error:
            # The step failed (e.g. the teleop service was unreachable): the sequence stops, the page says why.
            self.report(f"出错：{error}")
        finally:
            self.running = False
            if self.bridge.running:
                self.bridge.stop()
        if ok:
            self.next += 1
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
                self.target_mask = real_scene.target.detection.mask
        self.pan_offset = 0.0
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
        sides = self.arm_sides()
        reach = self.reach_advice(float(axis[0]), float(axis[1]), float(bounds[1, 2] - real_scene.scene.table_top_z),
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
        grasp, self.segments = result
        self.grasp = grasp
        self.plan_image = self.planning.draw(real_scene, grasp, self.segments, self.check)
        if box is not None:
            placed = self.box.plan(real_scene, grasp, self.segments, box)
            if placed is None:
                self.report("盒子测出来放不下瓶子（可能没测准，看头部画面里盒子的框）" if self.box.failure == "small"
                            else "没有规划出放进盒子的路径（右臂够不到盒子里能放的点）")
                return False
            self.segments.update(placed["segments"])
            self.report(f"放到盒子里：瓶轴到 {np.round(placed['point'], 3).tolist()}，夹爪转 {placed['yaw_deg']:+.1f}°，"
                        f"放下时指尖中心 {np.round(placed['place_tool'], 3).tolist()}")
        self.start_jaw = float(self.mapping.to_urdf({self.arm.part: self.real_arm()})[self.arm.jaw])
        self.sync_mock()
        self.report(f"抓取 #{grasp.index}（分数 {grasp.score:.3f}），指尖中心 {np.round(grasp.tool_pose[:3, 3], 3).tolist()}；"
                    + "，".join(f"{name} {len(segment.positions) * segment.dt * RealRobotConfig.TIME_SCALE:.1f}s"
                               for name, segment in self.segments.items()))
        real = self.planning.to_real(self.segments)
        rolls = np.concatenate([rows[:, 4] for rows in real.values()])
        self.report("抓取位真机关节（pan, lift, elbow, wrist_flex, wrist_roll）" + str(np.round(real["grasp"][-1], 1).tolist())
                    + f"；全程 wrist_roll {rolls.min():.1f}° ~ {rolls.max():.1f}°（起始 {self.real_arm()['wrist_roll']:.1f}°）")
        return True

    def wrist_look(self, radius: float):
        """The wrist camera's look, up to LOOKS times while it finds nothing: on 2026-10-09 it missed a bottle that the
        same picture showed at 0.84 when looked at again. Each look holds the GPU lock, apart from the live preview's
        detection on its own thread."""
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
            self.report(f"{self.arm.label[0]}腕相机：瓶子左边缘离定指 {(WristAlignConfig.SIDE_GAP_M - view.shift_m) * 100:+.1f} cm，"
                        f"还要左移 {view.shift_m * 100:+.1f} cm")
            if abs(view.shift_m) <= WristAlignConfig.TOLERANCE_M:
                self.align_gap = WristAlignConfig.SIDE_GAP_M - view.shift_m
                self.report(f"已对准（肩部 pan 共修正 {np.degrees(self.pan_offset):+.1f}°）")
                return True
            if attempt == WristAlignConfig.MAX_TRIES:
                break
            # Left is a smaller pan (real and URDF agree in sign).
            turn = -view.shift_m / reach
            if abs(np.degrees(self.pan_offset + turn)) > WristAlignConfig.MAX_PAN_DEG:
                self.report(f"要修正 {np.degrees(self.pan_offset + turn):+.1f}°，超过 {WristAlignConfig.MAX_PAN_DEG:.0f}°，停下")
                return False
            if not self.turn(turn):
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

    def turn(self, angle: float) -> bool:
        """The shoulder pan by angle (URDF radians), the rest of the arm held."""
        names = list(self.arm.joints)
        current = self.moveit.joint_positions()
        start = np.array([current[name] for name in names])
        end = start.copy()
        end[0] += angle
        steps = max(2, int(abs(np.degrees(angle)) / WristAlignConfig.PAN_SPEED_DEG_S / GripperConfig.JAW_STEP_S))
        rows = np.linspace(start, end, steps)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm, names, rows, GripperConfig.JAW_STEP_S)
        arrived = executed and self.catch_up()
        self.bridge.stop()
        if arrived:
            self.pan_offset += angle
        return arrived

    def move(self, segment_name: str, direction: int, **kwargs) -> bool:
        """unwind: the pan correction goes from all of it at the first row to none at the last (back to the
        planned start); otherwise every row carries it. drift: a further pan turn (URDF radians) spread over the
        rows, kept in the correction afterwards."""
        segment = self.segments[segment_name]
        rows = segment.positions[::direction].copy()
        rows[:, 0] += self.pan_offset * (np.linspace(1.0, 0.0, len(rows)) if kwargs.get("unwind") else 1.0)
        drift = kwargs.get("drift", 0.0)
        rows[:, 0] += drift * np.linspace(0.0, 1.0, len(rows))
        # Every row (densified) clear of the desk by the arm's margin, before anything moves.
        current = self.moveit.joint_positions()
        clear, lowest, _, link = self.planning.guard.check_rows(self.arm, rows, current)
        if not clear:
            self.report(f"桌面守卫：这一步「{segment_name}」会让 {link} 离桌面只剩 {lowest * 100:.1f} cm（要 {self.arm.desk_margin_m * 100:.0f} cm），不执行")
            return False
        if not self.start_at(rows[0]):
            return False
        names = list(self.arm.joints)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm, names, rows, segment.dt * RealRobotConfig.TIME_SCALE)
        arrived = executed and self.catch_up()
        self.bridge.stop()
        if arrived:
            self.pan_offset += drift
        return arrived

    def release(self, turn: float) -> bool:
        """The jaw opens, and once the real one is RELEASE_OPEN_FIRST wider than where it held the bottle (the moving
        finger has let go: judged on the real jaw, which only starts to move when its target passes where the bottle
        stopped it) the rest of the opening goes together with the shoulder pan turned by turn (URDF radians).
        Turning while the finger still pressed the bottle dragged it over (2026-10-06)."""
        arm = list(self.arm.joints)
        held = self.real_arm()["gripper"]
        let_go = held + WristAlignConfig.RELEASE_OPEN_FIRST
        current = self.moveit.joint_positions()
        start = np.array([current[name] for name in arm + [self.arm.jaw]])
        # Phase 1, the jaw alone and RELEASE_FIRST_SLOWDOWN times slower: far enough that the real one gets past let_go.
        first = min(JAWS["release"], self.mapping.to_urdf({self.arm.part: {**self.real_arm(), "gripper": let_go + WristAlignConfig.RELEASE_OPEN_FIRST}})[self.arm.jaw])
        steps = max(2, int(GripperConfig.JAW_MOVE_S * WristAlignConfig.RELEASE_FIRST_SLOWDOWN * (first - start[5])
                           / (JAWS["release"] - start[5]) / GripperConfig.JAW_STEP_S))
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_gripper, [self.arm.jaw], np.linspace(start[5], first, steps).reshape(-1, 1),
                                                  GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        deadline = time.monotonic() + WristAlignConfig.RELEASE_WAIT_S
        while executed and self.bridge.reply.get("present", {}).get("gripper", held) < let_go and time.monotonic() < deadline:
            time.sleep(0.05)
        opened = self.bridge.reply.get("present", {}).get("gripper", held)
        if not executed or opened < let_go or self.bridge.blocked:
            self.report(f"夹爪没有张开（{held:.0f} → {opened:.0f}，要到 {let_go:.0f}），不往左移")
            self.bridge.stop()
            return False
        self.report(f"动指已离开瓶子（夹爪 {held:.0f} → {opened:.0f}），继续张开并左移")
        # Phase 2: the rest of the opening and the turn together.
        current = self.moveit.joint_positions()
        middle = np.array([current[name] for name in arm + [self.arm.jaw]])
        end = middle.copy()
        end[0] += turn
        end[5] = JAWS["release"]
        steps = max(2, int(GripperConfig.JAW_MOVE_S * (end[5] - middle[5]) / (JAWS["release"] - start[5]) / GripperConfig.JAW_STEP_S))
        executed = self.moveit.execute_trajectory(self.arm.moveit_arm_jaw, arm + [self.arm.jaw], np.linspace(middle, end, steps),
                                                  GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        arrived = executed and self.catch_up()
        time.sleep(RealRobotConfig.JAW_SETTLE_S)
        self.bridge.stop()
        if arrived:
            self.pan_offset += turn
        return arrived

    def wait_upright(self) -> bool:
        """The head camera looks at the bottle again: one that fell lies about its diameter high instead of its height
        at planning (or is gone from view). Until it stands, the speaker asks someone at the desk to stand it up, then
        looks again. The robot holds still meanwhile; a stop on the teleop side (Esc / its button, which blocks
        following again) or the arm's torque going off ends the wait and fails the step."""
        planned = float(np.ptp(self.scene.placed.mesh.bounds[:, 2]))
        # Following armed without targets: nothing moves, and a stop shows up as follow_blocked.
        self.teleop.arm_follow()
        calls = 0
        while True:
            try:
                height = float(np.ptp(self.planning.observe().placed.mesh.bounds[:, 2]))
            except Exception as error:
                logger.warning("没看到瓶子：{}", error)
                height = 0.0
            if height >= AlertConfig.UPRIGHT_HEIGHT_SHARE * planned:
                self.report(f"瓶子立着（高 {height * 100:.1f} cm，规划时 {planned * 100:.1f} cm）"
                            + (f"，叫了 {calls} 次后有人扶起" if calls else ""))
                # Someone's hands were just at the desk: thank them and give them time to step back before moving.
                return not calls or self.clear_robot()
            if self.stopped():
                self.report("等人扶瓶子时被停止")
                return False
            calls += 1
            self.report(f"瓶子倒了（高 {height * 100:.1f} cm，规划时 {planned * 100:.1f} cm），第 {calls} 次语音请人扶起")
            self.speech.say(AlertConfig.BOTTLE_FALLEN, wait=True)
            time.sleep(AlertConfig.RECHECK_PAUSE_S)

    def clear_robot(self) -> bool:
        """After someone stood the bottle up: thanks and a warning to step away, CLEAR_WAIT_S, and a second warning
        right before the robot moves (to its rest pose). A stop meanwhile fails the step."""
        self.report(f"谢谢并提醒远离机器人，{AlertConfig.CLEAR_WAIT_S:.0f} s 后再动")
        self.speech.say(AlertConfig.THANKS_STEP_AWAY, wait=True)
        deadline = time.monotonic() + AlertConfig.CLEAR_WAIT_S
        while time.monotonic() < deadline:
            if self.stopped():
                self.report("等人离开时被停止")
                return False
            time.sleep(0.5)
        self.report("提醒：机器人要动了")
        self.speech.say(AlertConfig.ABOUT_TO_MOVE, wait=True)
        clear = not self.stopped()
        if not clear:
            self.report("等人离开时被停止")
        return clear

    def stopped(self) -> bool:
        """A stop on the teleop side (Esc / its button blocks following again) or the arm's torque gone."""
        status = self.teleop.status()
        stopped = status["follow_blocked"] or not status["torque"][self.arm.part]
        return stopped

    def jaw(self, goal: float) -> bool:
        """The jaw over JAW_MOVE_S on the mock, the real one following; a closing jaw stops on the bottle."""
        current = self.moveit.joint_positions()[self.arm.jaw]
        steps = max(2, int(GripperConfig.JAW_MOVE_S / GripperConfig.JAW_STEP_S))
        rows = np.linspace(current, goal, steps).reshape(-1, 1)
        self.bridge.start()
        executed = self.moveit.execute_trajectory(self.arm.moveit_gripper, [self.arm.jaw], rows, GripperConfig.JAW_STEP_S * RealRobotConfig.TIME_SCALE)
        time.sleep(RealRobotConfig.JAW_SETTLE_S)
        ok = executed and not self.bridge.blocked
        self.bridge.stop()
        return ok

    # --- keeping the mock and the real arm together ---

    def holding(self) -> bool:
        """After closing: a jaw that shut on nothing reads about 0-2 (on the spray bottle 14-16). Then, instead of lifting
        and placing nothing (2026-10-09: closed at 1, the bottle pushed ~3 cm aside, then a false fallen-bottle alarm),
        it opens, backs out along the reach-in and the approach to the start, and the step fails."""
        gripper = self.real_arm()["gripper"]
        if gripper > RealRobotConfig.EMPTY_JAW_READING:
            return True
        self.report(f"夹爪合到 {gripper:.0f}（不到 {RealRobotConfig.EMPTY_JAW_READING:.0f}），没夹住：张开，沿原路退回起始位")
        backed = (self.jaw(JAWS["open"]) and self.move(*SEGMENTS["retreat"]) and self.move(*SEGMENTS["return"], unwind=True)
                  and self.jaw(self.start_jaw))
        self.report("已退回起始位" if backed else "没能退回起始位，机器人停在原地")
        return False

    def real_arm(self) -> dict[str, float]:
        arm = {motor: values[0] for motor, values in self.teleop.status()["joints"][self.arm.part].items()}
        return arm

    def head_joints(self, frame: dict) -> dict:
        """Every part's real joints now, the head as it was for frame."""
        joints = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in self.teleop.status()["joints"].items()}
        joints["head"] = {"head_motor_1": float(frame["head_joints"][0]), "head_motor_2": float(frame["head_joints"][1])}
        return joints

    def correct_approach(self) -> bool:
        """Behind the bottle: where the jaw really is, by the head camera (RealPlanningConfig.CORRECT_*). A clear offset t
        is taken out: in the model's frame the bottle and the table sit at -t, so the jaw is sent to the planned
        pre-grasp minus t (the real pre-grasp), and grasp and lift are planned again from there; the approach then runs
        on through that move, so going back retraces it. Afterwards the jaw is looked at again. False stops the grasp."""
        self.corrected = False
        links = [self.arm.link("Fixed_Jaw"), self.arm.link("Wrist_Pitch_Roll")]
        frame = self.teleop.rgbd()
        found = self.check.offset(self.head_joints(frame), frame, links, self.scene.scene.table_top_z)
        if found is None:
            self.report(f"头部相机没看清{self.arm.label}夹爪，不做位置修正")
            return True
        t = found["t"]
        self.report(f"预抓取位置实测：{self.arm.label}夹爪比模型偏 {np.round(t * 100, 1).tolist()} cm（x 前 y 左 z 上；"
                    f"对齐 {found['before_mm']:.0f} → {found['after_mm']:.0f} mm，{found['points']} 点）")
        size = float(np.linalg.norm(t))
        if size < RealPlanningConfig.CORRECT_MIN_M or found["after_mm"] > RealPlanningConfig.CORRECT_FIT_MAX_MM:
            return True
        if size > RealPlanningConfig.CORRECT_MAX_M:
            self.report(f"偏 {size * 100:.1f} cm，超过 {RealPlanningConfig.CORRECT_MAX_M * 100:.0f} cm 不修正，停在瓶子后方")
            return False
        planner = self.planning.planner
        planner.set_obstacles([(name, extents, self.shifted(pose, -t)) for name, extents, pose in self.planning.obstacles])
        grasp = self.shifted(self.grasp.tool_pose, -t)
        backed = grasp.copy()
        backed[:3, 3] -= MotionConfig.APPROACH_DISTANCE * grasp[:3, 2]
        lifted = grasp.copy()
        lifted[2, 3] += MotionConfig.LIFT_HEIGHT
        current = self.segments["approach"].positions[-1]
        fix = planner.plan_segment(current, backed, touching=False)
        reach_in = planner.plan_segment(fix.positions[-1], grasp, touching=True) if fix is not None else None
        lift = planner.plan_segment(reach_in.positions[-1], lifted, touching=True) if reach_in is not None else None
        if lift is None or not all(self.planning.roll_ok(segment.positions) for segment in (fix, reach_in, lift)):
            self.report("按实测位置重新规划失败，停在瓶子后方")
            return False
        self.segments["fix"] = fix
        if not self.move("fix", 1):
            return False
        approach = self.segments["approach"]
        self.segments["approach"] = JointTrajectory(joint_names=approach.joint_names, positions=np.vstack([approach.positions, fix.positions[1:]]), dt=approach.dt)
        self.segments["grasp"], self.segments["lift"] = reach_in, lift
        # Look again: the jaw (model + measured offset) against where the plan wanted it.
        wanted = self.grasp.tool_pose[:3, 3] - MotionConfig.APPROACH_DISTANCE * self.grasp.tool_pose[:3, 2]
        frame = self.teleop.rgbd()
        again = self.check.offset(self.head_joints(frame), frame, links, self.scene.scene.table_top_z)
        if again is None:
            self.report("修正后头部相机没看清夹爪，停在瓶子后方")
            return False
        real_tool = planner.tool_pose(fix.positions[-1])[:3, 3] + again["t"]
        residual = real_tool - wanted
        self.report(f"已按实测修正：现在离预抓取点 {np.round(residual * 100, 1).tolist()} cm（{np.linalg.norm(residual) * 100:.1f} cm）")
        if np.linalg.norm(residual) > RealPlanningConfig.APPROACH_CHECK_M:
            self.report(f"修正后还差 {np.linalg.norm(residual) * 100:.1f} cm，超过 {RealPlanningConfig.APPROACH_CHECK_M * 100:.0f} cm，停在瓶子后方")
            return False
        self.corrected = True
        return True

    @staticmethod
    def shifted(pose: np.ndarray, t: np.ndarray) -> np.ndarray:
        moved = np.array(pose, float).copy()
        moved[:3, 3] += t
        return moved

    def check_jaw(self) -> bool:
        """The right jaw as the head camera sees it against the model at the real joints: the median depth of the
        fixed jaw may differ by APPROACH_CHECK_M (it is the finger that slides past the bottle). The moving jaw is only
        reported: its depth sits ~1.3 cm off the opposite way to the fixed one at every pose (its opening, or its
        model), which no arm or camera error explains. A jaw out of view or without depth is reported, not judged."""
        frame = self.teleop.rgbd()
        status = self.teleop.status()
        joints = {part: {motor: values[0] for motor, values in motors.items()} for part, motors in status["joints"].items()}
        joints["head"] = {"head_motor_1": float(frame["head_joints"][0]), "head_motor_2": float(frame["head_joints"][1])}
        _, pixels = self.check.project(joints, frame)
        errors = self.check.depth_errors({name: pixels[name] for name in (self.arm.link("Fixed_Jaw"), self.arm.link("Moving_Jaw"))}, frame)
        checked = [self.arm.link(link) for link in RealPlanningConfig.APPROACH_CHECK_LINKS]
        judged = {name: measured - model - self.arm.approach_check_offset_m for name, (count, model, measured) in errors.items()
                  if name in checked and count >= 30 and model is not None}
        self.report(f"到位检查（头部相机看{self.arm.label}夹爪，实测 - 模型）：" + "，".join(
            f"{name} {count} 点" + (f" {(measured - model) * 100:+.1f} cm" if model is not None else " 无深度") for name, (count, model, measured) in errors.items()))
        worst = max((abs(value) for value in judged.values()), default=0.0)
        if worst > RealPlanningConfig.APPROACH_CHECK_M:
            self.report(f"偏差 {worst * 100:.1f} cm 超过 {RealPlanningConfig.APPROACH_CHECK_M * 100:.0f} cm，停在瓶子后方")
            return False
        return True

    def sync_mock(self) -> None:
        """Every mock joint to the real robot's (the bridge off): the first step then streams the real arm's own
        pose, and the jaw does not jump to wherever the mock's was."""
        status = self.teleop.status()
        real = {part: {motor: values[0] for motor, values in joints.items()} for part, joints in status["joints"].items()}
        urdf = self.mapping.to_urdf(real)
        current = self.moveit.joint_positions()
        groups = {"right_arm": list(ARM_JOINTS[:5]), "right_gripper": ["Jaw"],
                  "left_arm": [f"{joint}_2" for joint in ARM_JOINTS[:5]], "left_gripper": ["Jaw_2"],
                  "head": ["head_pan_joint", "head_tilt_joint"]}
        for group, names in groups.items():
            self.moveit.execute_trajectory(group, names, np.array([[current[name] for name in names], [urdf[name] for name in names]]), 0.2)
        self.report("mock 已对齐真机")

    def start_at(self, row: np.ndarray) -> bool:
        """The mock goes to the step's first row; the real arm has to be there already (within LOADED_ARRIVED_DEG)."""
        names = list(self.arm.joints)
        current = self.moveit.joint_positions()
        self.moveit.execute_trajectory(self.arm.moveit_arm, names, np.array([[current[name] for name in names], row]), 0.2)
        present = self.real_arm()
        wanted = self.bridge.level(self.mapping.to_real(dict(zip(names, map(float, row))))[self.arm.part], present)
        error = max(abs(present[motor] - wanted[motor]) for motor in wanted)
        # The last step may have settled short under load (catch_up): within LOADED_ARRIVED_DEG it starts from there.
        if error > RealRobotConfig.LOADED_ARRIVED_DEG:
            self.report(f"真机不在这一步的起点（差 {error:.1f}°）")
            return False
        return True

    def catch_up(self) -> bool:
        """After MoveIt finished on the mock, until the real arm is within ARRIVED_DEG of the mock (arm joints), or has
        settled within LOADED_ARRIVED_DEG of it (carrying the bottle it cannot get closer)."""
        deadline = time.monotonic() + RealRobotConfig.MOVE_TIMEOUT_S
        history: list[tuple[float, dict[str, float]]] = []
        while time.monotonic() < deadline and not self.bridge.blocked:
            # Fresh each pass: the levelled wrist target follows the shoulder and elbow as they settle.
            target = self.bridge.target()
            error = self.bridge.error(target)
            if error <= RealRobotConfig.ARRIVED_DEG:
                return True
            now = time.monotonic()
            present = {joint: value for joint, value in self.bridge.reply.get("present", {}).items() if joint != "gripper"}
            history = [(at, joints) for at, joints in history if now - at <= RealRobotConfig.SETTLED_S] + [(now, present)]
            moved = max(max(joints[joint] for _, joints in history) - min(joints[joint] for _, joints in history) for joint in present) if present else np.inf
            settled = now - history[0][0] >= RealRobotConfig.SETTLED_S * 0.9 and moved <= RealRobotConfig.SETTLED_DEG
            if settled and error <= RealRobotConfig.LOADED_ARRIVED_DEG:
                worst = max(present, key=lambda joint: abs(present[joint] - target[joint]))
                self.report(f"负载下停住了：{worst} 差 {error:.1f}°（≤ {RealRobotConfig.LOADED_ARRIVED_DEG:.0f}°），算到位")
                return True
            time.sleep(0.1)
        self.report("被停止" if self.bridge.blocked else f"真机没跟上（差 {self.bridge.error(self.bridge.target()):.1f}°）")
        return False


def demo_real_grasp_execution() -> None:
    """Needs the bringup launch and the teleop service; runs only 'prepare' and 'plan' (the head moves to the grasp
    start pose, the arms stay at rest)."""
    execution = RealGraspExecution(MoveItHelper(node_name="real_grasp_execution_demo"), TeleopHelper())
    for name in ("prepare", "plan"):
        logger.info("{}：{}", name, execution.run(name))
    logger.info("\n{}", "\n".join(execution.progress))


def main() -> None:
    use_utf8_output()
    demo_real_grasp_execution()


if __name__ == "__main__":
    main()
