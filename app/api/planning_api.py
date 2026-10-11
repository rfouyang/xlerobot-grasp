"""抓取规划页的接口：上一次规划的候选漏斗、语义 / 示范排序的报告和图，评估记录，下一次规划用的排序算法。"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import APIRouter, Response
from loguru import logger

from app.context import grasp_context
from config.settings import use_utf8_output

router = APIRouter(prefix="/api/planning", tags=["planning"])
# The page reaches these under /grasp-api (they were the grasp backend's before the merge).
grasp_router = APIRouter(prefix="/api/real", tags=["planning"])


@router.get("/report")
def report() -> dict:
    result = grasp_context.planning_report()
    return result


@router.get("/records")
def records(limit: int = 30) -> dict:
    """The evaluation records: the latest attempts first, and the success counts."""
    result = grasp_context.planning_records(limit)
    return result


@grasp_router.post("/algorithms")
def algorithms(request: dict) -> dict:
    """The next plan's candidate ranking (mode and its options), before planning only."""
    result = grasp_context.set_ranking(request)
    return result


@grasp_router.get("/semantic.jpg")
def semantic_image() -> Response:
    picture = grasp_context.semantic_picture()
    response = Response(picture, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    return response


def demo_planning_api() -> None:
    """Before the service starts: the report says it is not ready."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    logger.info("{}", TestClient(app).get("/api/planning/report").json())


def main() -> None:
    use_utf8_output()
    demo_planning_api()


if __name__ == "__main__":
    main()
