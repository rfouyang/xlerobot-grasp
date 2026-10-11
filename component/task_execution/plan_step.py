"""「检测并规划」这一步：头部相机看到的物体，用哪只手、怎么抓；抓取摆放还有放到哪（盒子里点的位置，或桌面点）。
规划出来的都记在 RunState 里，后面的步骤照着走。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable

import numpy as np
from loguru import logger

from component.box_placement.open_box import OpenBox
from component.box_placement.service import BoxPlacement
from component.camera_calibration.check import CalibrationCheck
from component.grasp_planning.arm import ARMS, Arm
from component.grasp_planning.arm_choice import ArmChoice
from component.grasp_planning.real_scene import RealScene
from component.grasp_planning.service import RealGraspPlanning
from component.place_choice.service import PlaceChoice
from component.target_selection.service import TargetSelector
from component.task_execution.motion import ArmMotion
from component.task_execution.run_state import RunState
from config.settings import use_utf8_output
from config.task import MoveRightConfig, RealRobotConfig
from util.joint_mapping_helper import JointMappingHelper


class PlanStep:
    """The runner calls run() holding the GPU lock (cuRobo records CUDA graphs while it plans)."""

    def __init__(self, planning: RealGraspPlanning, choice: ArmChoice, box: BoxPlacement, selector: TargetSelector,
                 place: PlaceChoice, motion: ArmMotion, check: CalibrationCheck, use_arm: Callable[[Arm], None],
                 report: Callable[[str], None]) -> None:
        self.planning = planning
        self.choice = choice
        self.box = box
        self.selector = selector
        self.place = place
        self.motion = motion
        self.check = check
        # The runner's: the motion and the wrist camera follow the arm chosen here.
        self.use_arm = use_arm
        self.report = report
        self.mapping = JointMappingHelper()

    @property
    def arm(self) -> Arm:
        arm = self.motion.arm
        return arm

    def run(self, state: RunState, action: str, ignore_left: bool = False, arm_override: str | None = None) -> bool:
        real_scene = self.planning.observe()
        state.scene = real_scene
        perception = self.planning.perception
        if perception.point is not None:
            # Later looks (the upright check) follow the object where it was found, not where the click landed.
            rows, cols = np.nonzero(real_scene.target.detection.mask)
            perception.point = (float(cols.mean()), float(rows.mean()))
            perception.point_area = int(real_scene.target.detection.mask.sum())
            if action == "pick_target":
                self.selector.mask = real_scene.target.detection.mask
        self.motion.pan_offset = 0.0
        state.align_gap = 0.0
        state.align_image = None
        axis_y = float(real_scene.placed.mesh.bounds.mean(axis=0)[1])
        shift = min(max(axis_y - MoveRightConfig.HOME_Y, 0.0), MoveRightConfig.MAX_SHIFT_M)
        if action == "grasp" and not ignore_left and axis_y > MoveRightConfig.LEFT_LIMIT_Y:
            # The user decides: move it right first, or grasp anyway.
            state.too_left = {"axis_y": round(axis_y, 3), "shift_m": round(shift, 3)}
            self.report(f"瓶子太靠左（y {axis_y:+.3f} m，超过 {MoveRightConfig.LEFT_LIMIT_Y:+.3f} m），机器人没动："
                        f"要先「把瓶子往右移」{shift * 100:.1f} cm，还是照常抓取？")
            return False
        state.shift_m = shift if action == "move_right" else 0.0
        box = None
        if action in ("place_in_box", "place_at_spot"):
            box = self.find_place(real_scene, action)
            if box is None:
                return False
        planned = self.plan_grasp(state, real_scene, action, arm_override)
        if planned is None:
            return False
        grasp, segments = planned
        if box is not None and not self.plan_place(state, real_scene, grasp, segments, box, action):
            return False
        state.start_jaw = float(self.mapping.to_urdf({self.arm.part: self.motion.real_arm()})[self.arm.jaw])
        self.motion.sync_mock()
        self.report(f"抓取 #{grasp.index}（分数 {grasp.score:.3f}），"
                    f"指尖中心 {np.round(grasp.tool_pose[:3, 3], 3).tolist()}；"
                    + "，".join(f"{name} {len(segment.positions) * segment.dt * RealRobotConfig.TIME_SCALE:.1f}s"
                               for name, segment in segments.items()))
        real = self.planning.to_real(segments)
        rolls = np.concatenate([rows[:, 4] for rows in real.values()])
        self.report("抓取位真机关节（pan, lift, elbow, wrist_flex, wrist_roll）"
                    + str(np.round(real["grasp"][-1], 1).tolist())
                    + f"；全程 wrist_roll {rolls.min():.1f}° ~ {rolls.max():.1f}°"
                      f"（起始 {self.motion.real_arm()['wrist_roll']:.1f}°）")
        return True

    def plan_grasp(self, state: RunState, real_scene: RealScene, action: str, arm_override: str | None) -> tuple | None:
        """(grasp, segments) with the nearer arm (the user, 10-10: the object on the left is the left arm's): a far arm
        reaching across is not tried; the reach maps' far arm only when the nearer one cannot reach at all."""
        bounds = real_scene.placed.mesh.bounds
        axis = bounds.mean(axis=0)[:2]
        sides = [arm_override] if arm_override and action == "pick_target" else self.choice.sides(action)
        reach = self.choice.advice(float(axis[0]), float(axis[1]), float(bounds[1, 2] - real_scene.scene.table_top_z),
                                   real_scene.scene.table_top_z, sides)
        side = (reach["arms"] or sides)[0]
        self.use_arm(ARMS[side])
        if len(sides) > 1:
            self.report(f"用{self.arm.label}抓：{reach['text']}")
        planned = self.planning.plan(real_scene, self.arm)
        if planned is None:
            self.report(f"没有规划出{self.arm.label}的抓取：{self.failure()}"
                        + ("" if reach["ok"] else f"；{reach['text']}"))
            return None
        grasp, segments = planned
        self.motion.segments = segments
        state.grasp = grasp
        state.plan_image = self.planning.draw(real_scene, grasp, segments, self.check)
        return planned

    def failure(self) -> str:
        """Why the last plan found no grasp, in a few words: the desk guard's closest refusal if it refused them."""
        rejections = self.planning.preferences.report.get("desk_rejections") or []
        if rejections:
            closest = max(rejections, key=lambda item: item["clearance_m"])
            words = (f"夹爪离桌面最多 {closest['clearance_m'] * 100:.1f} cm，"
                     f"安全距离要 {closest['required_m'] * 100:.1f} cm")
            return words
        return "可达的抓取都没通过检查"

    def find_place(self, real_scene: RealScene, action: str) -> OpenBox | None:
        """Where it goes, as planning sees it: the desk spot as a box without walls, or the open box chosen."""
        place = self.place
        if action == "place_at_spot":
            if not place.spot:
                self.report("还没选放置点：在头部画面里点一下桌面上要放的位置")
                return None
            bounds = real_scene.placed.mesh.bounds
            box = self.box.spot(np.array(place.spot["xy"]), real_scene.scene.table_top_z,
                                (bounds[1, 0] - bounds[0, 0]) / 2)
            self.report(f"放到桌面点 {place.spot['xy']}")
            return box
        box = self.box.observe(real_scene, place.box["frame_uv"] if place.box else None)
        if box is None:
            self.report(self.box.failure_words())
            return None
        self.report(f"盒子：中心 {np.round(box.centre, 3).tolist()}，"
                    f"{box.half[0] * 200:.1f} x {box.half[1] * 200:.1f} cm，"
                    f"盒沿高 {(box.rim_z - box.table_z) * 100:.1f} cm，"
                    f"盒底高 {(box.floor_z - box.table_z) * 100:.1f} cm")
        return box

    def plan_place(self, state: RunState, real_scene: RealScene, grasp, segments: dict, box: OpenBox,
                   action: str) -> bool:
        """The carry, lowering, backing out and home after the grasp: in the box where it was clicked (or the middle
        of what the right arm reaches), or onto the desk spot. The + and the later upright check follow the plan."""
        in_box = action == "place_in_box"
        placed = self.box.plan(real_scene, grasp, segments, box, self.place.toward() if in_box else None)
        if placed is None:
            self.report(self.box.failure_words(spot=not in_box))
            return False
        segments.update(placed["segments"])
        state.placed_uv = self.selector.image_point([*placed["point"], box.floor_z + 0.05])
        if in_box:
            self.place.planned(placed["point"], self.selector.image_point([*placed["point"], box.floor_z]))
        self.report(f"放到盒子里：瓶轴到 {np.round(placed['point'], 3).tolist()}，夹爪转 {placed['yaw_deg']:+.1f}°，"
                    f"放下时指尖中心 {np.round(placed['place_tool'], 3).tolist()}")
        return True


def demo_plan_step() -> None:
    """Needs the bringup, the robot process and the GPU models (do not run it beside app/main.py): plans the nearest
    bottle with the arms at rest; nothing moves."""
    from component.task_execution.service import TaskRunner
    from util.moveit_helper import MoveItHelper
    from util.teleop_helper import TeleopHelper

    runner = TaskRunner(MoveItHelper(node_name="plan_step_demo"), TeleopHelper())
    planned = runner.planner.run(runner.state, "pick_target")
    logger.info("规划：{}\n{}", planned, "\n".join(runner.progress))


def main() -> None:
    use_utf8_output()
    demo_plan_step()


if __name__ == "__main__":
    main()
