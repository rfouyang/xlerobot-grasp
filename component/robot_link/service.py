"""真机连接：lerobot 的 XLerobot，按部件上/下力矩，读电机状态、发动作。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import threading

from lerobot.motors.feetech import OperatingMode
from lerobot.robots.xlerobot import XLerobot, XLerobotConfig
from loguru import logger

from config.robot import CameraStreamConfig, RobotConfig
from config.settings import use_utf8_output


class RobotLink:
    """Not XLerobot.connect(): that one waits for ENTER on a terminal when a calibration file exists, and ends by
    switching every motor's torque on at whatever goal it last held. Here connecting gives no torque at all; a
    part gets torque only when switched on, and its goal is set to where it is first, so switching on never
    moves it. Motors and cameras are read separately: the control loop must not wait for camera frames."""

    def __init__(self, **kwargs) -> None:
        self.robot = XLerobot(XLerobotConfig(use_degrees=kwargs.get("use_degrees", RobotConfig.USE_DEGREES)))
        self.torque = dict.fromkeys(RobotConfig.PARTS, False)
        self.connected = False
        # The control loop and the API's torque switches share the two serial buses.
        self.lock = threading.Lock()
        # motor -> (low, high) in the units the robot speaks (degrees / 0-100), from the calibration.
        self.limits: dict[str, tuple[float, float]] = {}

    def motors(self, part: str) -> tuple:
        """part -> (bus, motor names)."""
        robot = self.robot
        buses = {"left_arm": (robot.bus1, robot.left_arm_motors), "right_arm": (robot.bus2, robot.right_arm_motors),
                 "head": (robot.bus1, robot.head_motors), "base": (robot.bus2, robot.base_motors)}
        bus_motors = buses[part]
        return bus_motors

    @staticmethod
    def with_infrared(camera) -> None:
        """lerobot's RealSense pipeline opens colour and depth only: the two infrared streams join them (8-bit, the
        colour's size and rate) when it connects."""
        import pyrealsense2 as rs

        configure = camera._configure_rs_pipeline_config

        def configure_with_infrared(rs_config) -> None:
            configure(rs_config)
            for index in (1, 2):
                rs_config.enable_stream(rs.stream.infrared, index, camera.capture_width, camera.capture_height,
                                        rs.format.y8, camera.fps)

        camera._configure_rs_pipeline_config = configure_with_infrared

    def connect(self) -> None:
        """Never cuts torque: a part that still has it (a previous run lost its link while holding, as when usbipd
        crashed) is taken over as it is, so an arm held away from the rest pose does not drop. The page then parks
        it. Only parts without torque are configured."""
        robot = self.robot
        robot.bus1.connect()
        robot.bus2.connect()
        powered = self.powered_parts()
        self.configure([part for part in RobotConfig.PARTS if part not in powered])
        for part in powered:
            self.adopt(part)
        # Which vhci port is which wrist (component/robot/devices.py, config/devices.json).
        from component.device_matching.service import mapping, path_of

        ports = mapping()
        for name, camera in robot.cameras.items():
            if name in ("left_wrist", "right_wrist"):
                camera.index_or_path = camera.config.index_or_path = path_of(ports[name])
            if name == "head":
                camera.fps = camera.config.fps = CameraStreamConfig.HEAD_FPS
                if CameraStreamConfig.HEAD_INFRARED:
                    self.with_infrared(camera)
            camera.connect()
        self.limits = self.joint_limits()
        self.connected = True
        logger.info("{} 已连接：{} 个电机，相机 {}；接管仍有力矩的部件 {}", robot.config.id,
                    len(robot.bus1.motors) + len(robot.bus2.motors), list(robot.cameras),
                    [RobotConfig.PARTS[part] for part in powered] or "无")

    def powered_parts(self) -> list[str]:
        """Parts with torque on any of their motors."""
        powered = []
        for part in RobotConfig.PARTS:
            bus, motors = self.motors(part)
            torque = bus.sync_read("Torque_Enable", motors, normalize=False, num_retry=RobotConfig.BUS_RETRY)
            if any(torque.values()):
                powered.append(part)
        return powered

    def adopt(self, part: str) -> None:
        """A part found with torque: goals set to where it is (the base to standstill), then torque on all of its
        motors, so a half-powered part becomes whole without moving."""
        self.set_torque(part, True)
        logger.warning("{} 连接时仍有力矩：已接管，停在原位", RobotConfig.PARTS[part])

    def configure(self, parts: list[str]) -> None:
        """XLerobot.configure() for these parts only, without its last step (which switches all torque on).
        configure_motors writes every motor on its bus, so it is left out for a bus that has a powered part:
        its values were written by an earlier connect and stay in the motors."""
        robot = self.robot
        retry = RobotConfig.BUS_RETRY
        for bus in (robot.bus1, robot.bus2):
            on_bus = [part for part in RobotConfig.PARTS if self.motors(part)[0] is bus]
            if all(part in parts for part in on_bus):
                bus.disable_torque(num_retry=retry)
                bus.configure_motors()
        for part in parts:
            bus, motors = self.motors(part)
            bus.disable_torque(motors, num_retry=retry)
            mode = OperatingMode.VELOCITY if part == "base" else OperatingMode.POSITION
            for name in motors:
                bus.write("Operating_Mode", name, mode.value, num_retry=retry)
                if part != "base":
                    for register, value in RobotConfig.PID.items():
                        bus.write(register, name, value, num_retry=retry)

    def joint_limits(self) -> dict[str, tuple[float, float]]:
        """The calibrated range of every arm and head motor, in the bus's normalized units."""
        limits = {}
        for part in ("left_arm", "right_arm", "head"):
            bus, motors = self.motors(part)
            for name in motors:
                calibration = bus.calibration[name]
                motor_id = bus.motors[name].id
                ends = (bus._normalize({motor_id: calibration.range_min}),
                        bus._normalize({motor_id: calibration.range_max}))
                low, high = sorted((ends[0][motor_id], ends[1][motor_id]))
                limits[name] = (low, high)
        return limits

    def observe_motors(self) -> dict[str, float]:
        """Joint positions ({motor}.pos) and the base's body velocity (x.vel, y.vel, theta.vel), no cameras."""
        robot = self.robot
        # Over usbip a status packet is lost now and then (one in ~90k reads killed the loop): read again.
        retry = RobotConfig.BUS_RETRY
        with self.lock:
            positions = {**robot.bus1.sync_read("Present_Position", robot.left_arm_motors, num_retry=retry),
                         **robot.bus2.sync_read("Present_Position", robot.right_arm_motors, num_retry=retry),
                         **robot.bus1.sync_read("Present_Position", robot.head_motors, num_retry=retry)}
            wheels = robot.bus2.sync_read("Present_Velocity", robot.base_motors, num_retry=retry)
        base = robot._wheel_raw_to_body(wheels["base_left_wheel"], wheels["base_back_wheel"],
                                        wheels["base_right_wheel"])
        observation = {**{f"{name}.pos": value for name, value in positions.items()}, **base}
        return observation

    def send(self, action: dict[str, float]) -> dict[str, float]:
        """XLerobot.send_action: {motor}.pos goals and x/y/theta.vel for the base (missing ones are 0)."""
        with self.lock:
            sent = self.robot.send_action(action)
        return sent

    def stop_base(self) -> None:
        with self.lock:
            self.robot.stop_base()

    def set_torque(self, part: str, on: bool) -> None:
        """Torque_Enable is written motor by motor and each waits for a status packet, which usbip loses now and
        then (head motor 8 did): retried, and if switching on still fails, the whole part is switched off again,
        so no part is left half powered while the loop thinks it has no torque."""
        bus, motors = self.motors(part)
        retry = RobotConfig.BUS_RETRY
        with self.lock:
            if part == "base":
                bus.sync_write("Goal_Velocity", dict.fromkeys(motors, 0), normalize=False, num_retry=retry)
            if on:
                if part != "base":
                    present = bus.sync_read("Present_Position", motors, normalize=False, num_retry=retry)
                    bus.sync_write("Goal_Position", present, normalize=False, num_retry=retry)
                try:
                    bus.enable_torque(motors, num_retry=retry)
                except ConnectionError:
                    bus.disable_torque(motors, num_retry=retry)
                    raise
            else:
                bus.disable_torque(motors, num_retry=retry)
        self.torque[part] = on
        logger.info("{} 力矩{}", RobotConfig.PARTS[part], "开" if on else "关")

    def disconnect(self) -> None:
        """lerobot's default: the base stops and every motor loses torque (a raised arm drops)."""
        robot = self.robot
        with self.lock:
            robot.stop_base()
            for bus in (robot.bus1, robot.bus2):
                bus.disconnect(disable_torque=True)
        for camera in robot.cameras.values():
            camera.disconnect()
        self.torque = dict.fromkeys(RobotConfig.PARTS, False)
        self.connected = False
        logger.info("已断开，力矩全关")


def demo_robot_link() -> None:
    """Connects without torque, reads the motors once, prints the calibrated limits; nothing moves."""
    link = RobotLink()
    link.connect()
    observation = link.observe_motors()
    for name, value in observation.items():
        low, high = link.limits.get(name.removesuffix(".pos"), (None, None))
        logger.info("{:26s} {:8.2f}   范围 {}", name, value, "-" if low is None else f"{low:7.1f} … {high:7.1f}")
    link.disconnect()


def main() -> None:
    use_utf8_output()
    demo_robot_link()


if __name__ == "__main__":
    main()
