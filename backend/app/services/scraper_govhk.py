"""jobs.gov.hk scraper — two job tracks, both EMAIL applications.

  IT track (``govhk_gbayes`` + ``govhk_it``):
    1. 大灣區青年就業計劃 — server-rendered quickview list:
         list:   /0/tc/jobseeker/jobsearch/quickview/gbayes/?page=N
         detail: /0/tc/jobseeker/jobCard/?order=<token>&from=quickview&for=gbayes
       KEEPS ALL vacancies (IT + 一般, each tagged by its title classification);
       posting-date window is 1 week (GBAY_MAX_JOB_AGE_DAYS).
    2. 資訊及科技界 — the 「電腦及資訊科技」 vacancy category
       (Criteria.jobType=5). The search is a POST to /jobsearch/search/ that
       stashes the criteria in a session cookie and 302s to
       /jobsearch/quickview/?direct=False; subsequent joblist pages are plain
       GETs. **The POST needs the antiforgery token from the joblist page**:
       without it gov.hk 302s to /404.html, the criteria never enter the
       session, and the joblist returns *every* vacancy (no filtering at all).

  一般 track (``govhk_general``): gov.hk's own search box — one search per
  general keyword (Criteria.searchField), pages of joblist/?direct=False,
  filtered by the same general keywords and excluding IT-classified titles.
  Falls back to the main quickview (ALL vacancy categories, newest-first)
  when the search form is unavailable.

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
from .classify import (PRIORITY_SCAN_SLACK, TrackConfig, classify, is_priority_job,
                       title_matches)
from .jobdate import is_fresh
from .scraper_base import BrowserSession, JobDraft, grab_html, human_delay, open_page

log = logging.getLogger(__name__)

BASE = "https://www2.jobs.gov.hk"
GBY_PLATFORM = "govhk_gbayes"      # 大灣區青年就業計劃（IT track）
IT_PLATFORM = "govhk_it"           # 資訊及科技界（IT track）
GENERAL_PLATFORM = "govhk_general"  # 一般職位 quickview（一般 track）

GBY_LIST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/quickview/gbayes/"
QUICKVIEW_URL = f"{BASE}/0/tc/jobseeker/jobsearch/quickview/?direct=False"
JOBLIST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/joblist/"
# 搜尋表單一定要 POST 去 /search/ 先會生效（實測 2026-10）。
# 舊嘅 /simple/ 已經 404：POST 去嗰邊會 302 去 /404.html，條件入唔到 session，
# 之後揭 joblist 只會回「全部空缺」，即係完全冇 filter。
SEARCH_POST_URL = f"{BASE}/0/tc/jobseeker/jobsearch/search/"
IT_JOB_TYPE = "5"               # 「電腦及資訊科技」空缺類別
MAX_PAGES = 30
# 一次掃描最多用幾多個一般關鍵字搜尋（每個搜尋 = 1 次 POST + 若干頁 GET）。
KEYWORD_SEARCH_MAX = 12

# A vacancy number looks like 21-26-0008159
JOB_ID_RE = re.compile(r"\d{2}-\d{2}-\d{7}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# 表單 POST 必須帶嘅 antiforgery token（喺 joblist 頁嘅 <form> 裡面）
TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')

# 搜尋表單骨架（實測自 gov.hk joblist 頁嘅 <form>）。__RequestVerificationToken
# 每次由 joblist 頁新鮮攞；Criteria.jobType=5 揀「電腦及資訊科技」類別；
# Criteria.searchField = 關鍵字（一般軌用）；兩者喺 /search/ 都實測有效。
SEARCH_FORM = {
    "Criteria.filterId": "",
    "Criteria.jobType": "",
    "Criteria.selectedJobTitle": "",
    "Criteria.selectedDistricts": "",
    "Criteria.displayMoreVac": "false",
    "Criteria.industry": "",
    "Criteria.salaryFr": "",
    "Criteria.salaryTo": "",
    "Criteria.searchField": "",
    "Criteria.searchByOption": "1",
    "Criteria.specEmpProgram": "",
    "SearchFor": "",
    "RefineSearch": "True",
    "IsMobile": "False",
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
    POST search. Titles/links/salary/location live in sibling <span>s, and the
    刊登日期 column means the posting date is known WITHOUT opening the detail
    page (so stale vacancies can be skipped before the extra request).
    Returns [{job_id, title, salary_range, location, posted_at, detail_url}].
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
            # 刊登日期欄（DD/MM/YYYY）—— 唔開詳情頁都知幾時登
            "posted_at": cell("job_icon1"),
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
                 cfg: TrackConfig | None = None,
                 channels: list[str] | tuple[str, ...] | None = None) -> list[JobDraft]:
    """Scrape the gov.hk channels for one job track.

    - IT track: 大灣區 quickview (ALL vacancies, IT + 一般, 1-week window)
      + 資訊及科技界 category (cap).
    - general track: main quickview (all categories) filtered by the general
      keywords, excluding IT-classified titles.

    ``channels`` narrows to specific gov.hk sub-channels — e.g. only
    ``["govhk_gbayes"]`` for 大灣區計劃. Empty/None = the track's default set.
    """
    cfg = cfg or TrackConfig.defaults(track)
    seen: set[str] = set()
    wanted = ({c.strip() for c in channels if c and c.strip()} if channels
              else ({"govhk_general"} if track == "general"
                    else {"govhk_gbayes", "govhk_it"}))
    drafts: list[JobDraft] = []
    if "govhk_general" in wanted:
        drafts += await _scrape_general(session, seen, cfg)
    if "govhk_gbayes" in wanted:
        drafts += await _scrape_gbayes(session, seen, cfg)
    if "govhk_it" in wanted:
        drafts += await _scrape_it(session, seen, cfg)
    return drafts


async def _scrape_gbayes(session: BrowserSession, seen: set[str],
                         cfg: TrackConfig | None = None) -> list[JobDraft]:
    """大灣區青年就業計劃: quickview pages — KEEP ALL vacancies (IT + 一般).

    No IT keyword filter: every GBA job is fetched and tagged by its title
    classification (it / general). Posting-date window is 1 week
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
            category = classify(it["title"], cfg.it_keywords, cfg.non_it_keywords)
            drafts.append(await _fetch_detail(session, it, GBY_PLATFORM, category))
            # 大灣區：刊登日期要喺一個星期（7日）之內；
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


