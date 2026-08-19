"""Dashboard statistics."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import JobApplication
from ..schemas import StatsOut

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)

    by_status = dict(
        db.query(JobApplication.status, func.count()).group_by(JobApplication.status).all()
    )
    by_platform = dict(
        db.query(JobApplication.platform, func.count()).group_by(JobApplication.platform).all()
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

    return StatsOut(
        total=sum(by_status.values()),
        by_status=by_status,
        by_platform=by_platform,
        applied_last_7d=applied_7d,
        applied_last_30d=applied_30d,
        weekly_applied=weekly,
    )
