"""The supervisor's watch: a robot process that will not start (USB unplugged) is retried after a growing wait."""

from __future__ import annotations

import unittest
from unittest import mock

from component.process_supervision.service import Supervisor
from config.settings import RobotServerConfig


class WatchTest(unittest.TestCase):
    def run_watch(self, healthy: list[bool], restarts: list[bool]) -> list[float]:
        """Runs the watch over these check answers (and restart results); returns the waits between checks."""
        supervisor = Supervisor()
        supervisor.running = True
        waits = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) > len(healthy):
                supervisor.running = False

        answers = iter(healthy + [True])
        results = iter(restarts)

        def restart(self: Supervisor) -> bool:
            self.fails = 0  # as the real one does
            return next(results)
        with (mock.patch("component.process_supervision.service.time.sleep", sleep),
              mock.patch.object(Supervisor, "robot_healthy", staticmethod(lambda: next(answers))),
              mock.patch.object(Supervisor, "restart_robot", restart)):
            supervisor.watch()
        return waits[:len(healthy)]

    def test_failed_restarts_back_off(self) -> None:
        waits = self.run_watch([False] * 9, [False] * 7)
        check = RobotServerConfig.CHECK_S
        self.assertEqual(waits[:3], [check] * 3)
        self.assertEqual(waits[3:], [10.0, 20.0, 40.0, 80.0, 160.0, RobotServerConfig.RESTART_MAX_WAIT_S])

    def test_a_restart_that_works_resets_the_wait(self) -> None:
        waits = self.run_watch([False, False, False, False, False], [False, True])
        self.assertEqual(waits, [5.0, 5.0, 5.0, 10.0, 5.0])


if __name__ == "__main__":
    unittest.main()
