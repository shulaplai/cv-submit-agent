"""Scanner pipeline: scrape enabled tracks (IT / 一般) -> persist -> enrich.

Each enabled track runs its own platform pass with per-track caps and keyword
filters; drafts are persisted with their ``category`` (it | general) so the
board can show one page per track. LLM budget (MAX_ENRICH_PER_SCAN) is shared
across tracks: new jobs are prioritized by keyword pre-score, leftover budget
goes to backfilling the oldest un-enriched rows.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CoverLetter, JobApplication, Profile
from . import scraper_govhk, scraper_jobsdb, scraper_offertoday
from .classify import TrackConfig, parse_keywords, resolve_general_keywords, resolve_it_keywords
from .cl_generator import generate_cl_checked
from .cv_loader import get_cv_text, load_skills
from .language import detect_language
from .llm import LLMError
from .matcher import keyword_score, score_job
from .scraper_base import get_browser
from .store import persist_drafts
from .jobdate import is_fresh
from . import scan_control

log = logging.getLogger(__name__)


def _platform_scrapers() -> tuple:
    """Enabled scrapers, in scan order. JobsDB is gated by JOBSDB_ENABLED."""
    scrapers: list[tuple] = []
    if settings.GOVHK_ENABLED:
        scrapers.append(("govhk", scraper_govhk.scrape, None))
    if settings.JOBSDB_ENABLED:
        scrapers.append(("jobsdb", scraper_jobsdb.scrape, scraper_jobsdb.fetch_detail))
    scrapers.append(("offertoday", scraper_offertoday.scrape, scraper_offertoday.fetch_detail))
    return tuple(scrapers)


PLATFORM_SCRAPERS = _platform_scrapers()

_WS_RE = re.compile(r"\s+")


def make_dup_key(company: str, title: str) -> str:
    """Normalized cross-platform duplicate key: lowercase, stripped, squashed."""
    return _WS_RE.sub(" ", f"{company} | {title}".strip().lower())


def load_track_configs(db: Session, only: str | None = None) -> list[TrackConfig]:
    """Enabled track configs from the user's Profile (Settings page).

    ``only`` = "it" | "general" forces that track even when its toggle is off
    (the sidebar has 淨IT / 淨一般 scan shortcuts). ``None`` scans every
    enabled track. Falls back to .env / built-in defaults without a profile.
    """
    profile = db.get(Profile, 1)
    it_kws = resolve_it_keywords(profile.it_keywords if profile else "")
    general_kws = resolve_general_keywords(profile.general_job_keywords if profile else "")

    cfg_it = TrackConfig(
        name="it", label="IT",
        keywords=it_kws, it_keywords=it_kws,
        govhk_max_jobs=(profile.govhk_it_max_jobs if profile else 0) or settings.GOVHK_IT_MAX_JOBS,
        offertoday_max_per_search=(profile.offertoday_it_max_per_search if profile else 0)
        or settings.OFFERTODAY_MAX_PER_SEARCH,
    )
    cfg_general = TrackConfig(
        name="general", label="一般",
        keywords=general_kws, it_keywords=it_kws,
        govhk_max_jobs=(profile.govhk_general_max_jobs if profile else 0)
        or settings.GOVHK_GENERAL_MAX_JOBS,
        offertoday_max_per_search=(profile.offertoday_general_max_per_search if profile else 0)
        or settings.OFFERTODAY_GENERAL_MAX_PER_SEARCH,
        offertoday_search_terms=(
            parse_keywords(profile.offertoday_general_search_terms if profile else "")
            or parse_keywords(settings.OFFERTODAY_GENERAL_SEARCH_TERMS)
            or general_kws
        ),
        max_searches=settings.OFFERTODAY_GENERAL_MAX_SEARCHES,
    )

    tracks: list[TrackConfig] = []
    if only == "it":
        tracks.append(cfg_it)
    elif only == "general":
        tracks.append(cfg_general)
    else:
        if (profile.it_track_enabled if profile else settings.IT_TRACK_ENABLED):
            tracks.append(cfg_it)
        if (profile.general_track_enabled if profile else settings.GENERAL_TRACK_ENABLED):
            tracks.append(cfg_general)
    return tracks


@dataclass
class ScanSummary:
    scanned: int = 0
    new_jobs: int = 0
    skipped_duplicates: int = 0
    skipped_old: int = 0
    capped: int = 0
    enriched: int = 0
    backfilled: int = 0
    low_match: int = 0
    details_fetched: int = 0   # JDs fetched (new rows + detail-only backfill)
    stopped: bool = False
    errors: list[str] = field(default_factory=list)
    # per-track breakdown for the UI: {track: {scanned, new_jobs, skipped_old, capped}}
    tracks: dict = field(default_factory=dict)


async def run_scan(db: Session, progress: dict | None = None,
                   track: str | None = None) -> ScanSummary:
    """Full scan. `progress` is a shared dict mutated in place for live UI updates.

    Polls ``scan_control.stop_requested()`` between tracks/platforms (and
    between pages inside the scrapers). When a stop is requested the loop
    breaks immediately, but drafts already scraped ARE still persisted — the
    user's 暫停 button must not lose the work done so far.
    """
    summary = ScanSummary()
    all_drafts = []

    def set_progress(platform: str, phase: str, count: int):
        if progress is not None:
            progress.update({"platform": platform, "phase": phase, "count": count})

    for tcfg in load_track_configs(db, track):
        if scan_control.stop_requested():
            log.info("scan stop requested — breaking before track %s", tcfg.name)
            summary.stopped = True
            break
        t_drafts: list = []
        t_skipped_old = 0
        t_capped = 0
        set_progress(tcfg.name, "scanning", 0)
        for platform, scrape_fn, _ in PLATFORM_SCRAPERS:
            if platform == "govhk" and not settings.GOVHK_ENABLED:
                continue
            if scan_control.stop_requested():
                log.info("scan stop requested — breaking before platform %s", platform)
                summary.stopped = True
                break
            try:
                set_progress(platform, f"scraping ({tcfg.label})", 0)
                session = await get_browser(platform)
                drafts = await scrape_fn(session, track=tcfg.name, cfg=tcfg)
                t_drafts.extend(drafts)
                summary.scanned += len(drafts)
                set_progress(platform, f"scraped ({tcfg.label})", len(drafts))
            except Exception as e:  # noqa: BLE001
                log.exception("scan failed for %s/%s", tcfg.name, platform)
                summary.errors.append(f"{tcfg.name}/{platform}: {e}")
            if scan_control.stop_requested():
                log.info("scan stop requested — stopping after platform %s", platform)
                summary.stopped = True
                break

        # freshness filter: drop jobs posted more than MAX_JOB_AGE_DAYS ago
        max_age = settings.MAX_JOB_AGE_DAYS
        if max_age > 0:
            kept: list = []
            for d in t_drafts:
                if is_fresh(d.posted_at, max_age):
                    kept.append(d)
                else:
                    t_skipped_old += 1
                    summary.skipped_old += 1
                    log.info("dropping stale job %s/%s (posted %r, >%sd old)",
                             d.platform, d.job_id, d.posted_at, max_age)
            t_drafts = kept

        # per-track global cap: at most MAX_SCAN_JOBS drafts, fair-share
        # round-robin across platforms so one platform can't crowd out others
        max_jobs = settings.MAX_SCAN_JOBS
        if max_jobs > 0 and len(t_drafts) > max_jobs:
            buckets: dict[str, list] = {}
            for d in t_drafts:
                buckets.setdefault(d.platform, []).append(d)
            capped: list = []
            while len(capped) < max_jobs and buckets:
                for pf in list(buckets):
                    if buckets[pf]:
                        capped.append(buckets[pf].pop(0))
                    if not buckets[pf]:
                        del buckets[pf]
                    if len(capped) >= max_jobs:
                        break
            t_capped = len(t_drafts) - len(capped)
            summary.capped += t_capped
            log.info("per-scan cap %s (%s track): kept %s of %s drafts",
                     max_jobs, tcfg.name, len(capped), len(t_drafts))
            t_drafts = capped

        # ensure every draft carries its track category
        for d in t_drafts:
            if not d.category:
                d.category = tcfg.name
        all_drafts.extend(t_drafts)
        summary.tracks[tcfg.name] = {
            "scanned": len(t_drafts),
            "new_jobs": 0,          # filled after persist
            "skipped_old": t_skipped_old,
            "capped": t_capped,
        }

    new_count, dup_count, new_rows = persist_drafts(db, all_drafts)
    summary.new_jobs = new_count
    summary.skipped_duplicates = dup_count
    # per-track new counts (new_rows carry their category)
    for tcfg in load_track_configs(db, track):
        if tcfg.name in summary.tracks:
            summary.tracks[tcfg.name]["new_jobs"] = sum(
                1 for r in new_rows if r.category == tcfg.name)

    # ---- LLM budget allocation -----------------------------------------
    # When a stop was requested, the drafts scraped so far are already
    # persisted above; skip the (expensive, LLM-heavy) enrich phase entirely.
    if not summary.stopped:
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

        # ---- A. fetch the full JD for EVERY new row (no LLM) ----
        # The user wants the whole board to carry a description, not just the
        # LLM-budget top-N. Detail fetch also reveals the OfferToday datePosted,
        # so stale jobs get dropped here like scan-time freshness filtering.
        sem = asyncio.Semaphore(6)
        dropped_ids: set[int] = set()
        dropped_by_track: dict[str, int] = {}

        async def fill_detail(row: JobApplication) -> None:
            async with sem:
                try:
                    if await _fill_detail(db, row, _fetch_detail_for(row.platform)):
                        dropped_ids.add(row.id)
                        summary.skipped_old += 1
                        dropped_by_track[row.category] = dropped_by_track.get(row.category, 0) + 1
                        return
                    if row.jd_text:
                        summary.details_fetched += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("detail fetch failed for %s/%s: %s",
                                row.platform, row.job_id_on_platform, e)
                    summary.errors.append(f"{row.platform}/{row.job_id_on_platform}: {e}")

        if new_rows:
            await asyncio.gather(*(fill_detail(r) for r in new_rows))
            candidates = [r for r in candidates if r.id not in dropped_ids]
            if dropped_ids:
                summary.new_jobs = max(0, summary.new_jobs - len(dropped_ids))
                for tname, n in dropped_by_track.items():
                    if tname in summary.tracks:
                        summary.tracks[tname]["new_jobs"] = max(0, summary.tracks[tname]["new_jobs"] - n)

        # ---- B. detail-only backfill: oldest rows still missing a JD ----
        if settings.DETAIL_BACKFILL_PER_SCAN > 0:
            old_rows = _detail_backfill_candidates(db, settings.DETAIL_BACKFILL_PER_SCAN)
            if old_rows:
                await asyncio.gather(*(fill_detail(r) for r in old_rows))

        remaining = max(0, budget - len(candidates[:budget]))
        tasks = []

        async def enrich(row: JobApplication, platform: str, fetch_detail, kind: str) -> None:
            async with sem:
                try:
                    set_progress(platform, f"enriching ({kind})", row.title[:40])
                    dropped = await _enrich_one(db, row, platform, fetch_detail, load_skills())
                    if dropped:
                        # OfferToday datePosted revealed the job is stale
                        # (> MAX_JOB_AGE_DAYS) — treat it like scan-time filtering.
                        summary.skipped_old += 1
                        summary.new_jobs = max(0, summary.new_jobs - 1)
                        return
                    summary.enriched += 1
                    if row.status == "low_match":
                        summary.low_match += 1
                except Exception as e:  # noqa: BLE001
                    log.exception("enrich failed for %s/%s", platform, row.job_id_on_platform)
                    summary.errors.append(f"{platform}/{row.job_id_on_platform}: {e}")

        for row in candidates[: max(0, budget)]:
            tasks.append(enrich(row, row.platform, _fetch_detail_for(row.platform), "new"))

        if tasks:
            await asyncio.gather(*tasks)

        # backfill: oldest un-enriched rows when budget remains.
        # Computed AFTER the new-row enrich so rows dropped there (stale
        # OfferToday datePosted) can't be re-processed by the backfill pass.
        backfill_rows: list[JobApplication] = []
        if remaining > 0:
            backfill_rows = _backfill_candidates(db, remaining)
            summary.backfilled = len(backfill_rows)

        backfill_tasks = [
            enrich(row, row.platform, _fetch_detail_for(row.platform), "backfill")
            for row in backfill_rows[:remaining]
        ]
        if backfill_tasks:
            await asyncio.gather(*backfill_tasks)

    set_progress("", "done", 0)
    db.commit()
    return summary


def _detail_backfill_candidates(db: Session, limit: int) -> list[JobApplication]:
    """Oldest rows (jobsdb/offertoday) still missing a JD — detail fetch only."""
    return (
        db.query(JobApplication)
        .filter(JobApplication.jd_text == "",
                JobApplication.platform.in_(("jobsdb", "offertoday")),
                JobApplication.status != "applied")
        .order_by(JobApplication.created_at.asc())
        .limit(limit)
        .all()
    )


async def _fill_detail(db: Session, row: JobApplication, fetch_detail) -> bool:
    """Fetch the full JD for one row if missing (jobsdb/offertoday only).

    Also records the posted date (incl. OfferToday's JSON-LD datePosted) and
    returns True when the row was DELETED as stale (> MAX_JOB_AGE_DAYS).
    """
    if row.jd_text or fetch_detail is None or row.platform not in ("jobsdb", "offertoday"):
        return False
    session = await get_browser(row.platform)
    draft = _draft_from_row(row)
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
        max_age = settings.MAX_JOB_AGE_DAYS
        if max_age > 0 and not is_fresh(draft.posted_at, max_age):
            log.info("dropping stale job %s/%s after detail fetch (posted %r, >%sd old)",
                     row.platform, row.job_id_on_platform, draft.posted_at, max_age)
            db.delete(row)
            db.flush()
            return True
    if draft.external_url:
        row.external_url = draft.external_url
        row.apply_method = "external_link"
    db.flush()
    return False


def _fetch_detail_for(platform: str):
    for p, _, fetch_detail in PLATFORM_SCRAPERS:
        if p == platform:
            return fetch_detail
    return None


def _backfill_candidates(db: Session, limit: int) -> list[JobApplication]:
    """Oldest rows that never got enriched (no CL and missing JD/score).

    low_match rows are excluded — they were already LLM-judged as a bad match
    and re-scoring them every scan would burn the API budget for nothing
    (refresh / 補齊 on the job detail page can still re-judge one explicitly).
    """
    sub = (
        db.query(CoverLetter.application_id)
        .distinct()
    )
    return (
        db.query(JobApplication)
        .filter(~JobApplication.id.in_(sub),
                JobApplication.status.notin_(("applied", "low_match")))
        .order_by(JobApplication.created_at.asc())
        .limit(limit)
        .all()
    )


async def _enrich_one(db: Session, row: JobApplication, platform: str,
                      fetch_detail, skills: list[str]) -> bool:
    """Detail fetch (if missing) + language + match score + CL for one job.

    Returns True when the row was DELETED (OfferToday's datePosted turned out
    to be stale — same treatment as scan-time freshness filtering).
    """
    # 1. full JD if the list only gave a stub (skips when already fetched)
    if await _fill_detail(db, row, fetch_detail):
        return True

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
    return False


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
        category=row.category,
    )
