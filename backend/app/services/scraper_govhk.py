"""jobs.gov.hk scraper — two job tracks, both EMAIL applications.

  IT track (``govhk_gbayes`` + ``govhk_it``):
    1. 大灣區青年就業計劃 — server-rendered quickview list:
         list:   /0/tc/jobseeker/jobsearch/quickview/gbayes/?page=N
         detail: /0/tc/jobseeker/jobCard/?order=<token>&from=quickview&for=gbayes
       KEEPS ALL vacancies (IT + 一般, each tagged by its title classification);
       posting-date window is 2 months (GBAY_MAX_JOB_AGE_DAYS).
    2. 資訊及科技界 — the 「電腦及資訊科技」 vacancy category
       (Criteria.jobType=5). The search is a POST to /jobsearch/simple/ that
       stashes the criteria in a session cookie and 302s to
       /jobsearch/joblist/?direct=False; subsequent pages are plain GETs.

  一般 track (``govhk_general``): the main quickview (ALL vacancy categories,
  newest-first) filtered by the user's general keywords and excluding any
  IT-classified titles.

Application method for all three is EMAIL (contact address lives inside
申請須知), so drafts carry apply_method="email" + contact_email.
"""
from __future__ import annotations

import html as html_mod
import logging
import re

from bs4 import BeautifulSoup

from ..config import settings
from . import scan_control
from .classify import TrackConfig, classify, title_matches
from .jobdate import is_fresh
from .scraper_base import BrowserSession, JobDraft, grab_html, human_delay, open_page

log = logging.getLogger(__name__)

BASE = "https://www2.jobs.gov.hk"
GBY_PLATFORM = "govhk_gbayes"      # 大灣區青年就業計劃（IT track）
IT_PLATFORM = "govhk_it"           # 資訊及科技界（IT track）
GENERAL_PLATFORM = "govhk_general"  # 一般職位 quickview（一般 track）

GBY_LIST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/quickview/gbayes/"
QUICKVIEW_URL = f"{BASE}/0/tc/jobseeker/jobsearch/quickview/?direct=False"
SIMPLE_URL = f"{BASE}/0/tc/jobseeker/jobsearch/simple/"
JOBLIST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/joblist/"
IT_JOB_TYPE = "5"               # 「電腦及資訊科技」空缺類別
MAX_PAGES = 30

