"""抓取评估记录（抓取规划页）：每次真机任务一行 JSON（data/grasp_records.jsonl）——抓了什么、在哪、哪只手、哪个候选、
用什么算法排的序、对准偏了多少、每步多久、结果如何（成功，或在哪一步因为什么失败，瓶子最后是否立着）。用来研究什么样的
抓取位置更合适。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
import re
import threading
from collections import defaultdict

from loguru import logger

from config.settings import PathConfig, use_utf8_output


class GraspRecords:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PathConfig.DATA_DIR / "grasp_records.jsonl"
        self.lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info("评估记录：{} {} → {}", record.get("action"), record.get("arm"), record.get("outcome"))

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    def recent(self, limit: int = 30) -> list[dict]:
        recent = self.all()[-limit:][::-1]
        return recent

    def summary(self) -> dict:
        """Attempts that got past planning, and how many succeeded: overall, by arm, by ranking mode, by task."""
        groups: dict[str, dict[str, list[int]]] = {"arm": defaultdict(lambda: [0, 0]), "mode": defaultdict(lambda: [0, 0]), "action": defaultdict(lambda: [0, 0])}
        total = [0, 0]
        plan_failed = 0
        for record in self.all():
            if record.get("outcome") == "plan_failed":
                plan_failed += 1
                continue
            ok = int(record.get("outcome") == "ok")
            total[0] += 1
            total[1] += ok
            for key, value in (("arm", record.get("arm")), ("mode", (record.get("ranking") or {}).get("mode", "baseline")), ("action", record.get("action"))):
                groups[key][value or "—"][0] += 1
                groups[key][value or "—"][1] += ok
        summary = {"attempts": total[0], "ok": total[1], "plan_failed": plan_failed,
                   **{key: {name: {"attempts": n, "ok": k} for name, (n, k) in group.items()} for key, group in groups.items()}}
        return summary


def reason_from(progress: list[str]) -> str:
    """The line that says why, before the last 失败：... line (the step's own report, not 开始 / 完成)."""
    lines = [re.sub(r"^\d\d:\d\d:\d\d ", "", line) for line in progress]
    failures = [i for i, line in enumerate(lines) if line.startswith("失败：")]
    if not failures:
        return ""
    before = [line for line in lines[: failures[-1]] if not re.match(r"^(开始|完成)：", line)]
    reason = before[-1] if before else lines[failures[-1]]
    return reason


def upright_from(progress: list[str]) -> dict | None:
    """The last upright check in the progress: standing and its height, and how often people were asked to help."""
    calls = sum("语音请人扶起" in line for line in progress)
    for line in reversed(progress):
        match = re.search(r"瓶子立着（高 ([\d.]+) cm", line)
        if match:
            return {"standing": True, "height_cm": float(match.group(1)), "help_calls": calls}
    return {"standing": False, "help_calls": calls} if calls else None


def demo_grasp_records() -> None:
    """Offline: a temporary file, two attempts and a failed plan, the summary; the reason found in a progress."""
    import tempfile

    records = GraspRecords(Path(tempfile.mkdtemp()) / "records.jsonl")
    records.append({"action": "pick_target", "arm": "left", "outcome": "ok", "ranking": {"mode": "baseline"}})
    records.append({"action": "pick_target", "arm": "right", "outcome": "failed", "ranking": {"mode": "molmo"}})
    records.append({"action": "pick_target", "arm": "right", "outcome": "plan_failed"})
    logger.info("汇总：{}", records.summary())
    progress = ["19:35:22 开始：到瓶子后方", "19:35:22 桌面守卫：这一步「approach」会让 Fixed_Jaw_2 离桌面只剩 5.8 cm（要 6 cm），不执行",
                "19:35:22 失败：到瓶子后方（真机停在原地）"]
    logger.info("失败原因：{}", reason_from(progress))
    logger.info("立着：{}", upright_from(["19:57:02 瓶子立着（高 15.1 cm，规划时 15.1 cm）"]))


def main() -> None:
    use_utf8_output()
    demo_grasp_records()


if __name__ == "__main__":
    main()
