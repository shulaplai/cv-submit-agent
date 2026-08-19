"""Persistence: insert drafts with dedup, helpers to mutate application state."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models import JobApplication, utcnow
from .scraper_base import JobDraft

log = logging.getLogger(__name__)


def _apply_method_for(draft: JobDraft) -> str:
    if draft.external_url:
        return "external_link"
    if draft.contact_email:
        return "email"
    return "form"


def persist_drafts(db: Session, drafts: list[JobDraft]) -> tuple[int, int, list[JobApplication]]:
    """Insert new drafts; skip duplicates by (platform, job_id_on_platform).

    Returns (new_count, duplicate_count, new_rows).
    """
    new = dup = 0
    new_rows: list[JobApplication] = []
    for d in drafts:
        exists = (
            db.query(JobApplication)
            .filter_by(platform=d.platform, job_id_on_platform=d.job_id)
            .first()
        )
        if exists:
            dup += 1
            continue
        row = JobApplication(
            platform=d.platform,
            job_id_on_platform=d.job_id,
            url=d.url,
            external_url=d.external_url,
            title=d.title,
            company=d.company,
            location=d.location,
            salary_range=d.salary_range,
            jd_text=d.jd_text,
            jd_language="en",
            posted_at=d.posted_at,
            match_score=0,
            apply_method=_apply_method_for(d),
            contact_email=d.contact_email,
            contact_person=d.contact_person,
            status="pending_review",
        )
        db.add(row)
        new += 1
        new_rows.append(row)
    db.commit()
    return new, dup, new_rows


def mark_applied(db: Session, job_id: int) -> JobApplication | None:
    row = db.get(JobApplication, job_id)
    if row:
        row.status = "applied"
        row.applied_at = row.applied_at or utcnow()
        db.commit()
    return row
