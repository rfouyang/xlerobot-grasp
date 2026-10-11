"""一次真机任务的评估记录（抓取规划页）：规划成功时开一条，最后一步做完或某一步失败时写进 records.py 的文件；
规划失败也记一行（要抓什么、为什么没规划出来）。写不成只记日志，不影响任务。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from collections.abc import Callable

from loguru import logger

from component.grasp_planning.service import RealGraspPlanning
from component.task_execution.records import GraspRecords, reason_from, upright_from
from component.task_execution.run_state import RunState
from config.settings import use_utf8_output


class AttemptLog:
    def __init__(self, records: GraspRecords, planning: RealGraspPlanning) -> None:
        self.records = records
        self.planning = planning
        # The attempt opened when planning succeeds, and where its progress lines start.
        self.attempt: dict | None = None
        self.attempt_from = 0

    def drop(self) -> None:
        """A run reset half way is not recorded."""
        self.attempt = None

    def record(self, name: str, done: bool, finished: bool, state: RunState, progress: list[str],
               base: Callable[[], dict]) -> None:
        """After step name: finished when it was the last step. base gives what was asked (time, task, arm, target,
        place)."""
        try:
            if name == "plan" and done:
                self.attempt = self.opened(base(), state)
                self.attempt_from = len(progress)
            elif name == "plan" and not state.too_left:
                self.records.append({**base(), "outcome": "plan_failed", "reason": reason_from(progress),
                                     "funnel": self.funnel()})
            elif self.attempt is not None and (not done or finished):
                self.attempt.update(outcome="ok" if done else "failed", failed_step=None if done else name,
                                    reason="" if done else reason_from(progress),
                                    stage_seconds=dict(state.stage_seconds), align=state.align_info,
                                    upright=upright_from(progress[self.attempt_from:]),
                                    seconds=round(sum(state.stage_seconds.values()), 1))
                self.records.append(self.attempt)
                self.attempt = None
        except Exception as error:
            logger.warning("评估记录没写成：{}", error)

    def funnel(self) -> dict:
        """How many grasp candidates each check of the last plan kept."""
        funnel = {key: value for key, value in self.planning.stats.items() if key != "kept"}
        return funnel

    def opened(self, base: dict, state: RunState) -> dict:
        bounds = state.scene.placed.mesh.bounds
        grasp = state.grasp
        report = self.planning.preferences.report
        attempt = {**base,
                   "object": {"axis_xy": [round(float(v), 3) for v in bounds.mean(axis=0)[:2]],
                              "height_m": round(float(bounds[1, 2] - state.scene.scene.table_top_z), 3),
                              "radius_m": round(float((bounds[1, 0] - bounds[0, 0]) / 2), 3)},
                   "grasp": {"candidate": int(grasp.index), "score": round(float(grasp.score), 3),
                             "tool_xyz": [round(float(v), 3) for v in grasp.tool_pose[:3, 3]]},
                   "ranking": {"mode": self.planning.preferences.options.get("mode", "baseline"),
                               "selected": report.get("selected"), "used": report.get("used")},
                   "funnel": self.funnel()}
        return attempt


def demo_attempt_log() -> None:
    """Read-only: the last few records (the file is written only by real runs)."""
    records = GraspRecords()
    for record in records.recent(3):
        logger.info("{} {} {} → {}", record.get("time"), record.get("action"), record.get("arm"), record.get("outcome"))


def main() -> None:
    use_utf8_output()
    demo_attempt_log()


if __name__ == "__main__":
    main()
