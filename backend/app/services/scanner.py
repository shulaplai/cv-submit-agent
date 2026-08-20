"""Scanner pipeline: scrape all platforms -> persist -> enrich (detail/match/CL).

LLM budget: per scan at most MAX_ENRICH_PER_SCAN jobs get full LLM treatment
(match + CL). New jobs are prioritized by keyword pre-score; leftover budget
goes to backfilling the oldest un-enriched rows.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CoverLetter, JobApplication
from . import scraper_govhk, scraper_jobsdb, scraper_offertoday
from .cl_generator import generate_cl_checked
from .cv_loader import get_cv_text, load_skills
from .language import detect_language
from .llm import LLMError
from .matcher import keyword_score, score_job
from .scraper_base import get_browser
from .store import persist_drafts

log = logging.getLogger(__name__)

PLATFORM_SCRAPERS = (
    ("govhk", scraper_govhk.scrape, None),
    ("jobsdb", scraper_jobsdb.scrape, scraper_jobsdb.fetch_detail),
    ("offertoday", scraper_offertoday.scrape, scraper_offertoday.fetch_detail),
)

_WS_RE = re.compile(r"\s+")


def make_dup_key(company: str, title: str) -> str:
    """Normalized cross-platform duplicate key: lowercase, stripped, squashed."""
    return _WS_RE.sub(" ", f"{company} | {title}".strip().lower())


@dataclass
class ScanSummary:
    scanned: int = 0
    new_jobs: int = 0
    skipped_duplicates: int = 0
    enriched: int = 0
    backfilled: int = 0
    low_match: int = 0
    errors: list[str] = field(default_factory=list)


async def run_scan(db: Session, progress: dict | None = None) -> ScanSummary:
    """Full scan. `progress` is a shared dict mutated in place for live UI updates."""
    summary = ScanSummary()
    all_drafts = []

    def set_progress(platform: str, phase: str, count: int):
        if progress is not None:
            progress.update({"platform": platform, "phase": phase, "count": count})

    for platform, scrape_fn, _ in PLATFORM_SCRAPERS:
        if platform == "govhk" and not settings.GOVHK_ENABLED:
            continue
        try:
            set_progress(platform, "scraping", 0)
            session = await get_browser(platform)
            drafts = await scrape_fn(session)
            all_drafts.extend(drafts)
            summary.scanned += len(drafts)
            set_progress(platform, "scraped", len(drafts))
        except Exception as e:  # noqa: BLE001
            log.exception("scan failed for %s", platform)
            summary.errors.append(f"{platform}: {e}")

    new_count, dup_count, new_rows = persist_drafts(db, all_drafts)
    summary.new_jobs = new_count
    summary.skipped_duplicates = dup_count

    # ---- LLM budget allocation -----------------------------------------
    budget = settings.MAX_ENRICH_PER_SCAN
    candidates: list[JobApplication] = []
    if new_rows:
        # new rows: prioritize by keyword pre-score (cheap, no LLM)
        skills = load_skills()
        scored = []
        for row in new_rows:
            pre = keyword_score(row.title, row.jd_text, skills)
            row.match_score = pre  # provisional; LLM re-scores if enriched
            scored.append((pre, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [r for _, r in scored]
        summary.low_match = sum(1 for s, r in scored if s < settings.MATCH_THRESHOLD)

    # backfill: oldest un-enriched rows when budget remains
    backfill_rows: list[JobApplication] = []
    if budget > 0:
        backfill_rows = _backfill_candidates(db, budget)
        summary.backfilled = len(backfill_rows)

    tasks = []
    sem = asyncio.Semaphore(6)

    async def enrich(row: JobApplication, platform: str, fetch_detail, kind: str) -> None:
        async with sem:
            try:
                set_progress(platform, f"enriching ({kind})", row.title[:40])
                await _enrich_one(db, row, platform, fetch_detail, load_skills())
                summary.enriched += 1
                if row.status == "low_match":
                    summary.low_match += 1
            except Exception as e:  # noqa: BLE001
                log.exception("enrich failed for %s/%s", platform, row.job_id_on_platform)
                summary.errors.append(f"{platform}/{row.job_id_on_platform}: {e}")

    for row in candidates[: max(0, budget)]:
        tasks.append(enrich(row, row.platform, _fetch_detail_for(row.platform), "new"))
    remaining = max(0, budget - len(candidates[:budget]))
    for row in backfill_rows[:remaining]:
        tasks.append(enrich(row, row.platform, _fetch_detail_for(row.platform), "backfill"))

    if tasks:
        await asyncio.gather(*tasks)

    set_progress("", "done", 0)
    db.commit()
    return summary


def _fetch_detail_for(platform: str):
    for p, _, fetch_detail in PLATFORM_SCRAPERS:
        if p == platform:
            return fetch_detail
    return None


def _backfill_candidates(db: Session, limit: int) -> list[JobApplication]:
    """Oldest rows that never got enriched (no CL and missing JD/score)."""
    sub = (
        db.query(CoverLetter.application_id)
        .distinct()
    )
    return (
        db.query(JobApplication)
        .filter(~JobApplication.id.in_(sub),
                JobApplication.status != "applied")
        .order_by(JobApplication.created_at.asc())
        .limit(limit)
        .all()
    )


async def _enrich_one(db: Session, row: JobApplication, platform: str,
                      fetch_detail, skills: list[str]) -> None:
    """Detail fetch (if missing) + language + match score + CL for one job."""
    # 1. full JD if the list only gave a stub
    if not row.jd_text and fetch_detail is not None and platform in ("jobsdb", "offertoday"):
        session = await get_browser(platform)
        draft = _draft_from_row(row)
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
    job_dict = {
        "title": row.title, "company": row.company, "location": row.location,
        "salary_range": row.salary_range, "jd_text": row.jd_text,
        "short_desc": "",
    }
    score, reason = await score_job(job_dict, skills)
    row.match_score = score
    row.match_reason = reason
    if row.company and row.title:
        row.dup_key = make_dup_key(row.company, row.title)
    db.flush()

    if score < settings.MATCH_THRESHOLD:
        row.status = "low_match"
        db.flush()
        return

    # 2. generate CL (with quality check + one retry)
    try:
        cv_text = get_cv_text(row.jd_language)
        content, warning = await generate_cl_checked(
            cv_text, row.jd_text or row.title, job_dict, row.jd_language
        )
        if warning:
            log.info("CL quality warning for %s/%s: %s", platform, row.job_id_on_platform, warning)
        existing = (
            db.query(CoverLetter)
            .filter_by(application_id=row.id)
            .order_by(CoverLetter.version.desc())
            .first()
        )
        version = (existing.version + 1) if existing else 1
        db.add(CoverLetter(application_id=row.id, language=row.jd_language,
                           content=content, version=version))
    except LLMError:
        log.warning("CL generation failed for %s/%s (score kept)", platform, row.job_id_on_platform)
    row.status = "pending_review"
    db.flush()

    # 3. one-glance job summary for the UI (best-effort, zh)
    if not row.job_summary and row.jd_text:
        try:
            from .matcher import summarize_job
            row.job_summary = await summarize_job(job_dict)
        except LLMError:
            log.warning("summary failed for %s/%s", platform, row.job_id_on_platform)
        db.flush()


def _draft_from_row(row: JobApplication):
    from .scraper_base import JobDraft
    return JobDraft(
        platform=row.platform,
        job_id=row.job_id_on_platform,
        title=row.title,
        url=row.url,
        company=row.company,
        location=row.location,
        salary_range=row.salary_range,
        jd_text=row.jd_text,
        posted_at=row.posted_at,
        apply_method=row.apply_method,
        contact_email=row.contact_email,
        contact_person=row.contact_person,
        external_url=row.external_url,
    )