def _cap_budget(cfg: TrackConfig) -> tuple[int, int]:
    """(非優先軟上限, 優先豁免額)。軟上限 0 = 唔設限。"""
    soft = max(0, int(cfg.govhk_max_jobs))
    extra = max(0, int(cfg.priority_extra_max)) if cfg.cap_bypass_enabled else 0
    return soft, extra


def _room_for(title: str, skills, normal: int, priority: int, cfg: TrackConfig) -> tuple[bool, bool]:
    """(可唔可以收做普通工, 可唔可以收做優先工)。豁免關掉就冇優先額。

    軟上限用盡之後，只有優先工（標題命中優先字詞／pre-score 夠高）仲有 room；
    兩者都冇 room = 呢份唔收（唔會開詳情頁，慳時間）。
    """
    soft, extra = _cap_budget(cfg)
    room_normal = soft <= 0 or normal < soft
    prio = bool(extra) and priority < extra and is_priority_job(title, "", skills, cfg)
    return room_normal, prio


class _Tally:
    """一個渠道嘅收工計數（軟上限／優先豁免／收手判斷）。

    ``take()`` 回 ``"normal"``／``"priority"`` = 收（caller 應該開詳情頁）；
    ``"skip"`` = 唔收（軟上限已滿又唔係優先工 —— 唔好白開詳情頁）；
    ``"stop"`` = 連續 PRIORITY_SCAN_SLACK 份都冇 room，收手唔好再揭頁。
    """

    __slots__ = ("normal", "priority", "misses")

    def __init__(self) -> None:
        self.normal = 0
        self.priority = 0
        self.misses = 0

    @property
    def total(self) -> int:
        return self.normal + self.priority

    def take(self, title: str, skills, cfg: TrackConfig) -> str:
        room_normal, room_priority = _room_for(title, skills, self.normal, self.priority, cfg)
        if not room_normal and not room_priority:
            self.misses += 1
            if _cap_budget(cfg)[0] > 0 and self.misses >= PRIORITY_SCAN_SLACK:
                return "stop"
            return "skip"
        self.misses = 0
        if room_normal:
            self.normal += 1
            return "normal"
        self.priority += 1      # 高分豁免：照收
        return "priority"


