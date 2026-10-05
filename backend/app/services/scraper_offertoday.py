"""OfferToday (HK direct-hire platform) scraper — server-rendered MUI pages.

List: /hk/search/jobs-<category>/<code>  (infinite scroll; verified live)
  IT track category codes: 資訊科技=118000, 工程師=112000, 科技=127000
  一般 track keyword search: /hk/search/<keyword>-jobs  (verified live)
  card links: a[href*='/hk/job/']
Detail: /hk/job/<base64-token>  — job id = the token itself.
  Publish date: the detail page embeds JSON-LD (schema.org JobPosting) with a
  ``datePosted`` field (ISO yyyy-mm-dd) — extracted as the posting date.
Apply flow: click #J_apply (opens message + CV form, may require login).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote, urljoin

from .classify import (PRIORITY_SCAN_SLACK, TrackConfig, classify, is_priority_job,
                       title_matches)
from .scraper_base import BrowserSession, JobDraft, human_delay, open_page
from . import scan_control

log = logging.getLogger(__name__)

BASE = "https://www.offertoday.com"
# IT track: verified category pages.
SEARCH_URLS = (
    f"{BASE}/hk/search/jobs-%E8%B3%87%E8%A8%8A%E7%A7%91%E6%8A%80/118000",  # 資訊科技
    f"{BASE}/hk/search/jobs-%E5%B7%A5%E7%A8%8B%E5%B8%AB/112000",          # 工程師
    f"{BASE}/hk/search/jobs-%E7%A7%91%E6%8A%80/127000",                    # 科技
)
MAX_SCROLLS = 12
SALARY_RE = re.compile(r"(?:HK\s*\$|HK\$)?\s*\$?[\d,]+(?:K|M)?\s*(?:-\s*\$?[\d,]+(?:K|M)?)?\s*/?(?:月|小時|日)?")
# JSON-LD jobPosting on the detail page: {"datePosted": "2026-08-05", ...}
DATEPOSTED_RE = re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def _search_targets(cfg: TrackConfig) -> list[tuple[str, str, bool]]:
    """[(url, 搜尋字詞／分類頁名, 係唔係 AI 搜尋組)].

    IT track：
      1. 3 個技術分類頁（資訊科技／工程師／科技）
      2. **AI 搜尋組**（cfg.ai_search_terms，例如 agent／AI）—— 獨立預算，唔會
         同其他字詞爭，而且呢組嘅工有 7 日刊登日期限制（見 scanner）
      3. 其他 IT 關鍵字搜尋（cfg.offertoday_search_terms，會剔走 AI 組已用嘅字詞）
    general track：只有關鍵字搜尋。
    """
    from .tuning import is_ai_search_query

    if cfg.name != "it":
        terms = (cfg.offertoday_search_terms or cfg.keywords)[
            : cfg.max_searches or len(cfg.keywords)]
        return [(f"{BASE}/hk/search/{quote(t)}-jobs", t, False) for t in terms]

    targets: list[tuple[str, str, bool]] = [(u, "category", False) for u in SEARCH_URLS]
    ai_terms = (cfg.ai_search_terms or [])[
        : cfg.ai_search_max_searches or len(cfg.ai_search_terms or [])]
    for term in ai_terms:
        targets.append((f"{BASE}/hk/search/{quote(term)}-jobs", term, True))
    # 其他字詞：剔走已經做過 AI 組嘅字詞（唔想同一個字詞開兩次）
    other = [t for t in (cfg.offertoday_search_terms or [])
             if not is_ai_search_query(t)]
    other = other[: cfg.max_searches or len(other)]
    for term in other:
        targets.append((f"{BASE}/hk/search/{quote(term)}-jobs", term, False))
    return targets


def _search_urls_for(cfg: TrackConfig) -> list[str]:
    """（相容用）只要 URL。"""
    return [url for url, _q, _ai in _search_targets(cfg)]


async def _scroll_search(page, target_links: int) -> None:
    """Infinite-scroll the search page until enough job links collected."""
    for _ in range(MAX_SCROLLS):
        count = await page.locator("a[href*='/hk/job/']").count()
        if count >= target_links:
            break
        await page.mouse.wheel(0, 6000)
        await human_delay(0.6, 1.4)


async def scrape(session: BrowserSession, track: str = "it",
                 cfg: TrackConfig | None = None) -> list[JobDraft]:
    """Scrape the OfferToday search pages for one job track.

    IT track: the three tech category pages, keeping keyword-matching titles.
    general track: one search page per keyword term, keeping titles that match
    the general keywords and are NOT IT-classified.

    Soft cap: each search page contributes at most
    ``cfg.offertoday_max_per_search`` **非優先** drafts. 用戶要求：評級好高嘅工
    （標題命中優先字詞／pre-score 夠高）唔受上限限制，額外最多收
    ``cfg.priority_extra_max`` 份（豁免硬上限，防止失控）。
    """
    cfg = cfg or TrackConfig.defaults(track)
    from .cv_loader import load_skills

    skills = load_skills()
    drafts: list[JobDraft] = []
    seen: set[str] = set()

    for url, query, is_ai_group in _search_targets(cfg):
        if scan_control.stop_requested():
            log.info("offertoday: stop requested before search %s", url.rsplit("/", 1)[-1])
            return drafts
        try:
            page = await open_page(session.context, url)
            # scroll far enough to reach the soft cap AND the priority extra
            cap = cfg.offertoday_max_per_search
            extra = cfg.priority_extra_max if cfg.cap_bypass_enabled else 0
            target = (cap * 2 if cap else 150) + extra
            await _scroll_search(page, target)
            links = page.locator("a[href*='/hk/job/']")
            n = await links.count()
            taken_normal = 0
            taken_priority = 0
            misses = 0
            for i in range(n):
                if cap and taken_normal >= cap and taken_priority >= extra:
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
                if track == "it":
                    if not title_matches(title, cfg.keywords):
                        continue
                    category = "it"
                else:
                    if classify(title, cfg.it_keywords, cfg.non_it_keywords) != "general" \
                       or not title_matches(title, cfg.keywords):
                        continue
                    category = "general"
                if cap and taken_normal >= cap:
                    # 軟上限已滿：只有「優先工」先值得再開卡片文字睇分（慳時間）；
                    # 連續 PRIORITY_SCAN_SLACK 個都唔係優先工就當呢頁冇貨。
                    if not is_priority_job(title, "", skills, cfg):
                        misses += 1
                        if misses >= PRIORITY_SCAN_SLACK:
                            log.info("offertoday %s: %s normal cap reached and no priority "
                                     "job in the last %s links, moving on",
                                     url.rsplit("/", 1)[-1], cap, misses)
                            break
                        continue
                # 卡片文字要喺計數之前攞：豁免判斷（優先字詞／技能重疊分）需要佢
                card_text = (await link.evaluate(
                    "el => { let p = el; for (let i=0;i<3 && p.parentElement;i++) p = p.parentElement; return p.innerText; }"
                )) if await link.count() else ""
                priority = bool(is_priority_job(title, card_text, skills, cfg))
                if priority and taken_priority < extra:
                    taken_priority += 1
                elif (not cap) or taken_normal < cap:
                    taken_normal += 1
                else:
                    continue      # 普通額已滿，豁免額又滿 -> 唔收
                misses = 0
                seen.add(token)
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
                    category=category,
                    source_query=query,
                    raw={"card_text": card_text[:500], "priority": priority,
                         "ai_search": is_ai_group},
                ))
            log.info("offertoday %s%s: took %s normal + %s priority (cap %s + %s/search)",
                     url.rsplit("/", 1)[-1], "（AI 組）" if is_ai_group else "",
                     taken_normal, taken_priority, cap, extra)
            await page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("offertoday search %s failed: %s", url, e)
        await human_delay(1.0, 2.5)

    return drafts


async def fetch_detail(session: BrowserSession, draft: JobDraft) -> JobDraft:
    """Fetch full JD for an OfferToday job (called on demand).

    Also extracts the publish date from the embedded JSON-LD (datePosted),
    so OfferToday jobs finally carry a posting date for the freshness filter.
    """
    try:
        page = await open_page(session.context, draft.url)
        html = await page.content()
        text = await page.locator("body").inner_text()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        m = DATEPOSTED_RE.search(html)
        if m:
            draft.posted_at = m.group(1)

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
