"""系统自检（系统设置页）：服务 → 相机图像 → 关节读数 → 规划依赖 → USB 设备。只读：不开力矩、不动机器人。
每一项给出 ok（True 正常 / False 需处理 / None 提醒）、说明和处理办法。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import math
import shutil
import time

from loguru import logger

from component.process_supervision.service import Supervisor
from config.perception import HeadCameraConfig, PerceptionConfig
from config.planning import ArmConfig, DeskGuardConfig, GraspConfig
from config.settings import PathConfig, UsbConfig, use_utf8_output
from util.teleop_helper import TeleopHelper

# What should be on the USB bus (vendor:product -> role, how many).
USB_DEVICES = UsbConfig.DEVICES
PART_JOINTS = {"left_arm": 6, "right_arm": 6, "head": 2}


def item(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


class SelfCheck:
    def __init__(self, teleop: TeleopHelper | None = None, supervisor: Supervisor | None = None,
                 reaches: dict | None = None) -> None:
        self.teleop = teleop or TeleopHelper()
        self.supervisor = supervisor or Supervisor()
        self.reaches = reaches

    def run(self) -> dict:
        started = time.monotonic()
        status, status_error = self.robot_status()
        stages = [
            {"key": "services", "title": "服务接口与状态", "items": self.services(status, status_error)},
            {"key": "cameras", "title": "相机身份与有效图像", "items": self.cameras(status)},
            {"key": "joints", "title": "关节总线与读数", "items": self.joints(status)},
            {"key": "planning", "title": "规划依赖与模型", "items": self.planning()},
            {"key": "devices", "title": "USB 设备", "items": self.devices()},
        ]
        problems = [entry for stage in stages for entry in stage["items"] if entry["ok"] is False]
        warnings = [entry for stage in stages for entry in stage["items"] if entry["ok"] is None]
        for stage in stages:
            stage["ok"] = (False if any(entry["ok"] is False for entry in stage["items"])
                           else None if any(entry["ok"] is None for entry in stage["items"]) else True)
        report = {"time": time.time(), "seconds": round(time.monotonic() - started, 1), "ready": not problems,
                  "problems": len(problems), "warnings": len(warnings), "stages": stages,
                  "torque": (status or {}).get("torque"), "supervisor": {"restarts": self.supervisor.restarts}}
        return report

    def robot_status(self) -> tuple[dict | None, str]:
        try:
            first = self.teleop.status()
            time.sleep(0.5)
            second = self.teleop.status()
            second["frames_before"] = first.get("camera_frames", {})
            return second, ""
        except Exception as error:
            return None, str(error)

    # --- stages ---

    def services(self, status: dict | None, error: str) -> list[dict]:
        items = [item("网页与抓取服务", True, "本页能打开就在运行（app/main.py，8010）")]
        if status is None:
            items.append(item("机器人进程", False, f"8011 没响应：{error}",
                              "app/main.py 会在 15 s 内自动重启它；没恢复就点「重启机器人进程」，看 log/robot_*.log"))
        else:
            items.append(item("机器人进程", status.get("connected", False),
                              "已连接电机和相机" if status.get("connected") else "进程在，但没连上电机",
                              "" if status.get("connected")
                              else "检查 USB 是否挂进 WSL（launch/attach_usb.ps1），再重启机器人进程"))
            loop = status.get("loop_alive", False)
            rate = 1 / status["dt"] if status.get("dt") else 0
            items.append(item("控制循环", loop, f"{rate:.0f} Hz" if loop else "已停止",
                              "" if loop else "重启机器人进程"))
        bringup = self.supervisor.bringup_running()
        items.append(item("ROS bringup（MoveIt 的 mock）", bringup,
                          "ros2_control_node 在运行" if bringup else "没在运行",
                          "" if bringup else "重启 app/main.py（它会拉起 bringup），或看 log/bringup.log"))
        for problem in self.supervisor.check_environment():
            items.append(item("WSL 环境", False, problem, "按 doc/current_state.md 的启动步骤处理"))
        free = shutil.disk_usage(BASE_DIR).free / 1e9
        items.append(item("磁盘空间", free > 20 if free > 5 else False, f"剩 {free:.0f} GB",
                          "" if free > 20 else "清理 log/ 和 output/"))
        return items

    def cameras(self, status: dict | None) -> list[dict]:
        if status is None:
            return [item("相机", False, "机器人进程没响应，看不到相机", "先处理机器人进程")]
        items = []
        ages, frames, before = (status.get("camera_age_s", {}), status.get("camera_frames", {}),
                                status.get("frames_before", {}))
        labels = {"head": "头部 RGB", "head_depth": "头部深度", "left_wrist": "左腕相机", "right_wrist": "右腕相机"}
        for name, label in labels.items():
            age = ages.get(name)
            moving = frames.get(name, 0) > before.get(name, 0)
            if age is None:
                items.append(item(label, False, "没有这路相机", "看 log/robot_*.log 里相机连接的报错"))
            elif age > HeadCameraConfig.MAX_FRAME_AGE_S or not moving:
                items.append(item(label, False, f"画面 {age:.1f} s 没更新" + ("（帧号不动）" if not moving else ""),
                                  "头部会自动重连；腕部相机要检查 USB，或重启机器人进程"))
            else:
                items.append(item(label, True, f"最新一帧 {age * 1000:.0f} ms 前"))
        return items

    def joints(self, status: dict | None) -> list[dict]:
        if status is None:
            return [item("关节", False, "机器人进程没响应，读不到关节", "先处理机器人进程")]
        items = []
        names = {"left_arm": "左臂", "right_arm": "右臂", "head": "头部"}
        joints = status.get("joints", {})
        for part, expected in PART_JOINTS.items():
            readings = joints.get(part, {})
            bad = [joint for joint, values in readings.items()
                   if values is None or values[0] is None or not math.isfinite(values[0])]
            torque = (status.get("torque") or {}).get(part)
            if len(readings) < expected or bad:
                items.append(item(names[part], False,
                                  f"读到 {len(readings)}/{expected} 个关节" +
                                  (f"，{', '.join(bad)} 没有读数" if bad else ""),
                                  "检查这一路总线（重连 USB 后重启机器人进程）；不会自动改电机 ID"))
            else:
                away = (status.get("away_from_rest") or {}).get(part)
                note = "，不在停靠位附近（开力矩前要先摆回去）" if away else ""
                items.append(item(names[part], True if not away else None,
                                  f"{len(readings)} 个关节有读数，力矩{'开' if torque else '关'}{note}"))
        return items

    def planning(self) -> list[dict]:
        files = {
            "YOLOE 权重": PerceptionConfig.MODEL_DIR / PerceptionConfig.WEIGHTS,
            "SAM 3.1 权重": PerceptionConfig.SAM_WEIGHTS,
            "Fast-FoundationStereo 权重": PerceptionConfig.FFS_WEIGHTS,
            "GraspGenX 权重": GraspConfig.CHECKPOINT_DIR,
            "URDF": PathConfig.URDF_PATH,
            "cuRobo 右臂配置": ArmConfig.ROBOT_CONFIGS["right"],
            "cuRobo 左臂配置": ArmConfig.ROBOT_CONFIGS["left"],
        }
        items = [item(name, path.exists(),
                      str(path.relative_to(BASE_DIR)) if path.is_relative_to(BASE_DIR) else str(path),
                      "" if path.exists() else "缺文件：从备份恢复，或重新下载") for name, path in files.items()]
        calibrations = sorted((BASE_DIR / "config" / "calibration").glob("*.json"))
        items.append(item("标定文件", bool(calibrations), "，".join(path.stem for path in calibrations) or "没有",
                          "" if calibrations else "到相机标定页重新标定"))
        plane = DeskGuardConfig.PLANE_FILE
        items.append(item("桌面平面（桌面守卫）", True if plane.exists() else None,
                          f"上次测量 {time.strftime('%m-%d %H:%M', time.localtime(plane.stat().st_mtime))}"
                          if plane.exists() else "还没测过：第一次规划时测",
                          ""))
        if self.reaches is not None:
            for side, reach in self.reaches.items():
                ready = reach.reachable is not None
                label = "右手可达图" if side == "right" else "左手可达图"
                items.append(item(label, True if ready else None,
                                  f"{int(reach.reachable.sum())} 格可抓" if ready
                                  else "后台计算中（改 URDF 后要 8–10 分钟）"))
        return items

    @staticmethod
    def devices() -> list[dict]:
        found: dict[str, list[str]] = {}
        for device in Path("/sys/bus/usb/devices").glob("*"):
            try:
                hwid = f"{(device / 'idVendor').read_text().strip()}:{(device / 'idProduct').read_text().strip()}"
            except OSError:
                continue
            serial = (device / "serial").read_text().strip() if (device / "serial").exists() else ""
            found.setdefault(hwid, []).append(f"{device.name}{f'（序列号 {serial}）' if serial else ''}")
        items = []
        for hwid, (role, expected) in USB_DEVICES.items():
            where = found.get(hwid, [])
            ok = len(where) == expected
            items.append(item(role, ok, f"{len(where)}/{expected}：{'；'.join(where) or '没有'}（{hwid}）",
                              "" if ok else ("在 Windows 管理员 PowerShell 里运行 launch/attach_usb.ps1；"
                                             "RealSense 要最后挂，失败就拔插一次")))
        from component.device_matching.service import status as wrist_status

        wrists = wrist_status()
        current = wrists["mapping"]
        if wrists["problems"]:
            items.append(item("左右腕相机匹配", False, "；".join(wrists["problems"]),
                              "在下面「设备匹配」里看画面重新匹配"))
        else:
            items.append(item("左右腕相机匹配", True if current.get("confirmed") else None,
                              f"左腕 = 口 {current['left_wrist']}，右腕 = 口 {current['right_wrist']}" +
                              (f"（{current['confirmed']} 确认过）" if current.get("confirmed")
                               else "（还没在画面里确认过：张开一只夹爪看哪个画面在动）"), ""))
        return items


def demo_self_check() -> None:
    """Read-only: the full check against the running robot process."""
    report = SelfCheck().run()
    logger.info("就绪：{}，需处理 {}，提醒 {}（{} s）", report["ready"], report["problems"], report["warnings"],
                report["seconds"])
    for stage in report["stages"]:
        for entry in stage["items"]:
            mark = {True: "✓", False: "✗", None: "!"}[entry["ok"]]
            logger.info("{} [{}] {}：{} {}", mark, stage["title"], entry["name"], entry["detail"], entry["fix"])


def main() -> None:
    use_utf8_output()
    demo_self_check()


if __name__ == "__main__":
    main()