# A vacancy number looks like 21-26-0008159
JOB_ID_RE = re.compile(r"\d{2}-\d{2}-\d{7}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Exact form payload the browser sends when the user picks 「電腦及資訊科技」
# and clicks 搜尋 (captured live). IsMobile=true + Search=搜尋 are required.
IT_SEARCH_FORM = {
    "Criteria.filterId": "",
    "Criteria.jobType": IT_JOB_TYPE,
    "Criteria.displayMoreVac": "false",
    "Criteria.industry": "",
    "Criteria.salaryFr": "",
    "Criteria.salaryTo": "",
    "Criteria.searchField": "",
    "Criteria.searchByOption": "1",
    "Criteria.specEmpProgram": "",
    "SearchFor": "",
    "RefineSearch": "True",
    "IsMobile": "true",
    "isMobile": "false",
    "Search": "搜尋",
}


# ---------------------------------------------------------------- parsing

def parse_list_html(html: str) -> list[dict]:
    """Parse a quickview list page (gbayes) into raw item dicts.

    Returns [{job_id, title, salary_range, location, detail_url}].
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select("div.row.item[data-jobcard]"):
        card = row.get("data-jobcard", "")
        if not card:
            continue
        detail_url = BASE + html_mod.unescape(card) if card.startswith("/") else card
        job_id = ""
        clip = row.select_one("a.clipItBtn")
        if clip and clip.get("data-ordno"):
            job_id = clip["data-ordno"].strip()
        if not job_id:
            m = JOB_ID_RE.search(card)
            if m:
                job_id = m.group(0)
        title = row.select_one("div.d-flex.justify-content-between div")
        title = title.get_text(strip=True) if title else ""
        salary = row.select_one(".icon_salary")
        salary = salary.get_text(strip=True) if salary else ""
        loc = row.select_one(".icon_address")
        loc = loc.get_text(strip=True) if loc else ""
        items.append({
            "job_id": job_id,
            "title": title,
            "salary_range": salary,
            "location": loc,
            "detail_url": detail_url,
        })
    return items


def parse_joblist_html(html: str) -> list[dict]:
    """Parse a joblist search-result page (table) into raw item dicts.

    The joblist page is a <table> (one <tr> per vacancy) rendered after the
    POST search. Titles/links/salary/location live in sibling <span>s.
    Returns [{job_id, title, salary_range, location, detail_url}].
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for clip in soup.select("a.clipItBtn[data-ordno]"):
        job_id = clip.get("data-ordno", "").strip()
        if not job_id:
            continue
        row = clip.find_parent("tr")
        if row is None:
            continue
        title = ""
        tc = row.select_one("span.d-flex.flex-column > span")
        if tc:
            title = tc.get_text(strip=True)
        detail_url = ""
        link = row.select_one("a[id$='_orderNo_hyper']")
        if link and link.get("href"):
            href = link["href"]
            detail_url = BASE + html_mod.unescape(href) if href.startswith("/") else href

        def cell(img_substr: str) -> str:
            img = row.select_one(f"img[src*='{img_substr}']")
            if img:
                sp = img.find_next("span")
                return sp.get_text(strip=True) if sp else ""
            return ""

        items.append({
            "job_id": job_id,
            "title": title,
            "salary_range": cell("job_icon2"),
            "location": cell("fill_but3"),
            "detail_url": detail_url,
        })
    return items


def extract_email_and_person(apply_note: str) -> tuple[str, str]:
    """Extract (email, contact_person) from the 申請須知 text.

    Handles patterns like:
      求職者可電郵(recruitment@fusionbank.com)履歷表給富融銀行有限公司。如要索取收集個人資料聲明, 請與李小姐(Email)聯絡。
    """
    email = ""
    m = re.search(r"電郵\s*\(([^)]+)\)", apply_note) or EMAIL_RE.search(apply_note)
    if m:
        candidate = m.group(1) if m.lastindex else m.group(0)
        if "@" in candidate:
            email = candidate.strip().strip("()").strip()
    person = ""
    m = re.search(r"(?:請與|或與)\s*(.+?)\s*(?:\(Email\)|聯絡|接洽)", apply_note)
    if m:
        person = m.group(1).strip("，。,；; ")
    elif "聯絡人" in apply_note:
        m = re.search(r"聯絡人\s*[::：]\s*(.+)", apply_note)
        if m:
            person = m.group(1).strip("，。 ")
    return email, person


def parse_detail_html(html: str, detail_url: str) -> dict:
    """Parse the jobCard detail page into a raw detail dict."""
    soup = BeautifulSoup(html, "html.parser")
    text_of = lambda sel: (soup.select_one(sel).get_text(strip=True) if soup.select_one(sel) else "")

    job_id = ""
    ordno_el = soup.select_one("#ordNo")
    if ordno_el:
        job_id = (ordno_el.get("data-ordno") or "").strip() or ordno_el.get_text(strip=True)
    m = JOB_ID_RE.search(job_id)
    if m:
        job_id = m.group(0)

    apply_note = text_of("#openupRemark")
    email, person = extract_email_and_person(apply_note)

    jd_parts = []
    for label, sel in (
        ("職責", "#jobRemark"), ("資歷", "#eduRemark"),
        ("待遇", "#empTerm"), ("申請須知", "#openupRemark"),
        ("備註", "#propRemark"),
    ):
        val = text_of(sel)
        if val:
            jd_parts.append(f"{label}：{val}")

    emp_term = text_of("#empTerm")
    # thousands-grouped numbers: \d{1,3}(?:,\d{3})* so the trailing comma is not consumed
    m = re.search(
        r"每月\$\d{1,3}(?:,\d{3})*(?:\s*[-~]\s*\$?\d{1,3}(?:,\d{3})*)?",
        emp_term,
    )
    salary_range = m.group(0) if m else emp_term.split(",")[0].strip()

    return {
        "job_id": job_id,
        "title": text_of("#jobTitle"),
        "company": text_of("#empName"),
        "location": text_of("#locDesc"),
        "salary_range": salary_range,
        "posted_at": text_of("#postedDt"),
        "jd_text": "\n".join(jd_parts),
        "apply_note": apply_note,
        "contact_email": email,
        "contact_person": person,
        "url": detail_url,
    }


# ---------------------------------------------------------------- scraping

def _too_old(posted_at: str, max_age: int | None = None) -> bool:
    """True when the posting date parses and is older than the freshness window.

    gov.hk lists are sorted newest-first (刊登日期由近至遠), so the first job
    that falls out of the window means everything below it is older too —
    the scraper can stop that channel early.
    """
    max_age = settings.MAX_JOB_AGE_DAYS if max_age is None else max_age
    if max_age <= 0:
        return False
    return bool(posted_at) and not is_fresh(posted_at, max_age)


async def scrape(session: BrowserSession, track: str = "it",
                 cfg: TrackConfig | None = None) -> list[JobDraft]:
    """Scrape the gov.hk channels for one job track.

    - IT track: 大灣區 quickview (ALL vacancies, IT + 一般, 2-month window)
      + 資訊及科技界 category (cap).
    - general track: main quickview (all categories) filtered by the general
      keywords, excluding IT-classified titles.
    """
    cfg = cfg or TrackConfig.defaults(track)
    seen: set[str] = set()
    if track == "general":
        drafts = await _scrape_general(session, seen, cfg)
    else:
        drafts = await _scrape_gbayes(session, seen, cfg)
        drafts += await _scrape_it(session, seen, cfg)
    return drafts


async def _scrape_gbayes(session: BrowserSession, seen: set[str],
                         cfg: TrackConfig | None = None) -> list[JobDraft]:
    """大灣區青年就業計劃: quickview pages — KEEP ALL vacancies (IT + 一般).

    No IT keyword filter: every GBA job is fetched and tagged by its title
    classification (it / general). Posting-date window is 2 months
    (GBAY_MAX_JOB_AGE_DAYS); the list is sorted newest-first so the first job
    older than the window stops the channel.
    """
    cfg = cfg or TrackConfig.defaults("it")
    drafts: list[JobDraft] = []

    for page_no in range(1, MAX_PAGES + 1):
        if scan_control.stop_requested():
            log.info("govhk gbayes: stop requested at page %s", page_no)
            return drafts
        url = f"{GBY_LIST_URL}?page={page_no}"
        try:
            page = await open_page(session.context, url)
            page_html = await grab_html(page)
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("govhk gbayes list page %s failed: %s", page_no, e)
            break
        items = parse_list_html(page_html)
        if not items:
            break  # past the last page
        for it in items:
            if not it["job_id"] or it["job_id"] in seen:
                continue
            seen.add(it["job_id"])
            category = classify(it["title"], cfg.it_keywords)
            drafts.append(await _fetch_detail(session, it, GBY_PLATFORM, category))
            # 大灣區：刊登日期要喺兩個月（60日）之內；
            # list is sorted newest-first: first stale job -> stop this channel
            if drafts and _too_old(drafts[-1].posted_at, settings.GBAY_MAX_JOB_AGE_DAYS):
                log.info("govhk gbayes: reached posting-date window (%s), stopping channel",
                         drafts[-1].posted_at)
                return drafts
            if scan_control.stop_requested():
                log.info("govhk gbayes: stop requested mid-item — returning partial drafts")
                return drafts
        if page_no % 5 == 0:
            log.info("govhk gbayes page %s: %s items, %s drafts so far", page_no, len(items), len(drafts))
        await human_delay(0.5, 1.2)

    return drafts


async def _scrape_it(session: BrowserSession, seen: set[str],
                     cfg: TrackConfig | None = None) -> list[JobDraft]:
    """資訊及科技界 joblist (POST search + session GET pages), capped per scan.

    The category itself already restricts to IT/tech, so no extra title filter.
    """
    cfg = cfg or TrackConfig.defaults("it")
    drafts: list[JobDraft] = []

    try:
        resp = await session.context.request.post(SIMPLE_URL, form=IT_SEARCH_FORM)
        await resp.dispose()
    except Exception as e:  # noqa: BLE001
        log.warning("govhk IT search POST failed: %s", e)
        return drafts

    for page_no in range(1, MAX_PAGES + 1):
        if scan_control.stop_requested():
            log.info("govhk IT: stop requested at page %s", page_no)
            return drafts
        url = f"{JOBLIST_URL}?direct=False&page={page_no}"
        try:
            resp = await session.context.request.get(url)
            page_html = await resp.text()
            await resp.dispose()
        except Exception as e:  # noqa: BLE001
            log.warning("govhk IT list page %s failed: %s", page_no, e)
            break
        items = parse_joblist_html(page_html)
        if not items:
            break  # past the last page
        matches = [it for it in items if it["job_id"] and it["job_id"] not in seen]
        for it in matches:
            seen.add(it["job_id"])
            drafts.append(await _fetch_detail(session, it, IT_PLATFORM, "it"))
            # 資訊及科技界：每次 scan 最多 N 份（設定可改）
            if cfg.govhk_max_jobs > 0 and len(drafts) >= cfg.govhk_max_jobs:
                log.info("govhk IT: reached %s-job cap, stopping channel", len(drafts))
                return drafts
            # list is sorted newest-first: first stale job -> stop this channel
            if drafts and _too_old(drafts[-1].posted_at):
                log.info("govhk IT: reached posting-date window (%s), stopping channel",
                         drafts[-1].posted_at)
                return drafts
            if scan_control.stop_requested():
                log.info("govhk IT: stop requested mid-item — returning partial drafts")
                return drafts
        if page_no % 5 == 0:
            log.info("govhk IT page %s: %s new, %s drafts so far", page_no, len(matches), len(drafts))
        await human_delay(0.4, 1.0)

    return drafts


async def _scrape_general(session: BrowserSession, seen: set[str],
                          cfg: TrackConfig | None = None) -> list[JobDraft]:
    """一般 track: main quickview (ALL categories) filtered by general keywords.

    Same list/detail format as the gbayes quickview (div.row.item[data-jobcard]).
    IT-classified titles are excluded; the list is newest-first so the first
    stale job stops the channel; capped at cfg.govhk_max_jobs per scan.
    """
    cfg = cfg or TrackConfig.defaults("general")
    drafts: list[JobDraft] = []

    for page_no in range(1, MAX_PAGES + 1):
        if scan_control.stop_requested():
            log.info("govhk general: stop requested at page %s", page_no)
            return drafts
        url = f"{QUICKVIEW_URL}&page={page_no}"
        try:
            page = await open_page(session.context, url)
            page_html = await grab_html(page)
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("govhk general list page %s failed: %s", page_no, e)
            break
        items = parse_list_html(page_html)
        if not items:
            break  # past the last page
        matches = [
            it for it in items
            if it["job_id"] and it["job_id"] not in seen
            and title_matches(it["title"], cfg.keywords)      # keep: matches 一般 keywords
            and classify(it["title"], cfg.it_keywords) == "general"  # drop IT titles
        ]
        for it in matches:
            seen.add(it["job_id"])
            drafts.append(await _fetch_detail(session, it, GENERAL_PLATFORM, "general"))
            if cfg.govhk_max_jobs > 0 and len(drafts) >= cfg.govhk_max_jobs:
                log.info("govhk general: reached %s-job cap, stopping channel", len(drafts))
                return drafts
            if drafts and _too_old(drafts[-1].posted_at):
                log.info("govhk general: reached posting-date window (%s), stopping channel",
                         drafts[-1].posted_at)
                return drafts
            if scan_control.stop_requested():
                log.info("govhk general: stop requested mid-item — returning partial drafts")
                return drafts
        if page_no % 5 == 0:
            log.info("govhk general page %s: %s new matches, %s drafts so far", page_no, len(matches), len(drafts))
        await human_delay(0.5, 1.2)

    return drafts


async def _fetch_detail(session: BrowserSession, item: dict, platform: str,
                        category: str = "") -> JobDraft:
    try:
        page = await open_page(session.context, item["detail_url"])
        detail_html = await grab_html(page)
        await page.close()
        d = parse_detail_html(detail_html, item["detail_url"])
    except Exception as e:  # noqa: BLE001
        log.warning("govhk detail failed for %s: %s", item["job_id"], e)
        d = {"job_id": item["job_id"]}

    return JobDraft(
        platform=platform,
        job_id=d.get("job_id") or item["job_id"],
        title=d.get("title") or item["title"],
        company=d.get("company", ""),
        location=d.get("location") or item["location"],
        salary_range=item["salary_range"] or d.get("salary_range", ""),
        jd_text=d.get("jd_text", ""),
        posted_at=d.get("posted_at", ""),
        url=d.get("url", ""),
        apply_method="email",
        contact_email=d.get("contact_email", ""),
        contact_person=d.get("contact_person", ""),
        category=category,
        raw={"apply_note": d.get("apply_note", "")},
    )


async def run_once() -> list[JobDraft]:
    async with BrowserSession("govhk") as session:
        return await scrape(session)
