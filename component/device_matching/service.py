"""两个腕部相机型号相同、没有序列号，只能按它们在 WSL 里的 USB 口（vhci 端口，跟 launch/attach_usb.ps1 的挂载顺序走）
区分左右。哪个口是左腕、哪个是右腕，记在 config/devices.json：机器人进程连相机前读它；
系统设置页可以预览、交换、确认。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import time

from loguru import logger

from config.robot import DeviceMatchConfig
from config.settings import use_utf8_output


def mapping() -> dict:
    """{"left_wrist": port, "right_wrist": port, "confirmed": time or None}; the default when there is no file."""
    if DeviceMatchConfig.FILE.exists():
        saved = json.loads(DeviceMatchConfig.FILE.read_text())
        return {**DeviceMatchConfig.DEFAULT, **saved}
    return dict(DeviceMatchConfig.DEFAULT)


def save(new: dict) -> dict:
    DeviceMatchConfig.FILE.write_text(json.dumps(new, indent=1, ensure_ascii=False))
    logger.info("设备匹配已保存：左腕 {}，右腕 {}", new["left_wrist"], new["right_wrist"])
    return new


def swap() -> dict:
    current = mapping()
    swapped = {**current, "left_wrist": current["right_wrist"], "right_wrist": current["left_wrist"], "confirmed": None,
               "changed": time.strftime("%Y-%m-%d %H:%M:%S")}
    return save(swapped)


def confirm() -> dict:
    return save({**mapping(), "confirmed": time.strftime("%Y-%m-%d %H:%M:%S")})


def path_of(port: str) -> Path:
    return Path(DeviceMatchConfig.BY_PATH.format(port=port))


def wrist_cameras() -> list[dict]:
    """Every wrist camera (DeviceMatchConfig.WRIST_NAME) on the vhci host: its port, its capture device,
    its USB device."""
    found = []
    for link in sorted(Path("/dev/v4l/by-path").glob("platform-vhci_hcd.0-usb-*-video-index0")):
        device = link.resolve()
        name_file = Path("/sys/class/video4linux") / device.name / "name"
        name = name_file.read_text().strip() if name_file.exists() else ""
        if DeviceMatchConfig.WRIST_NAME not in name:
            continue
        port = link.name.removeprefix("platform-vhci_hcd.0-usb-").removesuffix("-video-index0")
        usb = Path("/sys/class/video4linux") / device.name / "device"
        found.append({"port": port, "device": str(device), "usb": usb.resolve().parent.name if usb.exists() else "",
                      "name": name})
    return found


def status() -> dict:
    """The mapping, the cameras there, and what does not fit (a role's port with no camera, a camera with no role)."""
    current = mapping()
    cameras = wrist_cameras()
    ports = {camera["port"]: camera for camera in cameras}
    problems = []
    for role, label in (("left_wrist", "左腕"), ("right_wrist", "右腕")):
        if current[role] not in ports:
            problems.append(f"{label}的口 {current[role]} 上没有腕部相机"
                            "（挂载顺序变了？用 launch/attach_usb.ps1 重新挂，或在这里重新匹配）")
    roles = {current["left_wrist"]: "left_wrist", current["right_wrist"]: "right_wrist"}
    extra = [camera for camera in cameras if camera["port"] not in roles]
    if extra:
        problems.append("有腕部相机没分配角色：" + "，".join(camera["port"] for camera in extra))
    result = {"mapping": current, "cameras": [{**camera, "role": roles.get(camera["port"])} for camera in cameras],
              "problems": problems}
    return result


def demo_devices() -> None:
    logger.info("{}", status())


def main() -> None:
    use_utf8_output()
    demo_devices()


if __name__ == "__main__":
    main()
