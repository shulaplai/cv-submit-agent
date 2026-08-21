"""OfferToday (HK direct-hire platform) scraper — server-rendered MUI pages.

List: /hk/search/jobs-<category>/<code>  (infinite scroll; verified live)
  category codes: 資訊科技=118000, 工程師=112000, 科技=127000
  card links: a[href*='/hk/job/']
Detail: /hk/job/<base64-token>  — job id = the token itself.
Apply flow: click #J_apply (opens message + CV form, may require login).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from .scraper_base import BrowserSession, JobDraft, grab_html, human_delay, open_page
from . import scan_control

log = logging.getLogger(__name__)

BASE = "https://www.offertoday.com"
SEARCH_URLS = (
    f"{BASE}/hk/search/jobs-%E8%B3%87%E8%A8%8A%E7%A7%91%E6%8A%80/118000",  # 資訊科技
    f"{BASE}/hk/search/jobs-%E5%B7%A5%E7%A8%8B%E5%B8%AB/112000",          # 工程師
    f"{BASE}/hk/search/jobs-%E7%A7%91%E6%8A%80/127000",                    # 科技
)
MAX_SCROLLS = 12
SALARY_RE = re.compile(r"(?:HK\s*\$|HK\$)?\s*\$?[\d,]+(?:K|M)?\s*(?:-\s*\$?[\d,]+(?:K|M)?)?\s*/?(?:月|小時|日)?")

TITLE_KEYWORDS = (
    "ai", "agent", "developer", "programmer", "engineer", "frontend",
    "backend", "full stack", "full-stack", "software", "python", "javascript",
    "typescript", "llm", "machine learning", "data", "資訊科技", "工程師",
    "程式", "系統", "軟件", "前端", "後端", "人工智能", "技術員", "it ",
)


def title_matches_keywords(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in TITLE_KEYWORDS)


async def _scroll_search(page, target_links: int) -> None:
    """Infinite-scroll the search page until enough job links collected."""
    for _ in range(MAX_SCROLLS):
        count = await page.locator("a[href*='/hk/job/']").count()
        if count >= target_links:
            break
        await page.mouse.wheel(0, 6000)
        await human_delay(0.6, 1.4)


async def scrape(session: BrowserSession) -> list[JobDraft]:
    from ..config import settings

    cap = settings.OFFERTODAY_MAX_PER_SEARCH
    drafts: list[JobDraft] = []
    seen: set[str] = set()

    for url in SEARCH_URLS:
        if scan_control.stop_requested():
            log.info("offertoday: stop requested before search %s", url.rsplit("/", 1)[-1])
            return drafts
        try:
            page = await open_page(session.context, url)
            # scroll until we have ~2x the per-search cap visible, then stop
            await _scroll_search(page, cap * 2 if cap else 150)
            links = page.locator("a[href*='/hk/job/']")
            n = await links.count()
            taken = 0
            for i in range(n):
                if cap and taken >= cap:
                    break
                if scan_control.stop_requested():
                    log.info("offertoday: stop requested mid-search — returning partial drafts")
                    return drafts
                link = links.nth(i)
                href = await link.get_attribute("href")
                if not href:
                    continue
                token = href.rstrip("/").rsplit("/", 1)[-1]
                if not token or token in seen:
                    continue
                title = (await link.inner_text()).strip()
                if not title_matches_keywords(title):
                    continue
                seen.add(token)
                card_text = (await link.evaluate(
                    "el => { let p = el; for (let i=0;i<3 && p.parentElement;i++) p = p.parentElement; return p.innerText; }"
                )) if await link.count() else ""
                salary = ""
                m = SALARY_RE.search(card_text)
                if m:
                    salary = m.group(0).strip()
                drafts.append(JobDraft(
                    platform="offertoday",
                    job_id=token,
                    title=title,
                    url=urljoin(BASE, href),
                    company="",  # filled by detail fetch
                    location="",
                    salary_range=salary,
                    jd_text="",
                    raw={"card_text": card_text[:500]},
                ))
                taken += 1
            log.info("offertoday %s: took %s drafts (cap %s/search)", url.rsplit("/", 1)[-1], taken, cap)
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("offertoday search %s failed: %s", url, e)
        await human_delay(1.0, 2.5)

    return drafts


async def fetch_detail(session: BrowserSession, draft: JobDraft) -> JobDraft:
    """Fetch full JD for an OfferToday job (called on demand)."""
    try:
        page = await open_page(session.context, draft.url)
        text = await page.locator("body").inner_text()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # company: line just after the title (format "公司·行業")
        if draft.title in lines:
            idx = lines.index(draft.title)
            for cand in lines[idx + 1 : idx + 4]:
                if "·" in cand and "HK" not in cand[:6]:
                    draft.company = cand.split("·")[0].strip()
                    break
        # location: short CJK line just before the 傳送投遞消息 button
        NAV_WORDS = {"首頁", "職位", "專區", "登入", "僱主平台", "全職", "兼職",
                     "傳送投遞消息", "工作內容", "語言技能", "技能", "薪資面議",
                     "需有香港工作許可", "線上", "今日活躍", "7日內活躍", "最新"}
        try:
            anchor = lines.index("傳送投遞消息")
            for ln in reversed(lines[max(0, anchor - 10):anchor]):
                if len(ln) <= 12 and re.fullmatch(r"[\u4e00-\u9fffA-Za-z ,\-]{2,12}", ln) \
                   and ln not in NAV_WORDS \
                   and not ln.startswith(("HK", "$", "薪")) \
                   and "經驗" not in ln and "學歷" not in ln and "天/週" not in ln \
                   and "許可" not in ln:
                    draft.location = ln
                    break
        except ValueError:
            pass
        # salary: search the header block (before the JD), anchored to HK $ format
        if not draft.salary_range:
            header_end = text.find("工作內容")
            header = text[:header_end] if header_end > 0 else text[:3000]
            m = re.search(
                r"(?:HK\s*\$|薪資面議|薪資可議)\s*\$?[\d,]+(?:K|M)?"
                r"(?:\s*-\s*\$?[\d,]+(?:K|M)?)?\s*/?(?:月|小時|日)?",
                header,
            )
            if m:
                draft.salary_range = m.group(0)
        # JD: slice from 工作內容 heading
        jd_start = text.find("工作內容")
        jd_end = text.find("語言技能", jd_start) if jd_start >= 0 else -1
        if jd_start >= 0:
            end = jd_end if jd_end > jd_start else jd_start + 6000
            draft.jd_text = text[jd_start + 4 : end].strip()
        await page.close()
    except Exception as e:  # noqa: BLE001
        log.warning("offertoday detail failed for %s: %s", draft.job_id, e)
    return draft


async def run_once() -> list[JobDraft]:
    async with BrowserSession("offertoday") as session:
        return await scrape(session)
