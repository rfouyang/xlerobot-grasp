"""只重新标定头部两个电机：零位 = 相机朝正前方、画面水平；手臂的标定不动。交互式，在终端里跑。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import shutil
import time

from loguru import logger
from lerobot.motors import MotorCalibration
from lerobot.motors.feetech import OperatingMode
from lerobot.robots.xlerobot import XLerobot, XLerobotConfig

from config.settings import use_utf8_output


class HeadCalibration:
    """XLeRobot's calibration redoes the left arm and the head together; this redoes the head alone. The pose set
    in step 1 (straight ahead, level) becomes the motors' half turn (2047). Note lerobot's 0° is the middle of the
    recorded range instead, and the head's tilt range is not centred on straight ahead (16° off on this robot)."""

    def __init__(self) -> None:
        self.robot = XLerobot(XLerobotConfig())
        self.bus = self.robot.bus1
        self.motors = list(self.robot.head_motors)

    def run(self) -> Path:
        robot, bus = self.robot, self.bus
        path = robot.calibration_fpath
        backup = path.with_name(f"{path.stem}.before_head_{time.strftime('%Y%m%d_%H%M%S')}.json")
        shutil.copy2(path, backup)
        logger.info("原标定已备份：{}", backup)
        bus.connect()
        bus.disable_torque(self.motors)
        for motor in self.motors:
            bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
        input("\n第 1 步：用手把头摆正——相机朝正前方、画面水平（头部电机没有力矩，可以直接转）。摆好后按回车……")
        offsets = bus.set_half_turn_homings(self.motors)
        print("\n第 2 步：把头左右转到两头、上下转到两头（各自转到机械限位前），全部转过后按回车结束……")
        mins, maxes = bus.record_ranges_of_motion(self.motors)
        head = {motor: MotorCalibration(id=bus.motors[motor].id, drive_mode=0, homing_offset=offsets[motor],
                                        range_min=mins[motor], range_max=maxes[motor]) for motor in self.motors}
        bus.write_calibration(head, cache=False)
        robot.calibration.update(head)
        robot._save_calibration()
        bus.disconnect(disable_torque=False)
        for motor, calibration in head.items():
            logger.info("{}：homing {}，范围 {}–{}（{:.0f}°）", motor, calibration.homing_offset, calibration.range_min,
                        calibration.range_max, (calibration.range_max - calibration.range_min) * 360 / 4096)
        logger.info("已写入电机并保存：{}", path)
        return path


def demo_head_calibration() -> None:
    """Interactive: run it in a terminal with the teleop service stopped (it holds the serial ports)."""
    HeadCalibration().run()


def main() -> None:
    use_utf8_output()
    demo_head_calibration()


if __name__ == "__main__":
    main()