async def _fetch_token(session: BrowserSession) -> str:
    """由 gov.hk joblist 頁攞表單嘅 antiforgery token。

    冇 token 嘅話搜尋 POST 會 302 去 /404.html，條件入唔到 session，之後嘅
    joblist 只會回「全部空缺」—— 即係靜靜地變成冇 filter。
    """
    request = getattr(getattr(session, "context", None), "request", None)
    if request is None:
        return ""
    try:
        resp = await request.get(f"{JOBLIST_URL}?direct=False")
        page_html = await resp.text()
        await resp.dispose()
    except Exception as e:  # noqa: BLE001
        log.warning("govhk search: 攞唔到表單 token: %s", e)
        return ""
    m = TOKEN_RE.search(page_html or "")
    if not m:
        log.warning("govhk search: joblist 頁搵唔到 antiforgery token")
        return ""
    return m.group(1)


async def _post_search(session: BrowserSession, token: str, keyword: str = "",
                       job_type: str = "") -> bool:
    """把搜尋條件放入 gov.hk session；之後 GET joblist 就係搜尋結果。

    ``keyword`` = Criteria.searchField（一般軌逐個關鍵字用）；
    ``job_type`` = Criteria.jobType（``IT_JOB_TYPE`` = 電腦及資訊科技）。
    回 False = 條件入唔到 session（caller 唔應該當成「冇結果」）。
    """
    request = getattr(getattr(session, "context", None), "request", None)
    if request is None or not token:
        return False
    form = dict(SEARCH_FORM)
    form["Criteria.jobType"] = job_type
    form["Criteria.searchField"] = keyword
    form["__RequestVerificationToken"] = token
    try:
        resp = await request.post(SEARCH_POST_URL, form=form)
        url = getattr(resp, "url", "") or ""
        await resp.dispose()
    except Exception as e:  # noqa: BLE001
        log.warning("govhk search %r failed: %s", keyword or job_type, e)
        return False
    if "404.html" in url:
        log.warning("govhk search %r: 俾網站拒絕（302 -> 404）", keyword or job_type)
        return False
    return True


async def _scrape_it(session: BrowserSession, seen: set[str],
                     cfg: TrackConfig | None = None) -> list[JobDraft]:
    """資訊及科技界 joblist（POST 搜尋 + joblist 分頁）, capped per scan.

    搜尋用 ``Criteria.jobType=5``（電腦及資訊科技）—— 呢個類別已經 restrict 咗
    IT／tech，所以唔使再加標題 filter。**注意**：一定要用 `/search/` 端點 +
    joblist 頁嘅 antiforgery token，否則條件入唔到 session，joblist 會回全部空缺。
    用戶要求：評級好高嘅工（標題命中優先字詞／pre-score 夠高）無視渠道上限 ——
    軟上限只計非優先工，優先工另有豁免額（cfg.priority_extra_max）。
    軟上限用盡之後唔會再開詳情頁，只會繼續揭頁搵優先工（連續 PRIORITY_SCAN_SLACK
    個都唔中就先收手，唔會白揭 30 頁）。
    """
    from .cv_loader import load_skills

    cfg = cfg or TrackConfig.defaults("it")
    skills = load_skills()
    drafts: list[JobDraft] = []
    tally = _Tally()

    token = await _fetch_token(session)
    if not token or not await _post_search(session, token, job_type=IT_JOB_TYPE):
        log.warning("govhk IT: 搜尋條件入唔到 session — 跳過呢個渠道")
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
            # 列表係新->舊：刊登日期已經過期就唔使開詳情頁，即刻收手
            if _too_old(it.get("posted_at")):
                log.info("govhk IT: reached posting-date window (%s), stopping channel",
                         it["posted_at"])
                return drafts
            verdict = tally.take(it["title"], skills, cfg)
            if verdict == "stop":
                log.info("govhk IT: %s normal cap reached and no priority job in the "
                         "last %s items, stopping channel", cfg.govhk_max_jobs, tally.misses)
                return drafts
            if verdict == "skip":
                continue          # 唔開詳情頁：軟上限已滿又唔係優先工
            d = await _fetch_detail(session, it, IT_PLATFORM, "it")
            drafts.append(d)
            # 詳情頁有更準嘅日期時再 check 一次（列表冇日期 icon 嘅情況）
            if drafts and _too_old(drafts[-1].posted_at):
                log.info("govhk IT: reached posting-date window (%s), stopping channel",
                         drafts[-1].posted_at)
                return drafts
            if scan_control.stop_requested():
                log.info("govhk IT: stop requested mid-item — returning partial drafts")
                return drafts
        if page_no % 5 == 0:
            log.info("govhk IT page %s: %s new, %s drafts so far (%s normal + %s priority)",
                     page_no, len(matches), len(drafts), tally.normal, tally.priority)
        await human_delay(0.4, 1.0)

    return drafts


