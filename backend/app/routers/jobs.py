"""Job application endpoints: list, detail, enrich, CL versions, apply actions."""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import SessionLocal, get_db
from ..models import CoverLetter, JobApplication, Profile, utcnow
from ..schemas import (
    CoverLetterEditIn,
    EmailPreview,
    JobApplicationOut,
    JobListOut,
    RegenerateCLLIn,
    UpdateApplicationIn,
)
from ..services import scraper_jobsdb, scraper_offertoday
from ..services.apply_bot import open_apply
from ..services.cl_generator import generate_cl_checked
from ..services.cv_loader import get_cv_text, load_skills
from ..services.email_bot import build_email
from ..services.language import detect_language
from ..services.llm import LLMError
from ..services.matcher import score_job
from ..services.scanner import make_dup_key
from ..services.jobdate import parse_posted_date
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


def _attach_dup_counts(db: Session, rows: list[JobApplication]) -> None:
    """Set dup_count = number of OTHER rows sharing the same dup_key."""
    keys = {r.dup_key for r in rows if r.dup_key}
    if not keys:
        for r in rows:
            r.dup_count = 0
        return
    counts = dict(
        db.query(JobApplication.dup_key, func.count())
        .filter(JobApplication.dup_key.in_(keys))
        .group_by(JobApplication.dup_key)
        .all()
    )
    for r in rows:
        r.dup_count = max(0, (counts.get(r.dup_key, 0) - 1)) if r.dup_key else 0


