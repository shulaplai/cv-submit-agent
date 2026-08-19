"""Job application endpoints: list, detail, enrich, CL versions, apply actions."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import get_db
from ..models import CoverLetter, JobApplication
from ..schemas import (
    CoverLetterEditIn,
    JobApplicationOut,
    JobListOut,
    RegenerateCLLIn,
    UpdateApplicationIn,
)
from ..services import scraper_jobsdb, scraper_offertoday
from ..services.apply_bot import open_apply
from ..services.cl_generator import generate_cl
from ..services.cv_loader import get_cv_text, load_skills
from ..services.language import detect_language
from ..services.llm import LLMError
from ..services.matcher import score_job
from ..services.scraper_base import get_browser
from ..services.store import mark_applied

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

VALID_STATUSES = {
    "pending_review", "low_match", "applied", "needs_manual_intervention",
    "failed", "interviewing", "rejected", "offer",
}


def _load(db: Session, job_id: int) -> JobApplication:
    row = db.get(JobApplication, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


@router.get("", response_model=JobListOut)
def list_jobs(status: str | None = None, platform: str | None = None, q: str = "",
              show_all: bool = False, limit: int = 100, offset: int = 0,
              db: Session = Depends(get_db)):
    query = db.query(JobApplication)
    if status:
        query = query.filter(JobApplication.status == status)
    if platform:
        query = query.filter(JobApplication.platform == platform)
    if not show_all:
        query = query.filter(JobApplication.status != "low_match")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(JobApplication.title.like(like),
                                 JobApplication.company.like(like)))
    total = query.count()
    hidden = db.query(JobApplication).filter(JobApplication.status == "low_match").count() if not show_all else 0
    rows = (query.order_by(JobApplication.created_at.desc())
            .offset(offset).limit(limit)
            .options(selectinload(JobApplication.cover_letters)).all())
    return JobListOut(items=rows, total=total, hidden_low_match=hidden)


@router.get("/{job_id}", response_model=JobApplicationOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    return _load(db, job_id)


@router.post("/{job_id}/refresh", response_model=JobApplicationOut)
async def refresh_job(job_id: int, db: Session = Depends(get_db)):
    """Fetch full JD (if missing), re-run match score and (re)generate CL."""
    row = _load(db, job_id)
    fetch_detail = {
        "jobsdb": scraper_jobsdb.fetch_detail,
        "offertoday": scraper_offertoday.fetch_detail,
    }.get(row.platform)

    if fetch_detail and not row.jd_text:
        from ..services.scraper_base import JobDraft
        draft = JobDraft(
            platform=row.platform, job_id=row.job_id_on_platform, title=row.title,
            url=row.url, company=row.company, location=row.location,
            salary_range=row.salary_range, jd_text=row.jd_text,
            posted_at=row.posted_at, apply_method=row.apply_method,
            contact_email=row.contact_email, contact_person=row.contact_person,
            external_url=row.external_url,
        )
        session = await get_browser(row.platform)
        draft = await fetch_detail(session, draft)
        row.jd_text = draft.jd_text
        if draft.company:
            row.company = draft.company
        if draft.location:
            row.location = draft.location
        if draft.salary_range:
            row.salary_range = draft.salary_range
        if draft.external_url:
            row.external_url = draft.external_url
            row.apply_method = "external_link"
        db.flush()

    row.jd_language = detect_language(row.jd_text or row.title)
    job_dict = {"title": row.title, "company": row.company, "location": row.location,
                "salary_range": row.salary_range, "jd_text": row.jd_text, "short_desc": ""}
    score, reason = await score_job(job_dict, load_skills())
    row.match_score = score
    row.match_reason = reason
    row.status = "pending_review" if score >= settings.MATCH_THRESHOLD else "low_match"

    try:
        cv_text = get_cv_text(row.jd_language)
        content = await generate_cl(cv_text, row.jd_text or row.title, job_dict, row.jd_language)
        latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                  .order_by(CoverLetter.version.desc()).first())
        db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                           content=content, version=(latest.version + 1 if latest else 1)))
    except LLMError as e:
        log.warning("CL gen failed on refresh: %s", e)

    db.commit()
    db.refresh(row)
    return row


@router.post("/{job_id}/cover-letters", response_model=JobApplicationOut)
def save_cover_letter(job_id: int, payload: CoverLetterEditIn, db: Session = Depends(get_db)):
    """Save an edited cover letter as a NEW version (history preserved)."""
    row = _load(db, job_id)
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    lang = latest.language if latest else detect_language(row.jd_text or row.title)
    db.add(CoverLetter(application_id=row.id, language=lang,
                       content=payload.content, version=(latest.version + 1 if latest else 1)))
    db.commit()
    db.refresh(row)
    return row


@router.post("/{job_id}/regenerate", response_model=JobApplicationOut)
async def regenerate_cl(job_id: int, payload: RegenerateCLLIn, db: Session = Depends(get_db)):
    row = _load(db, job_id)
    if not row.jd_text:
        raise HTTPException(status_code=400, detail="JD 未載入，請先 refresh")
    job_dict = {"title": row.title, "company": row.company, "location": row.location,
                "salary_range": row.salary_range, "jd_text": row.jd_text, "short_desc": ""}
    try:
        cv_text = get_cv_text(row.jd_language)
        content = await generate_cl(cv_text, row.jd_text, job_dict, row.jd_language,
                                    instructions=payload.instructions)
        latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                  .order_by(CoverLetter.version.desc()).first())
        db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                           content=content, version=(latest.version + 1 if latest else 1)))
        db.commit()
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"CL 生成失敗: {e}")
    db.refresh(row)
    return row


@router.post("/{job_id}/apply")
async def start_apply(job_id: int, db: Session = Depends(get_db)):
    """Open the semi-auto application flow (browser or Mail). Never submits."""
    row = _load(db, job_id)
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    cl_text = latest.content if latest else ""
    return await open_apply(row, cl_text)


@router.post("/{job_id}/mark-applied", response_model=JobApplicationOut)
def apply_done(job_id: int, db: Session = Depends(get_db)):
    row = mark_applied(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _load(db, job_id)


@router.patch("/{job_id}", response_model=JobApplicationOut)
def update_job(job_id: int, payload: UpdateApplicationIn, db: Session = Depends(get_db)):
    row = _load(db, job_id)
    if payload.status is not None:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"invalid status: {payload.status}")
        row.status = payload.status
    if payload.interview_stage is not None:
        row.interview_stage = payload.interview_stage
    if payload.notes is not None:
        row.notes = payload.notes
    db.commit()
    db.refresh(row)
    return row