async def _scrape_general(session: BrowserSession, seen: set[str],
                          cfg: TrackConfig | None = None) -> list[JobDraft]:
    """一般 track: 用 gov.hk 自己嘅搜尋框逐個一般關鍵字搵（server-side filter）。

    gov.hk 嘅搜尋框（``Criteria.searchField``）係 server-side 過濾，只會回真正
    命中關鍵字嘅空缺 —— 比舊嘅「揭晒全部 quickview 再自己 filter」快好多。
    攞唔到搜尋表單 token、或者搜尋完全冇回任何列，就跌落 `_quickview_general`
    舊路徑（保證仲有工收）。軟上限／優先豁免同其他渠道一樣。
    """
    from .cv_loader import load_skills

    cfg = cfg or TrackConfig.defaults("general")
    skills = load_skills()
    tally = _Tally()

    token = await _fetch_token(session)
    if token and cfg.keywords:
        drafts, rows = await _keyword_sweeps(session, seen, cfg, skills, tally, token)
        # 只有「搜尋機制冇回任何列」至跌落 quickview；filter 完冇工係正常結果
        if drafts or rows or scan_control.stop_requested():
            return drafts
        log.info("govhk general: 關鍵字搜尋冇回任何列，改用 quickview")
    elif not token:
        log.info("govhk general: 攞唔到搜尋表單 token，改用 quickview")
    return await _quickview_general(session, seen, cfg, skills, tally)


async def _keyword_sweeps(session: BrowserSession, seen: set[str], cfg: TrackConfig,
                          skills, tally: _Tally, token: str) -> tuple[list[JobDraft], int]:
    """逐個一般關鍵字做一次 gov.hk 搜尋，再揭 joblist 分頁。

    回 ``(drafts, 見過嘅列表列數)`` —— 列數 0 = 搜尋機制冇回任何嘢，caller
    應該跌落 quickview 舊路徑。
    """
    drafts: list[JobDraft] = []
    rows = 0
    keywords = [k.strip() for k in cfg.keywords if k.strip()][:KEYWORD_SEARCH_MAX]

    for kw in keywords:
        if scan_control.stop_requested():
            break
        if not await _post_search(session, token, keyword=kw):
            log.warning("govhk general[%s]: 搜尋失敗，跳過呢個關鍵字", kw)
            continue
        for page_no in range(1, MAX_PAGES + 1):
            if scan_control.stop_requested():
                return drafts, rows
            url = f"{JOBLIST_URL}?direct=False&page={page_no}"
            try:
                resp = await session.context.request.get(url)
                page_html = await resp.text()
                await resp.dispose()
            except Exception as e:  # noqa: BLE001
                log.warning("govhk general[%s] list page %s failed: %s", kw, page_no, e)
                break
            items = parse_joblist_html(page_html)
            if not items:
                break  # past the last page
            rows += len(items)
            for it in items:
                if not it["job_id"] or it["job_id"] in seen:
                    continue
                seen.add(it["job_id"])
                # 列表係新->舊：見到第一份過期就收手（呢個關鍵字同渠道）
                if _too_old(it.get("posted_at")):
                    log.info("govhk general[%s]: reached posting-date window (%s), stopping",
                             kw, it["posted_at"])
                    return drafts, rows
                if not (title_matches(it["title"], cfg.keywords)
                        and classify(it["title"], cfg.it_keywords, cfg.non_it_keywords) == "general"):
                    continue
                verdict = tally.take(it["title"], skills, cfg)
                if verdict == "stop":
                    log.info("govhk general: %s normal cap reached and no priority job in "
                             "the last %s items, stopping channel",
                             cfg.govhk_max_jobs, tally.misses)
                    return drafts, rows
                if verdict == "skip":
                    continue          # 唔開詳情頁：軟上限已滿又唔係優先工
                d = await _fetch_detail(session, it, GENERAL_PLATFORM, "general")
                drafts.append(d)
                if drafts and _too_old(drafts[-1].posted_at):
                    log.info("govhk general: reached posting-date window (%s), stopping channel",
                             drafts[-1].posted_at)
                    return drafts, rows
                if scan_control.stop_requested():
                    log.info("govhk general: stop requested mid-item — returning partial drafts")
                    return drafts, rows
            if page_no % 5 == 0:
                log.info("govhk general[%s] page %s: %s rows, %s drafts so far "
                         "(%s normal + %s priority)",
                         kw, page_no, len(items), len(drafts), tally.normal, tally.priority)
            await human_delay(0.5, 1.2)

    return drafts, rows


