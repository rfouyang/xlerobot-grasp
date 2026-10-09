"""抓取执行：桌子和物体作为障碍，挑从侧面抓瓶身中段的抓取并放进夹爪兜里，筛可达的、越靠中间越先试规划，第一个规划成功的去执行。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import time

import numpy as np
from loguru import logger

from component.grasp_execution.grasp_selection import GraspSelection
from component.grasp_execution.motion import GraspMotion
from component.grasp_execution.reachability import GraspReachability, ReachableGrasp
from component.grasp_generation.grasp_prediction import GraspCandidate
from component.grasp_generation.tabletop_scene import PlacedObject, TabletopScene
from config.settings import use_utf8_output
from util.motion_plan_helper import MotionPlanHelper
from util.moveit_helper import MoveItHelper


class GraspExecution:
    def __init__(self, moveit: MoveItHelper) -> None:
        self.planner = MotionPlanHelper()
        self.reachability = GraspReachability(self.planner)
        self.selection = GraspSelection()
        self.motion = GraspMotion(self.planner, moveit)
        # What the page shows while a run is going: one line per step, newest last.
        self.progress: list[str] = []
        self.running = False
        self.chosen: ReachableGrasp | None = None

    def report(self, text: str) -> None:
        self.progress.append(f"{time.strftime('%H:%M:%S')} {text}")
        logger.info("抓取执行：{}", text)

    def run(self, scene: TabletopScene, placed: PlacedObject, candidates: list[GraspCandidate]) -> bool:
        """Blocks until the lift is done or nothing could be planned."""
        self.progress = []
        self.running = True
        self.chosen = None
        box = placed.mesh.bounding_box_oriented
        self.planner.set_obstacles([("table", tuple(scene.table_size), scene.table_pose),
                                    ("object", tuple(box.primitive.extents), box.primitive.transform)])
        # Every run starts from the same pose, so reachability and planning do not depend on the last run.
        self.report("右臂回到就绪位")
        self.motion.go_ready()
        start = self.motion.arm_joints()
        seated = self.selection.seat(candidates, placed)
        self.report(f"{len(candidates)} 个候选里从侧面抓瓶身中段的 {len(seated)} 个，转到正对右肩、瓶身放进夹爪的兜里")
        ordered = self.selection.level(self.reachability.filter(seated, start))
        self.report(f"右臂能水平够到的 {len(ordered)} 个（越靠中间越先试）")
        segments = None
        for grasp in ordered:
            segments = self.motion.plan(start, grasp)
            if segments is not None:
                self.chosen = grasp
                break
        if segments is None:
            self.report("没有能规划出轨迹的抓取")
            self.running = False
            return False
        self.report(f"选中抓取 #{self.chosen.index}（分数 {self.chosen.score:.3f}），轨迹 "
                    + " / ".join(f"{name} {len(segment.positions) * segment.dt:.1f}s" for name, segment in segments.items()))
        done = self.motion.execute(segments, self.report)
        self.report("完成：物体已抬起" if done else "执行中断")
        self.running = False
        return done


def demo_grasp_execution() -> None:
    """Needs the bringup launch; mock hardware, so the jaw closes on nothing."""
    from component.grasp_generation.service import GraspGeneration

    generation = GraspGeneration()
    candidates = generation.generate("coke_can")
    execution = GraspExecution(MoveItHelper(node_name="grasp_execution_demo"))
    done = execution.run(generation.scene, generation.placed, candidates)
    logger.info("结果：{}\n{}", done, "\n".join(execution.progress))


def main() -> None:
    use_utf8_output()
    demo_grasp_execution()


if __name__ == "__main__":
    main()
