"""JobsDB (HK) scraper — SEEK platform, JS-rendered.

List cards expose stable data-automation attributes (verified against live DOM):
  [data-testid='job-card'], [data-automation='jobTitle'|'jobCompany'|'jobLocation'|
  'jobListingDate'|'jobShortDescription'], a[data-automation='job-list-view-job-link']
Detail: /job/<id> with [data-automation='jobAdDetails'], 'job-detail-title',
  'job-detail-location', 'advertiser-name', a[data-automation='job-detail-apply'].

The apply button navigates to /job/<id>/apply, which requires a SEEK login —
handled by the persistent BrowserSession profile (user logs in once).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from .scraper_base import BrowserSession, JobDraft, grab_html, human_delay, open_page

log = logging.getLogger(__name__)

BASE = "https://hk.jobsdb.com"
SEARCH_URL = f"{BASE}/jobs"
MAX_KEYWORDS = 4          # cap concurrent searches per scan
JOBS_PER_SEARCH = 60      # scroll a bit past first page (30/card page)

TITLE_KEYWORDS = (
    "ai", "agent", "developer", "programmer", "engineer", "frontend",
    "backend", "full stack", "full-stack", "software", "python", "javascript",
    "typescript", "llm", "machine learning", "ml ", "data", "資訊科技",
    "工程師", "程式", "系統", "軟件", "前端", "後端", "人工智能",
)


def title_matches_keywords(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in TITLE_KEYWORDS)


def _job_id_from_url(url: str) -> str:
    m = re.search(r"/job/(\d+)", url)
    return m.group(1) if m else ""


async def _scroll_search(page, target_links: int) -> None:
    """Infinite-scroll until enough job cards are visible or we give up."""
    for _ in range(8):
        count = await page.locator("[data-testid='job-card']").count()
        if count >= target_links:
            break
        await page.mouse.wheel(0, 5000)
        await human_delay(0.8, 1.6)


async def scrape(session: BrowserSession, keywords: list[str] | None = None) -> list[JobDraft]:
    from ..config import settings

    kws = (keywords or settings.keywords)[:MAX_KEYWORDS]
    drafts: list[JobDraft] = []
    seen: set[str] = set()

    for kw in kws:
        if not kw:
            continue
        url = f"{SEARCH_URL}?keywords={kw}&location=Hong%20Kong"
        try:
            page = await open_page(session.context, url)
            await _scroll_search(page, JOBS_PER_SEARCH)
            cards = page.locator("[data-testid='job-card']")
            n = await cards.count()
            for i in range(n):
                card = cards.nth(i)
                link = card.locator("[data-automation='job-list-view-job-link']")
                href = await link.get_attribute("href") if await link.count() else None
                if not href:
                    continue
                job_id = _job_id_from_url(href)
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                title = await _text(card, "[data-automation='jobTitle']")
                if not title_matches_keywords(title):
                    continue
                drafts.append(JobDraft(
                    platform="jobsdb",
                    job_id=job_id,
                    title=title,
                    url=urljoin(BASE, href.split("#")[0]),
                    company=await _text(card, "[data-automation='jobCompany']"),
                    location=await _text(card, "[data-automation='jobLocation']"),
                    posted_at=await _text(card, "[data-automation='jobListingDate']"),
                    jd_text="",  # detail fetched on demand (see jobs router refresh)
                    raw={"short_desc": await _text(card, "[data-automation='jobShortDescription']")},
                ))
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("jobsdb search '%s' failed: %s", kw, e)
        await human_delay(1.0, 3.0)

    return drafts


async def _text(locator, sel: str) -> str:
    el = locator.locator(sel).first
    if await el.count():
        return (await el.inner_text()).strip()
    return ""


async def fetch_detail(session: BrowserSession, draft: JobDraft) -> JobDraft:
    """Fetch full JD + apply info for a JobsDB job (called on demand)."""
    try:
        page = await open_page(session.context, draft.url)
        draft.jd_text = (await page.locator("[data-automation='jobAdDetails']").first.inner_text()).strip()
        loc = await page.locator("[data-automation='job-detail-location']").first.inner_text()
        if loc:
            draft.location = loc.strip()
        company = await page.locator("[data-automation='advertiser-name']").first.inner_text()
        if company:
            draft.company = company.strip()
        apply_el = page.locator("a[data-automation='job-detail-apply'], a[data-automation*='apply']").first
        if await apply_el.count():
            href = await apply_el.get_attribute("href") or ""
            if href.startswith("http"):
                draft.external_url = href
                draft.apply_method = "external_link"
        # salary if shown on detail
        for sel in ("[data-automation='job-salary']", "[data-automation='jobSalary']"):
            if await page.locator(sel).count():
                draft.salary_range = (await page.locator(sel).first.inner_text()).strip()
                break
        await page.close()
    except Exception as e:  # noqa: BLE001
        log.warning("jobsdb detail failed for %s: %s", draft.job_id, e)
    return draft


async def run_once() -> list[JobDraft]:
    async with BrowserSession("jobsdb") as session:
        return await scrape(session)
