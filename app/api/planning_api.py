"""抓取规划页的接口：上一次规划的候选漏斗、语义 / 示范排序的报告、算法选项（设置用 /grasp-api/api/real/algorithms）。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter
from loguru import logger

from config.settings import use_utf8_output
from util.demo_preferences import MODES


def create_planning_api() -> APIRouter:
    router = APIRouter()

    @router.get("/api/planning/report")
    def report():
        from app.context import grasp_context

        runner = getattr(grasp_context, "real", None)
        if runner is None:
            response = {"ready": False}
            return response
        planning, preferences = runner.planning, runner.planning.preferences
        status = runner.status()
        response = {"ready": True, "modes": list(MODES), "options": preferences.options, "report": preferences.report,
                    "stats": planning.stats, "action": status["action"], "next": status["next"], "running": status["running"] or status["auto"],
                    "plan_image": status["plan_image"], "align_image": status["align_image"], "align": status["align"],
                    "semantic_image": bool(preferences.report.get("observation_image")), "target": status["target"]}
        return response

    @router.get("/api/planning/records")
    def records(limit: int = 30):
        """The evaluation records: the latest attempts first, and the success counts."""
        from app.context import grasp_context

        runner = getattr(grasp_context, "real", None)
        if runner is None:
            response = {"recent": [], "summary": {}}
            return response
        response = {"recent": runner.records.recent(limit), "summary": runner.records.summary()}
        return response

    return router


def demo_planning_api() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_planning_api())
    logger.info("{}", TestClient(app).get("/api/planning/report").json())


def main() -> None:
    use_utf8_output()
    demo_planning_api()


if __name__ == "__main__":
    main()
