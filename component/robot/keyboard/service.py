"""键盘遥操作控制循环：30 Hz 读电机 → 按住的键改目标 → 发给真机；带看门狗和急停。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading
import time

from loguru import logger

from component.robot.keyboard.arm_teleop import ArmTeleop
from component.robot.keyboard.head_base_teleop import BaseTeleop, HeadTeleop
from component.robot.link import RobotLink
from config.robot import RobotConfig, TeleopConfig
from config.settings import use_utf8_output


class KeyboardTeleop:
    """One loop drives the whole robot. A part without torque only follows: its targets are set to where it is
    every pass, so switching it on later starts from its real pose. The keys come from the page; if they stop
    arriving for WATCHDOG_S they count as released. Esc / Space (or stop()) freezes everything where it is."""

    def __init__(self, link: RobotLink) -> None:
        self.link = link
        keymap = TeleopConfig.KEYMAP
        self.arms = {"left_arm": ArmTeleop("left", keymap["left_arm"], link.limits),
                     "right_arm": ArmTeleop("right", keymap["right_arm"], link.limits)}
        self.head = HeadTeleop(keymap["head"], link.limits)
        self.base = BaseTeleop(keymap["base"])
        self.stop_codes = set(keymap["stop"].values())
        # Page (connection) -> (held keys, when they last arrived). Every open page sends its own keys; the held
        # set is their union, so one page's heartbeat never releases a key held on another.
        self.clients: dict[str, tuple[set[str], float]] = {}
        self.stop_requested = False
        # Parts walking to their rest pose before losing torque: their keys are ignored until then (Esc still
        # cancels, which freezes them where they are).
        self.parking: set[str] = set()
        # Follow mode: part -> (joint targets, when they arrived), streamed by an outside planner. Once stopped
        # (Esc / the stop button) following stays blocked until the planner arms it again for its next step, so a
        # trajectory still streaming cannot undo a stop.
        self.follows: dict[str, tuple[dict[str, float], float]] = {}
        self.follow_blocked = True
        self.running = False
        self.thread: threading.Thread | None = None
        # What the page shows; replaced (not mutated) every pass.
        self.state: dict = {}

    def set_keys(self, codes: list[str], client: str = "local") -> None:
        self.clients[client] = (set(codes), time.monotonic())

    def drop_client(self, client: str) -> None:
        self.clients.pop(client, None)

    def held_keys(self) -> tuple[set[str], bool]:
        """Union of the keys of every page heard from within WATCHDOG_S; and whether any page is."""
        now = time.monotonic()
        fresh = [codes for codes, at in list(self.clients.values()) if now - at < TeleopConfig.WATCHDOG_S]
        pressed = set().union(*fresh)
        return pressed, bool(fresh)

    def stop(self) -> None:
        self.stop_requested = True

    def arm_follow(self) -> None:
        self.follows.clear()
        self.follow_blocked = False

    def follow(self, part: str, joints: dict[str, float]) -> bool:
        """False when following is blocked (after a stop, until armed again)."""
        if not self.follow_blocked:
            self.follows[part] = (dict(joints), time.monotonic())
        accepted = not self.follow_blocked
        return accepted

    def end_follow(self, part: str) -> None:
        self.follows.pop(part, None)

    def following(self) -> list[str]:
        now = time.monotonic()
        parts = [part for part, (_, at) in list(self.follows.items()) if now - at < TeleopConfig.FOLLOW_WATCHDOG_S]
        return parts

    def teleop_of(self, part: str):
        teleop = self.head if part == "head" else self.arms[part]
        return teleop

    def go_to(self, part: str, pose: dict[str, float]) -> None:
        self.teleop_of(part).go_to(pose)

    def moving(self, part: str) -> bool:
        moving = self.teleop_of(part).goal is not None
        return moving

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self.loop, name="keyboard_teleop", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.running = False
        self.thread.join()

    @property
    def alive(self) -> bool:
        alive = self.thread is not None and self.thread.is_alive()
        return alive

    def loop(self) -> None:
        """A step that fails is skipped, the base stopped, and the loop goes on: on 2026-10-09 a head camera reconnect
        (lerobot counts a camera being reopened as the whole robot not connected) ended this loop for good, and the
        arms stopped answering targets and reporting their joints until a restart. If the loop ends for any reason,
        the base must not keep its last velocity: it is stopped on the way out (the arms and head keep holding their
        last goal)."""
        period = 1.0 / TeleopConfig.LOOP_HZ
        last = time.monotonic()
        failures = 0
        try:
            while self.running:
                started = time.monotonic()
                dt = started - last
                last = started
                try:
                    self.step(dt)
                    if failures:
                        logger.info("控制循环恢复（之前连续 {} 步出错）", failures)
                    failures = 0
                except Exception as error:
                    failures += 1
                    if failures in (1, 30) or failures % 300 == 0:
                        logger.warning("控制循环这一步出错（连续 {} 次），底盘已停，跳过：{}", failures, error)
                    try:
                        self.link.stop_base()
                    except Exception:
                        pass
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            if self.running:
                logger.opt(exception=True).error("控制循环意外停止：底盘已停，手臂和头保持最后的目标")
            self.link.stop_base()

    def step(self, dt: float) -> None:
        pressed, fresh = self.held_keys()
        observation = self.link.observe_motors()
        present = {part: self.present(part, observation) for part in ("left_arm", "right_arm", "head")}
        torque = self.link.torque
        stopping = self.stop_requested or bool(pressed & self.stop_codes)
        self.stop_requested = False
        if stopping:
            self.follows.clear()
            self.follow_blocked = True
        following = self.following()
        action = {}
        for part in ("left_arm", "right_arm", "head"):
            teleop = self.teleop_of(part)
            if not torque[part] or stopping:
                teleop.sync(present[part])
            else:
                if not teleop.target:
                    # Taken over with torque at connect: start from where it holds.
                    teleop.sync(present[part])
                if part in following:
                    teleop.step_follow(self.follows[part][0], dt)
                else:
                    # A part whose stream went quiet just holds: no keys for it while it is still claimed.
                    teleop.step(set() if part in self.parking or part in self.follows else pressed, dt)
                teleop.limit_lead(present[part])
            if torque[part]:
                action.update(teleop.action())
        if torque["base"] and not stopping:
            self.base.step(pressed)
        else:
            self.base.stop()
        action.update(self.base.action())
        self.link.send(action)
        self.state = {
            "time": time.time(), "dt": dt, "pressed": sorted(pressed), "keys_fresh": fresh, "stopping": stopping,
            "torque": dict(torque), "base_level": self.base.level, "base_command": self.base.action(),
            "base_velocity": {key: observation[key] for key in ("x.vel", "y.vel", "theta.vel")},
            "moving": {part: self.moving(part) for part in ("left_arm", "right_arm", "head")},
            "parking": sorted(self.parking), "following": following, "follow_blocked": self.follow_blocked,
            "joints": {part: {joint: [value, self.target(part).get(joint)] for joint, value in present[part].items()}
                       for part in present},
        }

    def present(self, part: str, observation: dict[str, float]) -> dict[str, float]:
        if part == "head":
            values = {motor: observation[f"{motor}.pos"] for motor in RobotConfig.HEAD_MOTORS}
        else:
            prefix = part.removesuffix("_arm")
            values = {joint: observation[f"{prefix}_arm_{joint}.pos"] for joint in RobotConfig.ARM_JOINTS}
        return values

    def target(self, part: str) -> dict[str, float]:
        target = self.teleop_of(part).target
        return target


def demo_keyboard_teleop() -> None:
    """Real robot, no torque anywhere: 1 s of the loop with 'right arm forward' held, which must only follow."""
    link = RobotLink()
    link.connect()
    teleop = KeyboardTeleop(link)
    teleop.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        teleop.set_keys([TeleopConfig.KEYMAP["right_arm"]["forward"]])
        time.sleep(0.1)
    state = teleop.state
    teleop.shutdown()
    link.disconnect()
    logger.info("循环间隔 {:.1f} ms，按键 {}，力矩 {}", state["dt"] * 1000, state["pressed"], state["torque"])
    for joint, (value, target) in state["joints"]["right_arm"].items():
        logger.info("  right_arm {:14s} 当前 {:7.2f}  目标 {:7.2f}", joint, value, target)


def main() -> None:
    use_utf8_output()
    demo_keyboard_teleop()


if __name__ == "__main__":
    main()
