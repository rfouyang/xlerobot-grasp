"""放进盒子：从头部深度里找出桌上开口朝上的盒子，规划把抓起的瓶子送到盒子上方、竖直放下、退出、回起始位。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable

import numpy as np
import trimesh.transformations as tra
from loguru import logger

from component.box_placement.open_box import OpenBox
from component.camera_calibration.check import head_camera_pose
from component.grasp_planning.reachable_grasp import ReachableGrasp
from component.grasp_planning.real_scene import RealScene
from component.grasp_planning.service import RIGHT_ARM, RealGraspPlanning
from config.planning import MotionConfig, RealPlanningConfig, SelectionConfig
from config.settings import use_utf8_output
from config.task import PlaceConfig
from util.motion_plan_helper import JointTrajectory

# What the page and the voice say when a place does not plan (BoxPlacement.failure), "unreachable" as said for a box.
FAILURE_WORDS = {
    "chosen": "选的盒子不是开口朝上的（或没测出盒沿）：换一个盒子",
    "none": "头部画面里没找到开口朝上的盒子",
    "small": "盒子测出来放不下瓶子（可能没测准，看头部画面里盒子的框）",
    "start_box": "抓起瓶子后手会碰到盒子：把瓶子挪得离盒子远一点（或把盒子往前推一点）",
    "start_table": "抓起瓶子后手会碰到桌面（规划器看到的桌面比实际高）",
    "start_self": "抓起瓶子后右臂和左臂（或头）离得太近",
    "unreachable": "没有规划出放进盒子的路径（右臂够不到盒子里能放的点）",
    "unreachable_spot": "没有规划出放到桌面点的路径（右臂够不到那里，或那里放不下）",
}


class BoxPlacement:
    """The box is found by YOLOE ("box") and measured on the sensor's own depth (an opaque box reads cleanly there;
    LingBot-Depth spills past its edges): the candidate that is open (rim 2-12 cm) and nearest the right shoulder.
    The bottle sits in the jaw as it was grasped, so the place pose is the grasp pose turned about the vertical toward
    the place point and raised by the floor's height. cuRobo knows the arm only, not the bottle under the jaw: while
    carrying, the box is an obstacle raised by the bottle's length below the jaw, so its foot clears the rim."""

    def __init__(self, planning: RealGraspPlanning) -> None:
        self.planning = planning
        # Why the last observe() or plan() came back empty, a FAILURE_WORDS key.
        self.failure = ""

    def failure_words(self, spot: bool = False) -> str:
        key = "unreachable_spot" if spot and self.failure == "unreachable" else self.failure
        words = FAILURE_WORDS[key]
        return words

    def observe(self, real_scene: RealScene, near_uv: list[float] | None = None) -> OpenBox | None:
        """The open box: the one at near_uv in the head image (the box chosen in the chat; else the nearest to it), or
        without near_uv the one nearest the right shoulder."""
        self.failure = ""
        frame = real_scene.target.frame
        # Before LingBot-Depth: the sensor's (or the stereo's) own depth.
        depth = frame["depth_raw"] if "depth_raw" in frame else frame["depth"] * frame["depth_scale"]
        detections = self.planning.perception.detector.detect(frame["color"], PlaceConfig.BOX_PROMPTS,
                                                              conf=PlaceConfig.BOX_CONFIDENCE)
        self.planning.urdf.update_cfg(real_scene.urdf_joints)
        base_from_camera = head_camera_pose(self.planning.urdf)
        table_z = real_scene.scene.table_top_z
        boxes = []
        # Each open box's detection frame in the head image, to find the chosen one by.
        frames = []
        for detection in detections:
            points = self.planning.back_project({**frame, "depth": depth, "depth_scale": 1.0}, base_from_camera,
                                                detection.mask)
            camera_distance = np.linalg.norm(points - base_from_camera[:3, 3], axis=1)
            height = points[:, 2] - table_z
            points = points[(camera_distance < 1.0) & (height > -0.01) & (height < 0.15)]
            height = points[:, 2] - table_z
            if len(points) < 500:
                continue
            rim = float(np.percentile(height, 97))
            if not PlaceConfig.RIM_RANGE_M[0] <= rim <= PlaceConfig.RIM_RANGE_M[1]:
                continue
            # The footprint from every point of the box (floor and walls): the walls' upper part alone held only
            # the near wall's outside once the box stood straight ahead (the thin side walls read low there),
            # 10.8 x 0.4 cm.
            centre, long_axis, half = self.footprint(points[:, :2])
            inside = height[(height > -0.005) & (height < 0.5 * rim)]
            floor = float(np.median(inside)) if len(inside) > 50 else 0.005
            box = OpenBox(centre=centre, long_axis=long_axis, half=half, rim_z=table_z + rim, floor_z=table_z + floor,
                          table_z=table_z)
            logger.info("盒子候选（{} {:.2f}）：中心 {}，{:.1f} x {:.1f} cm，长边朝 {:.0f}°，"
                        "盒沿高 {:.1f} cm，盒底高 {:.1f} cm", detection.label,
                        detection.score, np.round(box.centre, 3), box.half[0] * 200, box.half[1] * 200,
                        np.degrees(np.arctan2(box.long_axis[1], box.long_axis[0])), rim * 100, floor * 100)
            boxes.append(box)
            frames.append(detection.box)
        if near_uv is not None and boxes:
            u, v = near_uv
            def at(i):
                return (0.0 if frames[i][0] <= u <= frames[i][2] and frames[i][1] <= v <= frames[i][3]
                        else float(np.hypot((frames[i][0] + frames[i][2]) / 2 - u,
                                            (frames[i][1] + frames[i][3]) / 2 - v)))

            index = min(range(len(boxes)), key=at)
            if at(index) > 0:
                # The chosen box is none of the open ones (a closed box, a rim not measured): not another box instead.
                self.failure = "chosen"
                logger.info("对话里选的盒子（画面 {}）不在开口朝上的盒子里", tuple(near_uv))
                return None
            logger.info("盒子：用选的那个（画面 {}，{} 个开口朝上的里第 {} 个）", tuple(near_uv), len(boxes), index + 1)
            return boxes[index]
        shoulder = np.array(SelectionConfig.SHOULDER_XY)
        box = min(boxes, key=lambda b: np.linalg.norm(b.centre - shoulder)) if boxes else None
        self.failure = "" if box is not None else "none"
        return box

    @staticmethod
    def footprint(walls: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The walls' rectangle from their points (x, y): the long axis by PCA, the extents between the 2nd and 98th
        percentiles along each axis. A tight rectangle over every point grew to 40 cm on a 23 cm box (stray depth at
        its edges)."""
        middle = np.median(walls, axis=0)
        _, vectors = np.linalg.eigh(np.cov((walls - middle).T))
        long_axis = vectors[:, 1]
        across = np.array([-long_axis[1], long_axis[0]])
        s, t = (walls - middle) @ long_axis, (walls - middle) @ across
        (s0, s1), (t0, t1) = np.percentile(s, [2, 98]), np.percentile(t, [2, 98])
        centre = middle + (s0 + s1) / 2 * long_axis + (t0 + t1) / 2 * across
        half = np.array([s1 - s0, t1 - t0]) / 2
        return centre, long_axis, half

    @staticmethod
    def spot(point: np.ndarray, table_z: float, radius: float) -> OpenBox:
        """A desk point as a box with no walls: its place points are the point itself (within 4 mm, for a bottle of this
        radius), its floor and rim the desk, so the bottle goes down to the height it was grasped from."""
        half = PlaceConfig.WALL_M + radius + PlaceConfig.WALL_MARGIN_M + 0.004
        box = OpenBox(centre=np.asarray(point, float), long_axis=np.array([1.0, 0.0]), half=np.array([half, half]),
                      rim_z=table_z, floor_z=table_z, table_z=table_z)
        return box

    def obstacle(self, box: OpenBox, extra_height: float) -> tuple[str, tuple, np.ndarray] | None:
        """The box as a solid cuboid from the table to its rim, plus extra_height, cut off nearer the robot than
        OBSTACLE_MIN_X: the left arm, folded at rest and locked where it is while the right arm plans, sits over the
        box's near end, and a box around it put every right-arm state in collision (19:37)."""
        if box.rim_z - box.table_z < 0.005:
            # A desk spot (no walls): nothing to keep clear of but the desk itself. Its column for the carry stood
            # around the lifted jaw when the spot was near the bottle, and nothing planned (2026-10-09).
            return None
        height = box.rim_z - box.table_z + extra_height
        low, high = -box.half[0], box.half[0]
        if abs(box.long_axis[0]) > 1e-3:
            # Along the long axis, where the centre line crosses x = OBSTACLE_MIN_X, moved on so that the near corners
            # of a turned box are not nearer either.
            cut = (PlaceConfig.OBSTACLE_MIN_X - box.centre[0]) / box.long_axis[0]
            slack = abs(box.long_axis[1]) * box.half[1] / abs(box.long_axis[0])
            low, high = (max(low, cut + slack), high) if box.long_axis[0] > 0 else (low, min(high, cut - slack))
        centre = box.centre + (low + high) / 2 * box.long_axis
        pose = tra.rotation_matrix(np.arctan2(box.long_axis[1], box.long_axis[0]), [0, 0, 1])
        pose[:3, 3] = [*centre, box.table_z + height / 2]
        obstacle = ("box", (max(high - low, 0.01), 2 * box.half[1], height), pose)
        return obstacle

    def place_points(self, box: OpenBox, radius: float) -> list[np.ndarray]:
        """Where the bottle's axis may go: inside the walls by the bottle's radius and a margin, those about as far
        from the right shoulder as a grasp first (the near end of the box is too close to the body, the far end out of
        reach)."""
        room = box.half - PlaceConfig.WALL_M - radius - PlaceConfig.WALL_MARGIN_M
        if np.any(room < 0):
            return []
        across = np.array([-box.long_axis[1], box.long_axis[0]])
        offsets = [0.0] if room[1] < 0.01 else [-room[1] / 2, 0.0, room[1] / 2]
        points = [box.centre + s * box.long_axis + t * across
                  for s in np.linspace(-room[0], room[0], 9) for t in offsets]
        shoulder = np.array(SelectionConfig.SHOULDER_XY)
        points.sort(key=lambda p: abs(np.linalg.norm(p - shoulder) - PlaceConfig.PREFERRED_REACH_M))
        return points

    def start_collision(self, joints: np.ndarray, pose: np.ndarray, table_obstacle: tuple) -> str:
        """What the lifted arm (the plan's start) is in collision with: planned in place with the desk alone, then with
        nothing (the locked left arm and the head stay)."""
        planner = self.planning.planner
        planner.set_obstacles([table_obstacle])
        if planner.plan_segment(joints, pose, touching=False) is not None:
            logger.info("抓起后的手臂碰到了盒子（盒子的障碍体）")
            return "start_box"
        # A small cuboid far away for "nothing": an empty world is not what cuRobo was set up with.
        far = np.eye(4)
        far[:3, 3] = [5.0, 5.0, -5.0]
        planner.set_obstacles([("far", (0.01, 0.01, 0.01), far)])
        if planner.plan_segment(joints, pose, touching=False) is not None:
            logger.info("抓起后的手臂碰到了桌面")
            return "start_table"
        logger.info("抓起后的手臂自身就在碰撞里（和锁住的左臂或头）")
        return "start_self"

    def stays_above(self, segment: JointTrajectory, z: float) -> bool:
        lowest = min(self.planning.planner.tool_pose(joints)[2, 3] for joints in segment.positions)
        ok = lowest >= z - PlaceConfig.EXIT_DIP_M
        return ok

    def order(self, points: list[np.ndarray], toward: np.ndarray | None, reaches: Callable[[np.ndarray], bool]) -> list:
        """The place points to try first: those nearest toward (where the box was clicked); without it, those around
        the middle of what the right arm reaches (reaches: the spot above it has an IK solution); with none reaching,
        as place_points sorted them."""
        if toward is not None:
            ordered = sorted(points, key=lambda p: np.linalg.norm(p - toward))
            logger.info("放置点按点选的位置 {} 排：最近的离它 {:.1f} cm", np.round(toward, 3),
                        np.linalg.norm(ordered[0] - toward) * 100 if ordered else 0.0)
            return ordered
        reached = [point for point in points if reaches(point)]
        if not reached:
            logger.info("盒内的点右手都够不着（放置点上方 IK 无解）")
            return points
        middle = np.mean(reached, axis=0)
        logger.info("盒内右手够得到的点 {}/{} 个，中心 {}", len(reached), len(points), np.round(middle, 3))
        ordered = (sorted(reached, key=lambda p: np.linalg.norm(p - middle))
                   + [p for p in points if not any(p is q for q in reached)])
        return ordered

    def plan(self, real_scene: RealScene, grasp: ReachableGrasp, segments: dict[str, JointTrajectory], box: OpenBox,
             toward: np.ndarray | None = None) -> dict | None:
        """Segments carry (lifted, to above the place), lower (down into the box), back_out (straight up out of the box,
        then back along the approach up there) and home (to the start pose), with where the bottle goes; None when no
        place point plans. Backing out at the place's height took the jaw down past the box's wall (2026-10-06).
        toward: (x, y) the place point nearest it first (where the box was clicked); None: the middle of the box's part
        the right arm reaches first."""
        planner = self.planning.planner
        grasp_pose = grasp.tool_pose
        bounds = real_scene.placed.mesh.bounds
        axis_xy = bounds.mean(axis=0)[:2]
        radius = (bounds[1, 0] - bounds[0, 0]) / 2
        bottle_in_tool = axis_xy - grasp_pose[:2, 3]
        start = np.array([real_scene.urdf_joints[joint] for joint in RIGHT_ARM])
        shoulder = np.array(SelectionConfig.SHOULDER_XY)
        table = real_scene.scene.table_pose.copy()
        table[2, 3] += RealPlanningConfig.TABLE_CLEARANCE_M
        table_obstacle = ("table", tuple(real_scene.scene.table_size), table)
        # The bottle hangs this far below the jaw's lowest point: the tool sits (grasp z - table) above its foot.
        below_jaw = grasp_pose[2, 3] - box.table_z - PlaceConfig.TOOL_ABOVE_JAW_BOTTOM_M
        lifted_joints = segments["lift"].positions[-1]

        def obstacles(extra_height: float) -> None:
            """The desk and the box raised by extra_height (a desk spot has no box)."""
            planner.set_obstacles([o for o in (table_obstacle, self.obstacle(box, extra_height)) if o])

        points = self.place_points(box, radius)
        logger.info("盒内可放的点 {} 个（瓶子半径 {:.1f} cm）", len(points), radius * 100)
        self.failure = "unreachable" if points else "small"

        def poses(point: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
            yaw = np.arctan2(*(point - shoulder)[::-1]) - np.arctan2(*(axis_xy - shoulder)[::-1])
            turn = tra.rotation_matrix(yaw, [0, 0, 1])
            place = turn @ grasp_pose
            place[:2, 3] = point - turn[:2, :2] @ bottle_in_tool
            place[2, 3] = grasp_pose[2, 3] + (box.floor_z - box.table_z) + PlaceConfig.PLACE_CLEARANCE_M
            # Carried just high enough: the bottle's foot over the rim by the clearance and the loaded arm's sag (out
            # to the left the arm reaches less high; the full lift height planned nowhere there).
            above = place.copy()
            above[2, 3] += (box.rim_z - box.floor_z) + PlaceConfig.CARRY_CLEARANCE_M + PlaceConfig.CARRY_SAG_M
            return yaw, place, above

        carried = below_jaw + PlaceConfig.CARRY_CLEARANCE_M
        obstacles(carried)
        points = self.order(points, toward, lambda point: planner.solve_ik(poses(point)[2], lifted_joints) is not None)
        for point in points[: PlaceConfig.MAX_PLACE_TRIES]:
            yaw, place, above = poses(point)
            # Out of the box: up until the open jaw is over the rim by EXIT_CLEARANCE_M, then back level from there.
            risen = place.copy()
            risen[2, 3] = max(above[2, 3],
                              box.rim_z + PlaceConfig.TOOL_ABOVE_JAW_BOTTOM_M + PlaceConfig.EXIT_CLEARANCE_M)
            back = risen.copy()
            back[:2, 3] -= MotionConfig.APPROACH_DISTANCE * place[:2, 2] / max(np.linalg.norm(place[:2, 2]), 1e-6)
            obstacles(carried)
            carry = planner.plan_segment(lifted_joints, above, touching=False)
            if carry is None:
                # Why: the place unreachable, or the start (the lifted arm) already in collision?
                lifted = planner.tool_pose(lifted_joints)
                stay = planner.plan_segment(lifted_joints, lifted, touching=False)
                logger.info("送到盒子上方失败：放置点上方 IK {}，起点原地规划 {}",
                            "有解" if planner.solve_ik(above, lifted_joints) is not None else "无解",
                            "成功" if stay is not None else "失败（起点可能在碰撞里）")
                if stay is None:
                    self.failure = self.start_collision(lifted_joints, lifted, table_obstacle)
                    # The same start for every place point: no use trying the others.
                    break
                continue
            obstacles(0.0)
            lower = planner.plan_segment(carry.positions[-1], place, touching=False)
            rise = planner.plan_segment(lower.positions[-1], risen, touching=False) if lower is not None else None
            # Leaving, the walls count a little higher: the arm keeps clear of the rim on the way back and home.
            obstacles(PlaceConfig.EXIT_CLEARANCE_M)
            level = planner.plan_segment(rise.positions[-1], back, touching=False) if rise is not None else None
            home = (planner.plan_segment(level.positions[-1], planner.tool_pose(start), touching=False)
                    if level is not None else None)
            if home is None:
                continue
            # The tool may not dip on the way out: not below the place while rising, not below the risen height after.
            if not (self.stays_above(rise, place[2, 3]) and self.stays_above(level, risen[2, 3])):
                logger.info("退出时工具下沉，换下一个放置点")
                continue
            back_out = JointTrajectory(joint_names=rise.joint_names,
                                       positions=np.vstack([rise.positions, level.positions[1:]]), dt=rise.dt)
            # The plan's end turned about the free axis maybe: finish exactly on the start joints.
            blend = np.linspace(home.positions[-1], start, 15)[1:]
            home = JointTrajectory(joint_names=home.joint_names, positions=np.vstack([home.positions, blend]),
                                   dt=home.dt)
            planned = {"carry": carry, "lower": lower, "back_out": back_out, "home": home}
            if not all(self.planning.roll_ok(segment.positions) for segment in planned.values()):
                continue
            result = {"segments": planned, "point": point, "yaw_deg": float(np.degrees(yaw)),
                      "place_tool": place[:3, 3]}
            return result
        return None


def demo_box_placement() -> None:
    """Reads only (the teleop service, the head at the grasp pose, a bottle and an open box on the desk): finds the
    box, plans a grasp and the placing, nothing moves."""
    from component.grasp_generation.grasp_prediction import GraspPrediction
    from util.teleop_helper import TeleopHelper

    planning = RealGraspPlanning(TeleopHelper(), prediction=GraspPrediction())
    real_scene = planning.observe()
    box = BoxPlacement(planning).observe(real_scene)
    if box is None:
        logger.warning("没找到开口朝上的盒子")
        return
    planned = planning.plan(real_scene)
    if planned is None:
        logger.warning("没有规划出抓取")
        return
    result = BoxPlacement(planning).plan(real_scene, *planned, box)
    logger.info("放置：{}", "没有规划出来" if result is None
                else (f"瓶轴到 {np.round(result['point'], 3)}，"
                      f"转 {result['yaw_deg']:+.1f}°"))


def main() -> None:
    use_utf8_output()
    demo_box_placement()


if __name__ == "__main__":
    main()
