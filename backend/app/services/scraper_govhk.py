"""jobs.gov.hk — Greater Bay Area Youth Employment Scheme vacancy scraper.

Two server-rendered pages, verified against live HTML:
  list:   /0/tc/jobseeker/jobsearch/quickview/gbayes/?page=N
  detail: /0/tc/jobseeker/jobCard/?order=<token>&from=quickview&for=gbayes

Application method for these vacancies is EMAIL (contact address is inside
the 申請須知 field), so drafts carry apply_method="email" + contact_email.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..config import settings
from .scraper_base import BrowserSession, JobDraft, grab_html, human_delay, open_page

log = logging.getLogger(__name__)

BASE = "https://www2.jobs.gov.hk"
LIST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/quickview/gbayes/"
MAX_PAGES = 30  # ~450 vacancies, 20/page
TITLE_KEYWORDS = (
    "資訊科技", "工程師", "AI", "人工智能", "developer", "programmer",
    "frontend", "前端", "IT", "軟件", "software", "程式", "系統",
    "數據", "data", "網絡", "network", "雲", "cloud", "後端", "backend",
    "全棧", "full stack", "計算機", "計算機科學",
)
# A vacancy number looks like 21-26-0008159
JOB_ID_RE = re.compile(r"\d{2}-\d{2}-\d{7}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------- parsing

def parse_list_html(html: str) -> list[dict]:
    """Parse the quickview list page into raw item dicts.

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

def title_matches_keywords(title: str) -> bool:
    low = title.lower()
    return any(k.lower() in low for k in TITLE_KEYWORDS)


async def scrape(session: BrowserSession) -> list[JobDraft]:
    """Scrape all quickview pages; fetch detail only for keyword-matching titles."""
    drafts: list[JobDraft] = []
    seen_ids: set[str] = set()

    for page_no in range(1, MAX_PAGES + 1):
        url = f"{LIST_URL}?page={page_no}"
        try:
            page = await open_page(session.context, url)
            page_html = await grab_html(page)
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("govhk list page %s failed: %s", page_no, e)
            break
        items = parse_list_html(page_html)
        if not items:
            break  # past the last page
        matches = [it for it in items if it["job_id"] and it["job_id"] not in seen_ids
                   and title_matches_keywords(it["title"])]
        for it in matches:
            seen_ids.add(it["job_id"])
            drafts.append(await _fetch_detail(session, it))
        if page_no % 5 == 0:
            log.info("govhk page %s: %s new matches, %s drafts so far", page_no, len(matches), len(drafts))
        await human_delay(0.5, 1.2)

    return drafts


async def _fetch_detail(session: BrowserSession, item: dict) -> JobDraft:
    try:
        page = await open_page(session.context, item["detail_url"])
        detail_html = await grab_html(page)
        await page.close()
        d = parse_detail_html(detail_html, item["detail_url"])
    except Exception as e:  # noqa: BLE001
        log.warning("govhk detail failed for %s: %s", item["job_id"], e)
        d = {"job_id": item["job_id"]}

    return JobDraft(
        platform="govhk",
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
        raw={"apply_note": d.get("apply_note", "")},
    )


async def run_once() -> list[JobDraft]:
    async with BrowserSession("govhk") as session:
        return await scrape(session)