async def _quickview_general(session: BrowserSession, seen: set[str], cfg: TrackConfig,
                             skills, tally: _Tally) -> list[JobDraft]:
    """舊路徑：main quickview（全部分類、新->舊）自己 filter 一般 keywords。

    Same list/detail format as the gbayes quickview (div.row.item[data-jobcard]).
    IT-classified titles are excluded; the first stale job stops the channel.
    """
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
            and classify(it["title"], cfg.it_keywords, cfg.non_it_keywords) == "general"  # drop IT titles
        ]
        for it in matches:
            seen.add(it["job_id"])
            verdict = tally.take(it["title"], skills, cfg)
            if verdict == "stop":
                log.info("govhk general: %s normal cap reached and no priority job in "
                         "the last %s items, stopping channel", cfg.govhk_max_jobs, tally.misses)
                return drafts
            if verdict == "skip":
                continue          # 唔開詳情頁：軟上限已滿又唔係優先工
            d = await _fetch_detail(session, it, GENERAL_PLATFORM, "general")
            drafts.append(d)
            if drafts and _too_old(drafts[-1].posted_at):
                log.info("govhk general: reached posting-date window (%s), stopping channel",
                         drafts[-1].posted_at)
                return drafts
            if scan_control.stop_requested():
                log.info("govhk general: stop requested mid-item — returning partial drafts")
                return drafts
        if page_no % 5 == 0:
            log.info("govhk general page %s: %s new matches, %s drafts so far (%s normal + %s priority)",
                     page_no, len(matches), len(drafts), tally.normal, tally.priority)
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
        source_query=platform,
        raw={"apply_note": d.get("apply_note", "")},
    )


def can_fetch_detail(platform: str) -> bool:
    """政府工有冇得補詳情（jd_text 以外，最重要係補返聯絡 email）。"""
    return (platform or "").startswith("govhk")


async def fetch_detail(session: BrowserSession, draft: JobDraft) -> JobDraft:
    """補／更新政府工嘅詳情：JD、聯絡 email／聯絡人、刊登日期、公司、地點。

    點解需要：`store._apply_method_for()` 見到 draft 冇 contact_email 就會標
    `apply_method="form"`，令投遞時跌入「唔支援嘅平台」。但其實政府工嘅申請方法
    （Email／電話／親身）係寫喺詳情頁嘅「申請須知」裡面，只要重新揭一次詳情頁就
    搵得返。呢個 function 就係俾「🔄 更新 JD」同投遞前嘅自動修復用。
    """
    item = {
        "job_id": draft.job_id,
        "title": draft.title,
        "detail_url": draft.url,
        "location": draft.location,
        "salary_range": draft.salary_range,
    }
    d = await _fetch_detail(session, item, draft.platform or IT_PLATFORM,
                            draft.category or "")
    if d.jd_text:
        draft.jd_text = d.jd_text
    if d.contact_email:
        draft.contact_email = d.contact_email
    if d.contact_person:
        draft.contact_person = d.contact_person
    if d.company:
        draft.company = d.company
    if d.location:
        draft.location = d.location
    if d.salary_range:
        draft.salary_range = d.salary_range
    if d.posted_at:
        draft.posted_at = d.posted_at
    if d.url:
        draft.url = d.url
    if d.contact_email:
        # 有 email 就一定係 email 申請（本來可能因為冇 email 被標成 form）
        draft.apply_method = "email"
    return draft


async def run_once() -> list[JobDraft]:
    async with BrowserSession("govhk") as session:
        return await scrape(session)
