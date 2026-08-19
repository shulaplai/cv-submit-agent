"""Scanner pipeline: scrape all platforms -> persist -> enrich (detail/match/CL)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import JobApplication, CoverLetter
from . import scraper_govhk, scraper_jobsdb, scraper_offertoday
from .cl_generator import generate_cl
from .cv_loader import get_cv_text, load_skills
from .language import detect_language
from .llm import LLMError
from .matcher import score_job
from .scraper_base import get_browser
from .store import persist_drafts

log = logging.getLogger(__name__)

PLATFORM_SCRAPERS = (
    ("govhk", scraper_govhk.scrape, scraper_govhk.fetch_detail if hasattr(scraper_govhk, "fetch_detail") else None),
    ("jobsdb", scraper_jobsdb.scrape, scraper_jobsdb.fetch_detail),
    ("offertoday", scraper_offertoday.scrape, scraper_offertoday.fetch_detail),
)


@dataclass
class ScanSummary:
    scanned: int = 0
    new_jobs: int = 0
    skipped_duplicates: int = 0
    enriched: int = 0
    low_match: int = 0
    errors: list[str] = field(default_factory=list)


async def run_scan(db: Session) -> ScanSummary:
    """Full scan: scrape lists (all platforms), persist, then enrich new jobs."""
    summary = ScanSummary()
    all_drafts = []
    for platform, scrape_fn, _ in PLATFORM_SCRAPERS:
        if platform == "govhk" and not settings.GOVHK_ENABLED:
            continue
        try:
            session = await get_browser(platform)
            drafts = await scrape_fn(session)
            all_drafts.extend(drafts)
            summary.scanned += len(drafts)
        except Exception as e:  # noqa: BLE001
            log.exception("scan failed for %s", platform)
            summary.errors.append(f"{platform}: {e}")

    new_count, dup_count, new_rows = persist_drafts(db, all_drafts)
    summary.new_jobs = new_count
    summary.skipped_duplicates = dup_count

    if new_rows:
        skills = load_skills()
        sem = asyncio.Semaphore(6)

        async def enrich(row: JobApplication, platform: str, fetch_detail) -> None:
            async with sem:
                try:
                    await _enrich_one(db, row, platform, fetch_detail, skills)
                    summary.enriched += 1
                    if row.status == "low_match":
                        summary.low_match += 1
                except Exception as e:  # noqa: BLE001
                    log.exception("enrich failed for %s/%s", platform, row.job_id_on_platform)
                    summary.errors.append(f"{platform}/{row.job_id_on_platform}: {e}")

        jobs_by_platform = {p: [] for p, _, _ in PLATFORM_SCRAPERS}
        for row in new_rows:
            jobs_by_platform[row.platform].append(row)

        tasks = []
        for platform, _, fetch_detail in PLATFORM_SCRAPERS:
            for row in jobs_by_platform.get(platform, []):
                tasks.append(enrich(row, platform, fetch_detail))
        if tasks:
            await asyncio.gather(*tasks)

    db.commit()
    return summary


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

    if score < settings.MATCH_THRESHOLD:
        row.status = "low_match"
        db.flush()
        return

    # 2. generate CL
    try:
        cv_text = get_cv_text(row.jd_language)
        content = await generate_cl(cv_text, row.jd_text or row.title, job_dict, row.jd_language)
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