def _split_multi(value: str | None) -> list[str]:
    """Comma-separated multi-select -> non-empty trimmed list."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_day(value: str | None, name: str, date_only: bool) -> date | datetime | None:
    """Parse a YYYY-MM-DD filter value; 400 on garbage. Returns a date (or a
    datetime when date_only is False — used for created_at ranges)."""
    if not value:
        return None
    try:
        if date_only:
            return date.fromisoformat(value)
        d = datetime.fromisoformat(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} 格式唔啱: {value}")


def _apply_filters(query, *, statuses=None, platforms=None, category=None, q="",
                   show_all=False, added_from=None, added_to=None,
                   posted_from=None, posted_to=None, min_match=None, max_match=None,
                   has_jd=False, has_cl=False, ready_to_apply=False):
    """Shared filter builder. ``statuses``/``platforms`` are lists (multi-select).
    Used both for the main query and for facet counts."""
    if not settings.JOBSDB_ENABLED:
        # JobsDB is hidden for now — keep its rows out of the board.
        query = query.filter(JobApplication.platform != "jobsdb")
    if statuses:
        query = query.filter(JobApplication.status.in_(statuses))
    if platforms:
        query = query.filter(JobApplication.platform.in_(platforms))
    if category in ("it", "general"):
        query = query.filter(JobApplication.category == category)
    if not show_all:
        query = query.filter(JobApplication.status != "low_match")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(JobApplication.title.like(like),
                                 JobApplication.company.like(like)))
    # 入庫日期 range (created_at is when the job entered the DB)
    d_from = _parse_day(added_from, "added_from", False)
    if d_from is not None:
        query = query.filter(JobApplication.created_at >= d_from)
    d_to = _parse_day(added_to, "added_to", False)
    if d_to is not None:
        query = query.filter(JobApplication.created_at < d_to + timedelta(days=1))
    # 刊登日期 range (normalized posted_date column)
    p_from = _parse_day(posted_from, "posted_from", True)
    if p_from is not None:
        query = query.filter(JobApplication.posted_date >= p_from)
    p_to = _parse_day(posted_to, "posted_to", True)
    if p_to is not None:
        query = query.filter(JobApplication.posted_date <= p_to)
    # 匹配度範圍
    if min_match is not None:
        query = query.filter(JobApplication.match_score >= min_match)
    if max_match is not None:
        query = query.filter(JobApplication.match_score <= max_match)
    # 有無 JD / 有無 CL
    if has_jd:
        query = query.filter(JobApplication.jd_text != "")
    if has_cl:
        query = query.filter(JobApplication.cover_letters.any())
    # 「可以即刻投遞」: 待處理 + 有 CL 已備 + 唔係外部網站
    if ready_to_apply:
        query = query.filter(
            JobApplication.status == "pending_review",
            JobApplication.apply_method != "external_link",
            JobApplication.cover_letters.any(),
        )
    return query


def _facets(db: Session, **filters) -> dict:
    """Per-status / per-platform counts under the active filters (excluding the
    dimension being counted) — powers the badge numbers on the filter chips."""
    status_facets: dict[str, int] = {}
    base = _apply_filters(db.query(JobApplication),
                          statuses=None, platforms=filters.get("platforms"),
                          **{k: v for k, v in filters.items() if k not in ("statuses", "platforms")})
    for s, n in base.with_entities(JobApplication.status, func.count()).group_by(JobApplication.status):
        status_facets[s] = n
    platform_facets: dict[str, int] = {}
    base = _apply_filters(db.query(JobApplication),
                          statuses=filters.get("statuses"), platforms=None,
                          **{k: v for k, v in filters.items() if k not in ("statuses", "platforms")})
    for p, n in base.with_entities(JobApplication.platform, func.count()).group_by(JobApplication.platform):
        platform_facets[p] = n
    return {"statuses": status_facets, "platforms": platform_facets}


@router.get("", response_model=JobListOut)
def list_jobs(status: str | None = None, platform: str | None = None,
              category: str | None = None, q: str = "",
              show_all: bool = False, limit: int = 100, offset: int = 0,
              sort: str = "updated",
              added_from: str | None = None, added_to: str | None = None,
              posted_from: str | None = None, posted_to: str | None = None,
              min_match: int | None = None, max_match: int | None = None,
              has_jd: bool = False, has_cl: bool = False,
              ready_to_apply: bool = False,
              db: Session = Depends(get_db)):
    """List jobs. status / platform accept comma-separated multi-selects;
    ready_to_apply = 待處理 + 有 CL + 唔係外部網站 (可以即刻投遞)."""
    filters = dict(
        statuses=_split_multi(status), platforms=_split_multi(platform),
        category=category, q=q, show_all=show_all,
        added_from=added_from, added_to=added_to,
        posted_from=posted_from, posted_to=posted_to,
        min_match=min_match, max_match=max_match,
        has_jd=has_jd, has_cl=has_cl, ready_to_apply=ready_to_apply,
    )
    query = _apply_filters(db.query(JobApplication), **filters)
    order = {
        "updated": (JobApplication.updated_at.desc(),),
        "created": (JobApplication.created_at.desc(),),   # 入庫日期（最新先）
        # 刊登日期：用正規化 posted_date 排序（無刊登日期嘅排最後）
        "posted": (JobApplication.posted_date.desc().nullslast(), JobApplication.id.desc()),
        "match": (JobApplication.match_score.desc(),),
    }.get(sort)
    if order is None:
        raise HTTPException(status_code=400, detail=f"sort 必須係 updated/created/posted/match")
    total = query.count()
    hidden_q = db.query(JobApplication).filter(JobApplication.status == "low_match")
    if not settings.JOBSDB_ENABLED:
        hidden_q = hidden_q.filter(JobApplication.platform != "jobsdb")
    hidden = hidden_q.count() if not show_all else 0
    rows = (query.order_by(*order)
            .offset(offset).limit(limit)
            .options(selectinload(JobApplication.cover_letters)).all())
    _attach_dup_counts(db, rows)
    return JobListOut(items=rows, total=total, hidden_low_match=hidden,
                      facets=_facets(db, **filters))


# ------------------------------------------------------------------ batch apply

_batch_state: dict = {"running": False, "total": 0, "done": 0, "results": []}


class BatchApplyIn(BaseModel):
    ids: list[int]
    auto: bool | None = None


async def _run_batch(ids: list[int], auto: bool | None) -> None:
    from ..services.apply_bot import open_apply
    from ..services.cl_generator import generate_cl_checked
    from ..services.cv_loader import get_cv_text

    db: Session = SessionLocal()
    try:
        profile = db.get(Profile, 1)
        effective_auto = auto if auto is not None else (
            profile.auto_submit if profile else settings.AUTO_SUBMIT
        )
        _batch_state.update({"running": True, "total": len(ids), "done": 0, "results": []})

        for job_id in ids:
            entry: dict = {"id": job_id, "title": "", "ok": False,
                           "submitted": False, "message": ""}
            try:
                row = db.get(JobApplication, job_id)
                if row is None:
                    entry["message"] = "揾唔到職位"
                elif row.status == "applied":
                    entry.update({"ok": True, "title": row.title, "message": "已經投咗，skip"})
                elif row.apply_method == "external_link":
                    entry.update({"title": row.title, "message": "外部網站，唔自動投（俾 link 你）"})
                else:
                    entry["title"] = row.title
                    # ensure a CL exists (generate on the fly if possible)
                    cl_text = ""
                    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                              .order_by(CoverLetter.version.desc()).first())
                    if latest:
                        cl_text = latest.content
                    elif row.jd_text:
                        try:
                            cv_text = get_cv_text(row.jd_language)
                            job_dict = {"title": row.title, "company": row.company,
                                        "location": row.location, "salary_range": row.salary_range,
                                        "jd_text": row.jd_text, "short_desc": ""}
                            content, _w = await generate_cl_checked(
                                cv_text, row.jd_text, job_dict, row.jd_language)
                            cl_text = content
                            db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                                               content=content, version=1))
                            db.commit()
                        except Exception as e:  # noqa: BLE001
                            entry["message"] = f"未生成 CL：{str(e)[:120]}"
                    result = await open_apply(row, cl_text, auto=effective_auto)
                    entry.update({
                        "ok": result.get("ok", False),
                        "submitted": result.get("submitted", False),
                        "message": result.get("message", ""),
                    })
                    if result.get("submitted"):
                        row.status = "applied"
                        row.applied_at = row.applied_at or utcnow()
                        db.commit()
            except Exception as e:  # noqa: BLE001
                entry["message"] = str(e)[:200]
            finally:
                _batch_state["results"].append(entry)
                _batch_state["done"] += 1
    finally:
        _batch_state["running"] = False
        db.close()


@router.post("/batch-apply")
async def batch_apply(payload: BatchApplyIn):
    """Apply to a whole checked list at once (background task, per-job results)."""
    if _batch_state["running"]:
        return {"started": False, "message": "batch 已經喺度行緊"}
    if not payload.ids:
        return {"started": False, "message": "冇揀到職位"}
    asyncio.create_task(_run_batch(payload.ids, payload.auto))
    return {"started": True, "total": len(payload.ids),
            "message": f"開始一齊投遞 {len(payload.ids)} 份（逐份處理，可睇進度）"}


@router.get("/batch-status")
def batch_status():
    return _batch_state


@router.get("/email-templates")
def email_templates():
    """List the email body templates the user can pick before sending."""
    from ..services.email_templates import list_templates
    return {"templates": list_templates()}


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
        if draft.posted_at:
            row.posted_at = draft.posted_at
            row.posted_date = parse_posted_date(draft.posted_at)
            from ..services.jobdate import is_fresh
            max_age = settings.MAX_JOB_AGE_DAYS
            if max_age > 0 and not is_fresh(draft.posted_at, max_age):
                # 手動 refresh：唔刪你撳緊嗰份工，改為標記過期（隱藏出主頁）
                row.status = "low_match"
                row.match_reason = f"刊登日期已超過 {max_age} 日，已過期"
                db.commit()
                db.refresh(row)
                return row
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
    if row.company and row.title:
        row.dup_key = make_dup_key(row.company, row.title)
    row.status = "pending_review" if score >= settings.MATCH_THRESHOLD else "low_match"

    if score >= settings.MATCH_THRESHOLD:
        try:
            cv_text = get_cv_text(row.jd_language)
            content, _warning = await generate_cl_checked(
                cv_text, row.jd_text or row.title, job_dict, row.jd_language
            )
            latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                      .order_by(CoverLetter.version.desc()).first())
            db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                               content=content, version=(latest.version + 1 if latest else 1)))
        except LLMError as e:
            log.warning("CL gen failed on refresh: %s", e)

    if not row.job_summary and row.jd_text:
        try:
            from ..services.matcher import summarize_job
            row.job_summary = await summarize_job(job_dict)
        except LLMError as e:
            log.warning("summary gen failed on refresh: %s", e)

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
        content, _warning = await generate_cl_checked(
            cv_text, row.jd_text, job_dict, row.jd_language,
            instructions=payload.instructions,
        )
        latest = (db.query(CoverLetter).filter_by(application_id=row.id)
                  .order_by(CoverLetter.version.desc()).first())
        db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                           content=content, version=(latest.version + 1 if latest else 1)))
        db.commit()
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"CL 生成失敗: {e}")
    db.refresh(row)
    return row


@router.get("/{job_id}/email-preview", response_model=EmailPreview)
def email_preview(job_id: int, template: str = "standard", db: Session = Depends(get_db)):
    """Preview the composed application email WITHOUT opening Mail."""
    row = _load(db, job_id)
    if not row.contact_email:
        raise HTTPException(status_code=400, detail="呢份工冇聯絡 email，唔可以用 email 申請")
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    from ..services.cv_loader import resolve_cv_path
    cv_path = resolve_cv_path(row.jd_language) or resolve_cv_path("zh" if row.jd_language == "en" else "en")
    email = build_email(row, latest.content if latest else "", cv_path, template)
    return EmailPreview(
        to=email["to"], contact_person=row.contact_person,
        subject=email["subject"], body=email["body"], attachment=email["attachment"],
    )


class ApplyIn(BaseModel):
    """auto=None -> follow profile/settings; True -> auto-submit; False -> manual review."""
    auto: bool | None = None
    template: str = "standard"


@router.post("/{job_id}/apply")
async def start_apply(job_id: int, payload: ApplyIn | None = None, db: Session = Depends(get_db)):
    """Run the application flow (browser or Mail).

    Auto-submits when auto=True (profile default when unset): fills the form +
    CV, clicks submit / sends the email, and marks the job applied on success.
    Never submits external-link jobs; aborts on login walls / missing CL / CV.
    """
    row = _load(db, job_id)
    latest = (db.query(CoverLetter).filter_by(application_id=row.id)
              .order_by(CoverLetter.version.desc()).first())
    cl_text = latest.content if latest else ""

    profile = db.get(Profile, 1)
    auto = payload.auto if (payload and payload.auto is not None) else (
        profile.auto_submit if profile else settings.AUTO_SUBMIT
    )
    template = payload.template if (payload and payload.template) else "standard"

    result = await open_apply(row, cl_text, auto=auto, template_key=template)
    if result.get("submitted"):
        row.status = "applied"
        row.applied_at = row.applied_at or utcnow()
        db.commit()
        result["job_id"] = row.id
    return result


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
