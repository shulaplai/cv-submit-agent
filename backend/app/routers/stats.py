"""Dashboard statistics + 投遞結果漏斗（funnel）。

漏斗（用戶要求用數據決定「邊類工值得繼續投」）：已經投遞嘅工按
  - track（IT／一般）
  - AI（ai_match 係唔係 AI 相關）
  - 分數段（≥65 / 50–64 / <50）
分組，計出 已投 / 有回覆 / 面試中 / 落選 / 冇回音 / Offer / 未更新 + 回覆率。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import JobApplication
from ..schemas import FunnelRow, StatsOut

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 已經投咗（漏斗嘅分母）
APPLIED_STATUSES = ("applied", "interviewing", "rejected", "offer", "no_response")
# 有回覆（公司真係覆過）
RESPONDED_STATUSES = ("interviewing", "rejected", "offer")

DOCUMENTED_OUTCOME_STATUSES = ("interviewing", "rejected", "offer", "no_response")


def _row(key: str, label: str, rows: list[JobApplication]) -> FunnelRow:
    applied = len(rows)
    interviewing = sum(1 for r in rows if r.status == "interviewing")
    rejected = sum(1 for r in rows if r.status == "rejected")
    offer = sum(1 for r in rows if r.status == "offer")
    no_response = sum(1 for r in rows if r.status == "no_response")
    responded = interviewing + rejected + offer
    pending = sum(1 for r in rows if r.status not in DOCUMENTED_OUTCOME_STATUSES)
    return FunnelRow(
        key=key, label=label, applied=applied, responded=responded,
        interviewing=interviewing, rejected=rejected, no_response=no_response,
        offer=offer, pending=pending,
        response_rate=round(responded / applied * 100, 1) if applied else 0.0,
    )


def _score_band(score: int) -> str:
    if score >= 65:
        return "high"
    if score >= 50:
        return "mid"
    return "low"


def build_funnel(rows: list[JobApplication]) -> dict[str, list[FunnelRow]]:
    """已投遞記錄 -> 三種分組嘅漏斗。"""
    by_cat = {
        "it": _row("it", "IT 職位", [r for r in rows if r.category == "it"]),
        "general": _row("general", "一般職位", [r for r in rows if r.category == "general"]),
    }
    by_ai = {
        "ai": _row("ai", "AI 相關", [r for r in rows if r.ai_match]),
        "non_ai": _row("non_ai", "非 AI", [r for r in rows if not r.ai_match]),
    }
    bands = {"high": "≥65 分", "mid": "50–64 分", "low": "＜50 分"}
    by_score = {k: _row(k, label, [r for r in rows if _score_band(r.match_score) == k])
                for k, label in bands.items()}
    return {
        "funnel_by_category": list(by_cat.values()),
        "funnel_by_ai": list(by_ai.values()),
        "funnel_by_score": list(by_score.values()),
    }


@router.get("", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)

    base = db.query(JobApplication)
    if not settings.JOBSDB_ENABLED:
        base = base.filter(JobApplication.platform != "jobsdb")
    by_status = dict(
        base.with_entities(JobApplication.status, func.count()).group_by(JobApplication.status).all()
    )
    by_platform = dict(
        base.with_entities(JobApplication.platform, func.count()).group_by(JobApplication.platform).all()
    )

    applied_7d = (
        db.query(func.count())
        .filter(JobApplication.applied_at >= now - timedelta(days=7))
        .scalar()
        or 0
    )
    applied_30d = (
        db.query(func.count())
        .filter(JobApplication.applied_at >= now - timedelta(days=30))
        .scalar()
        or 0
    )

    # weekly applied counts for the last 8 ISO weeks
    weekly: list[dict] = []
    for i in range(7, -1, -1):
        week_start = now - timedelta(days=7 * i)
        count = (
            db.query(func.count())
            .filter(JobApplication.applied_at >= week_start - timedelta(days=7),
                    JobApplication.applied_at < week_start)
            .scalar()
            or 0
        )
        weekly.append({"week": week_start.strftime("%m-%d"), "count": count})

    # applications in the current ISO week (goal tracking)
    days_since_monday = now.weekday()
    iso_week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    applied_this_week = (
        db.query(func.count())
        .filter(JobApplication.applied_at >= iso_week_start)
        .scalar()
        or 0
    )

    # 漏斗：已經投遞嘅工（唔受 jobsdb 隱藏影響 —— 佢係你嘅申請歷史）
    applied_rows = (
        db.query(JobApplication)
        .filter(JobApplication.status.in_(APPLIED_STATUSES))
        .all()
    )
    funnel = build_funnel(applied_rows)

    return StatsOut(
        total=sum(by_status.values()),
        by_status=by_status,
        by_platform=by_platform,
        applied_last_7d=applied_7d,
        applied_last_30d=applied_30d,
        weekly_applied=weekly,
        weekly_goal=settings.GOAL_APPLICATIONS_PER_WEEK,
        applied_this_week=applied_this_week,
        funnel=list(funnel["funnel_by_category"]) + list(funnel["funnel_by_ai"])
        + list(funnel["funnel_by_score"]),
        **funnel,
    )
