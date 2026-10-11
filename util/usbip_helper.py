"""机器人的 USB 设备挂进 WSL（usbipd-win）：开机后它们都在 Windows 上，app/main.py 启动时自动挂过来。
挂载（attach）不需要管理员；只有「共享」（bind）要管理员，而且只需做一次（重启后仍在）：launch/attach_usb.ps1。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import re
import subprocess
import time

from loguru import logger

from config.settings import UsbConfig, use_utf8_output

# One device row of `usbipd list`: bus id, VID:PID, name, state (Not shared / Shared / Attached).
ROW = re.compile(r"^\s*(\d+-\d+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.*?)\s{2,}(Not shared|Shared|Attached)\s*$")


class UsbipHelper:
    def devices(self) -> list[dict]:
        """The connected USB devices on the Windows side: busid, hwid, name, state."""
        out = subprocess.run([str(UsbConfig.USBIPD), "list"], capture_output=True, text=True, timeout=30).stdout
        found = [{"busid": m[1], "hwid": m[2].lower(), "name": m[3].strip(), "state": m[4]}
                 for m in map(ROW.match, out.splitlines()) if m]
        return found

    def attach_robot(self) -> list[str]:
        """Attaches the robot's devices that are shared but not yet in WSL: the motor buses and wrist cameras first, the
        RealSense last (it sometimes needs a moment, or a replug). Returns what still needs the user (empty: all in)."""
        if not UsbConfig.USBIPD.exists():
            return [f"没找到 usbipd（{UsbConfig.USBIPD}）：在 Windows 上安装 usbipd-win"]
        try:
            devices = self.devices()
        except (OSError, subprocess.SubprocessError) as error:
            return [f"usbipd list 失败：{error}"]
        problems = []
        for hwid, (role, expected) in UsbConfig.DEVICES.items():
            rows = [device for device in devices if device["hwid"] == hwid]
            if len(rows) < expected:
                problems.append(f"{role}：Windows 上只看到 {len(rows)}/{expected} 个（{hwid}），"
                                f"检查线是否插好、机器人是否上电")
            for device in rows:
                if device["state"] == "Not shared":
                    problems.append(f"{role}（{device['busid']}）还没共享："
                                    f"在 Windows 管理员 PowerShell 里运行一次 launch/attach_usb.ps1")
                elif device["state"] == "Shared":
                    result = subprocess.run([str(UsbConfig.USBIPD), "attach", "--wsl", "--busid", device["busid"]],
                                            capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        logger.info("USB 已挂进 WSL：{}（{}）", role, device["busid"])
                    else:
                        problems.append(f"{role}（{device['busid']}）挂不上："
                                        f"{(result.stderr or result.stdout).strip().splitlines()[-1:]}")
                    time.sleep(UsbConfig.SETTLE_S)
        for problem in problems:
            logger.warning("USB：{}", problem)
        return problems


def demo_usbip_helper() -> None:
    """Lists the robot's devices and attaches the shared ones (what app/main.py does at start)."""
    helper = UsbipHelper()
    for device in helper.devices():
        if device["hwid"] in UsbConfig.DEVICES:
            logger.info("{} {} {} {}", device["busid"], device["hwid"], device["state"], device["name"])
    problems = helper.attach_robot()
    logger.info("都在 WSL 里了" if not problems else "还要处理：{}", problems)


def main() -> None:
    use_utf8_output()
    demo_usbip_helper()


if __name__ == "__main__":
    main()
