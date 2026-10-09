"""标定结果：config/calibration/ 下每次求解一个带日期的 JSON。这里读出给标定页看的摘要（相机、内容、关键数字、方法）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import re
import time

import numpy as np
from loguru import logger

from config.charuco import CharucoConfig
from config.settings import use_utf8_output

# What each result file is about, by its name.
KINDS = [
    (r"^head_and_wrist", "头部 + 右腕", "头部相机位姿与右腕手眼"),
    (r"^head_ffs_scale", "头部", "FFS 深度尺度"),
    (r"^head_on_jaw", "头部", "头部外参（看夹爪求的）"),
    (r"^left_arm", "左臂", "左臂关节偏置"),
    (r"^left_wrist", "左腕", "左腕内参、手眼与关节偏置"),
]


def short(value) -> str | None:
    """A number or a short list of numbers for the table; None for anything bigger."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.4g}"
    if isinstance(value, dict) and all(isinstance(v, (int, float)) for v in value.values()) and len(value) <= 6:
        return "，".join(f"{k} {v:+.2f}" for k, v in value.items())
    if isinstance(value, list) and value and all(isinstance(v, (int, float)) for v in value) and len(value) <= 6:
        return "[" + ", ".join(f"{v:.3g}" for v in value) + "]"
    return None


def summarise(path: Path) -> dict:
    data = json.loads(path.read_text())
    camera, content = next(((camera, content) for pattern, camera, content in KINDS if re.search(pattern, path.stem)), ("—", path.stem))
    figures = {}
    for key, value in data.items():
        text = short(value)
        if text is not None and key not in ("views",):
            figures[key] = text
        elif isinstance(value, dict):
            for inner, sub in value.items():
                text = short(sub)
                if text is not None:
                    figures[f"{key}.{inner}"] = text
    if "fixed_jaw_T_camera_optical" in data:
        lens = np.array(data["fixed_jaw_T_camera_optical"])[:3, 3] * 1000
        figures["镜头在定指上 (mm)"] = "[" + ", ".join(f"{v:.1f}" for v in lens) + "]"
    note = data.get("method") or data.get("meaning") or data.get("note") or ""
    date = re.search(r"(\d{8})", path.stem)
    summary = {"file": path.name, "camera": camera, "content": content, "date": date.group(1) if date else "",
               "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
               "figures": dict(list(figures.items())[:8]), "note": note[:240]}
    return summary


def calibrations() -> list[dict]:
    files = sorted(CharucoConfig.RESULT_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    rows = []
    for path in files:
        try:
            rows.append(summarise(path))
        except (ValueError, KeyError) as error:
            rows.append({"file": path.name, "camera": "—", "content": f"读不出：{error}", "date": "", "modified": "", "figures": {}, "note": ""})
    return rows


def demo_versions() -> None:
    for row in calibrations():
        logger.info("{} [{}] {}：{}", row["file"], row["camera"], row["content"], row["figures"])


def main() -> None:
    use_utf8_output()
    demo_versions()


if __name__ == "__main__":
    main()
