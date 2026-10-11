"""The evaluation record of a run (attempt_log.py): opened when planning succeeds, written at the last step or the
first failure, one line for a failed plan, none for a run reset half way."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from component.task_execution.attempt_log import AttemptLog
from component.task_execution.records import GraspRecords
from component.task_execution.run_state import RunState


def planned_state() -> RunState:
    """A bottle 3 cm in radius and 15 cm tall at (0.3, -0.1) on a desk at z 0, and grasp #7."""
    bounds = np.array([[0.27, -0.13, 0.0], [0.33, -0.07, 0.15]])
    scene = SimpleNamespace(placed=SimpleNamespace(mesh=SimpleNamespace(bounds=bounds)),
                            scene=SimpleNamespace(table_top_z=0.0))
    grasp = SimpleNamespace(index=7, score=0.9, tool_pose=np.eye(4))
    state = RunState(scene=scene, grasp=grasp)
    return state


class AttemptLogTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.records = GraspRecords(Path(folder.name) / "records.jsonl")
        planning = SimpleNamespace(stats={"generated": 40, "reachable": 9, "kept": [1, 2]},
                                   preferences=SimpleNamespace(report={}, options={}))
        self.log = AttemptLog(self.records, planning)
        self.base = lambda: {"action": "place_in_box", "arm": "right"}

    def test_a_run_that_works(self) -> None:
        state = planned_state()
        progress = ["17:27 开始：检测并规划"]
        self.log.record("plan", True, False, state, progress, self.base)
        progress.append("17:28 真机抓取：瓶子立着（高 14.4 cm，规划时 13.0 cm）")
        state.stage_seconds = {"plan": 24.0, "home": 7.1}
        self.log.record("home", True, True, state, progress, self.base)
        [record] = self.records.all()
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(record["object"]["radius_m"], 0.03)
        self.assertEqual(record["grasp"]["candidate"], 7)
        self.assertEqual(record["funnel"], {"generated": 40, "reachable": 9})
        self.assertEqual(record["seconds"], 31.1)

    def test_a_failed_step_and_a_failed_plan(self) -> None:
        self.log.record("plan", True, False, planned_state(), [], self.base)
        self.log.record("align", False, False, planned_state(), ["腕相机里没找到「瓶子」"], self.base)
        self.log.record("plan", False, False, RunState(), ["没有规划出右手的抓取"], self.base)
        outcomes = [(record["outcome"], record.get("failed_step")) for record in self.records.all()]
        self.assertEqual(outcomes, [("failed", "align"), ("plan_failed", None)])

    def test_a_reset_half_way_is_not_recorded(self) -> None:
        self.log.record("plan", True, False, planned_state(), [], self.base)
        self.log.drop()
        self.log.record("home", True, True, planned_state(), [], self.base)
        self.assertEqual(self.records.all(), [])


if __name__ == "__main__":
    unittest.main()
