"""OfferToday (HK direct-hire platform) scraper.

**用戶要求：OfferToday 嘅工要「唔開詳情都知日期」**（先可以喺開詳情之前篩走過期工）。
OfferToday 列表頁係純 JS 渲染，但佢自己會打一個 JSON API：

    POST {BASE}/wapi/geek/recommend/search/list

回傳 ``resultList`` 每項（``cardType == 0``）已經有 ``jobId``、``jobName``、
``companyName``、``salaryDesc``、``skills``、``experience``、``educationDesc``、
``jobFunctions``，而 **``jobPostTime`` 就係刊登／更新日期**
（``發布於10-05``／``更新於09-14``／``更新於3個月前``／``發布於近3個月``）。

兩個取用方式：

1. **攔截（主要路徑）**——正常 scroll 搜尋頁，用 ``page.on("response")`` 收佢自己
   發出嘅 ``search/list`` 回應，砌成 ``job_id -> card`` 對照表，喺建立 draft 時直接
   填 ``posted_at``／``company``／``salary_range``／``location``。
   因為係網站自己嘅請求，**唔會撞 rate limit**（實測 scroll 3 次收到 10 個回應）。
   過期工之後喺 scanner 嘅新鮮度檢查（``scanner.py`` 嘅 ``is_fresh``）就會被丟，
   唔使再開詳情頁 —— 呢個就係用戶要嘅效果。

2. **直接打 API（選用，``OFFERTODAY_API_ENABLED``，預設關）**——``_api_scrape()``
   用 ``page.evaluate(fetch)`` 直接問 API（可以帶 ``publishTime`` 原生日期 filter、
   ``jobFunctionCodes`` 分類代碼）。**API rate limit 好惡**：連續打會回 HTTP 429，
   退避重試都可能用盡（實測 6 個目標有 5 個失敗），所以預設關咗。
   注意 ``context.request`` 喺 CDP 連線下用唔到，一定要喺真 page 入面 fetch。

List URLs: /hk/search/jobs-<category>/<code>；/hk/search/<keyword>-jobs
  IT track category codes: 資訊科技=118000, 工程師=112000, 科技=127000
Detail: /hk/job/<jobId> —— job id = JSON 入面嘅 ``jobId``。
  Detail 頁仍然用嚟攞完整 JD（JSON-LD 亦帶 ``datePosted`` ISO 日期）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote, urljoin

from ..config import settings
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

# ------------------------------------------- 列表 JSON 回應攔截（主要路徑）
LIST_API_MARK = "/wapi/geek/recommend/search/list"


def _absorb_listing(body: dict, store: dict[str, dict]) -> int:
    """把一個 ``search/list`` 回應入面嘅職位卡片放入 ``store[job_id]``。

    ``cardType == 0`` 才係職位（``cardType == 1`` 係「訂閱新職位」banner）。
    回傳新加入嘅數量。
    """
    if not isinstance(body, dict):
        return 0
    data = body.get("data") or body.get("zpData") or {}
    added = 0
    for item in (data.get("resultList") or []):
        if not isinstance(item, dict) or item.get("cardType") != 0:
            continue
        token = item.get("jobId")
        if token and token not in store:
            store[token] = item
            added += 1
    return added


def _watch_list_api(target, store: dict[str, dict]):
    """喺 page／context 掛 response listener，收埋頁面自己打嘅列表 JSON。

    回傳 handler（畀 ``_unwatch_list_api`` 移除）；冇 event API（測試嘅假 object）
    就回 ``None``。**要喺開頁之前掛**，先至收得到第一頁嘅回應。
    """
    on = getattr(target, "on", None)
    if on is None:
        return None

    async def _handler(resp) -> None:
        try:
            if LIST_API_MARK not in resp.url:
                return
            body = await resp.json()
        except Exception:  # noqa: BLE001 — 唔係我哋要嘅回應／已經 dispose
            return
        _absorb_listing(body, store)

    try:
        on("response", _handler)
    except Exception:  # noqa: BLE001
        return None
    return _handler


def _unwatch_list_api(target, handler) -> None:
    """移除 ``_watch_list_api`` 掛嘅 listener（唔想跨掃描累積）。"""
    if handler is None or target is None:
        return
    remove = getattr(target, "remove_listener", None)
    if remove is None:
        return
    try:
        remove("response", handler)
    except Exception:  # noqa: BLE001
        pass


def _apply_listing(draft: JobDraft, meta: dict | None) -> bool:
    """用列表卡片資料填 draft（特別係 ``posted_at``）。回 True = 攞到日期。

    用戶要求：OfferToday 唔開詳情都知日期 —— 有咗 ``posted_at``，
    scanner 嘅新鮮度檢查（``is_fresh``）就可以喺開詳情頁之前丟走過期工。
    """
    if not meta:
        return False
    posted = parse_post_time(meta.get("jobPostTime"))
    if posted:
        draft.posted_at = posted
    if not draft.company:
        draft.company = str(meta.get("companyName") or meta.get("brandName") or "")
    if not draft.location:
        draft.location = str(meta.get("locationDesc") or meta.get("level3LocDesc") or "")
    # 卡片文字嘅「薪金」係用 regex 由成段文字捉出嚟，好易捉到經驗值
    # （「3-5年」→「3-5」、「10年」→「10」）。列表 JSON 嘅 ``salaryDesc``
    # 準得多 —— 只要手上嗰個唔似真薪金（冇「$」）就用列表值。
    listed_salary = str(meta.get("salaryDesc") or "")
    if listed_salary and "$" not in (draft.salary_range or ""):
        draft.salary_range = listed_salary
    draft.raw = {**(draft.raw or {}),
                 "post_time_text": str(meta.get("jobPostTime") or ""),
                 "listing": True}
    skills = meta.get("skills")
    if skills:
        draft.raw.setdefault("skills", list(skills))
    return bool(posted)


# ---------------------------------------------------------------- JSON API
API_LIST = f"{BASE}/wapi/geek/recommend/search/list"
API_PAGE_SIZE = 30
API_MAX_PAGES = 8
# 429 退避（秒）；第一個 0.0 = 即刻試第一次
API_RETRY_WAITS = (0.0, 3.0, 9.0, 20.0)
API_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "api-language": "zh_HK",
    "csrf-token": "",
    "x-requested-with": "XMLHttpRequest",
    "traceid": "F-cvsubmit1",
}
# 喺 page 入面打 API（同源 → 帶到 cookie；context.request 喺 CDP 下唔 work）
_API_FETCH_JS = """async ([url, payload, hdr]) => {
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: Object.assign({}, hdr, {'content-type': 'application/json;charset=UTF-8'}),
      body: JSON.stringify(payload)
    });
    return {status: r.status, text: await r.text()};
  } catch (e) {
    return {status: 0, text: String(e)};
  }
}"""

# 「發布於10-05」／「更新於3個月前」／「剛剛」／「昨天」／「3日前」
_POST_MMDD_RE = re.compile(r"(\d{1,2})-(\d{1,2})")
_POST_DAYS_RE = re.compile(r"(\d+)\s*日")
_POST_MONTHS_RE = re.compile(r"(\d+)\s*(?:個)?月")


def parse_post_time(text: str, today: date | None = None) -> str:
    """OfferToday 卡片嘅 ``jobPostTime`` → ISO 日期（``""`` = 解析唔到）。

    實測格式：「發布於09-10」「更新於10-05」（當年年份，若果係未來就當舊年）、
    「發布於今天」「剛剛」「昨天」「3日前」、「更新於3個月前」「更新於近3個月」。
    解析唔到就回 ``""``（召喚方當「日期未知」處理）。
    """
    if not text:
        return ""
    t = str(text)
    today = today or date.today()
    if "剛剛" in t or "今天" in t or "今日" in t:
        return today.isoformat()
    if "昨天" in t or "昨日" in t:
        return (today - timedelta(days=1)).isoformat()
    m = _POST_MONTHS_RE.search(t)
    if m:
        return (today - timedelta(days=30 * int(m.group(1)))).isoformat()
    m = _POST_DAYS_RE.search(t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = _POST_MMDD_RE.search(t)
    if m:
        mo, dy = int(m.group(1)), int(m.group(2))
        try:
            d = date(today.year, mo, dy)
        except ValueError:
            return ""
        if d > today + timedelta(days=1):        # 跨年：例如今日 12 月見到 01-05
            try:
                d = date(today.year - 1, mo, dy)
            except ValueError:
                return ""
        return d.isoformat()
    return ""


@dataclass(frozen=True)
class _ApiTarget:
    """一個 API 搜尋目標：分類頁（``codes``）或者關鍵字（``keyword``）。"""
    referer: str
    query: str
    keyword: str = ""
    codes: tuple[str, ...] = ()
    is_ai_group: bool = False

    @property
    def label(self) -> str:
        return self.keyword or ("/".join(self.codes) or "category")


def _api_payload(target: _ApiTarget, page_no: int) -> dict:
    return {
        "keyword": target.keyword,
        "page": page_no,
        "salaryType": 0,
        "publishTime": "",
        "employmentTypes": [],
        "experiences": [],
        "educationLevels": [],
        "benefits": [],
        "workPermits": [],
        "rcdType": 7,
        "pageSize": API_PAGE_SIZE,
        "jobFunctionCodes": list(target.codes),
        "industries": [],
        "subDistrictCodes": [],
    }


def _card_text(item: dict) -> str:
    """卡片可讀文字（等同舊 DOM 路徑嘅 ``card_text``，畀豁免／技能分用）。"""
    parts: list[str] = []
    for key in ("salaryDesc", "experience", "educationDesc", "locationDesc",
                "jobTypeDesc", "industryDesc", "workPermitDesc"):
        val = item.get(key)
        if val:
            parts.append(str(val))
    for skill in (item.get("skills") or []):
        if skill:
            parts.append(str(skill))
    return " | ".join(parts)


async def _api_list(page, target: "_ApiTarget", page_no: int) -> dict | None:
    """攞一頁列表；429 會退避重試。回 ``None`` = 放棄呢一頁。"""
    for wait in API_RETRY_WAITS:
        if wait:
            await asyncio.sleep(wait)
        if scan_control.stop_requested():
            return None
        try:
            out = await page.evaluate(_API_FETCH_JS,
                                      [API_LIST, _api_payload(target, page_no), API_HEADERS])
        except Exception as e:  # noqa: BLE001 — page 死咗／被閂
            log.warning("offertoday API %s page %s failed: %s", target.label, page_no, e)
            return None
        status = (out or {}).get("status")
        if status == 200:
            try:
                body = json.loads(out.get("text") or "")
            except ValueError:
                log.warning("offertoday API %s page %s: 回非 JSON", target.label, page_no)
                return None
            return body.get("data") or body.get("zpData") or {}
        if status != 429:
            log.warning("offertoday API %s page %s: HTTP %s", target.label, page_no, status)
            return None
        log.info("offertoday API %s page %s: 429 rate limited — 等 %ss 再試",
                 target.label, page_no, wait or 3.0)
    log.warning("offertoday API %s page %s: 429 重試用完", target.label, page_no)
    return None


async def _api_scrape(session, track: str, cfg: TrackConfig,
                      skills: list) -> list[JobDraft]:
    """經 JSON API 掃 OfferToday（唔開卡片、唔 scroll；日期由列表直接攞）。"""
    targets = _api_targets(cfg)
    if not targets:
        return []
    drafts: list[JobDraft] = []
    seen: set[str] = set()
    page = None
    try:
        page = await open_page(session.context, targets[0].referer)
    except Exception as e:  # noqa: BLE001
        log.warning("offertoday API: 開唔到 page（%s）", e)
        return []
    try:
        for target in targets:
            if scan_control.stop_requested():
                break
            cap = cfg.offertoday_max_per_search
            extra = cfg.priority_extra_max if cfg.cap_bypass_enabled else 0
            taken_normal = taken_priority = misses = 0
            for page_no in range(1, API_MAX_PAGES + 1):
                if cap and taken_normal >= cap and taken_priority >= extra:
                    break
                if scan_control.stop_requested():
                    break
                data = await _api_list(page, target, page_no)
                if data is None:
                    break
                items = [x for x in (data.get("resultList") or [])
                         if x.get("cardType") == 0]
                if not items:
                    break
                full = False
                for item in items:
                    if cap and taken_normal >= cap and taken_priority >= extra:
                        full = True
                        break
                    token = item.get("jobId") or ""
                    title = (item.get("jobName") or "").strip()
                    if not token or not title or token in seen:
                        continue
                    if track == "it":
                        if not title_matches(title, cfg.keywords):
                            continue
                        category = "it"
                    else:
                        if classify(title, cfg.it_keywords, cfg.non_it_keywords) != "general" \
                           or not title_matches(title, cfg.keywords):
                            continue
                        category = "general"
                    card_text = _card_text(item)
                    priority = bool(is_priority_job(title, card_text, skills, cfg))
                    if cap and taken_normal >= cap:
                        # 軟上限已滿：只有「優先工」先值得再收。
                        if not priority:
                            misses += 1
                            if misses >= PRIORITY_SCAN_SLACK:
                                log.info("offertoday API %s: %s normal cap reached and no "
                                         "priority job in the last %s cards, moving on",
                                         target.label, cap, misses)
                                full = True
                                break
                            continue
                    if priority and taken_priority < extra:
                        taken_priority += 1
                    elif (not cap) or taken_normal < cap:
                        taken_normal += 1
                    else:
                        continue
                    misses = 0
                    seen.add(token)
                    drafts.append(JobDraft(
                        platform="offertoday",
                        job_id=token,
                        title=title,
                        url=f"{BASE}/hk/job/{token}",
                        company=str(item.get("companyName") or item.get("brandName") or ""),
                        location=str(item.get("locationDesc")
                                     or item.get("level3LocDesc") or ""),
                        salary_range=str(item.get("salaryDesc") or ""),
                        jd_text="",
                        posted_at=parse_post_time(item.get("jobPostTime")),
                        category=category,
                        source_query=target.query,
                        raw={"card_text": card_text[:500],
                             "priority": priority,
                             "ai_search": target.is_ai_group,
                             "skills": list(item.get("skills") or []),
                             "post_time_text": str(item.get("jobPostTime") or "")},
                    ))
                log.info("offertoday API %s%s page %s: total %s, took %s normal + %s priority "
                         "(cap %s + %s/search)",
                         target.label, "（AI 組）" if target.is_ai_group else "", page_no,
                         data.get("total"), taken_normal, taken_priority, cap, extra)
                if full or not data.get("hasMore"):
                    break
                await human_delay(1.5, 3.0)
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
    return drafts


def _api_targets(cfg: TrackConfig) -> list[_ApiTarget]:
    """由 ``_search_targets()`` 衍生 API 目標（同一套字詞／分類，唔會走漏）。"""
    targets: list[_ApiTarget] = []
    for url, query, is_ai in _search_targets(cfg):
        if query == "category":
            code = url.rstrip("/").rsplit("/", 1)[-1]
            targets.append(_ApiTarget(referer=url, query=query, codes=(code,),
                                      is_ai_group=is_ai))
        else:
            targets.append(_ApiTarget(referer=url, query=query, keyword=query,
                                      is_ai_group=is_ai))
    return targets


def _search_targets(cfg: TrackConfig) -> list[tuple[str, str, bool]]:
    """[(url, 搜尋字詞／分類頁名, 係唔係 AI 搜尋組)].

    IT track：
      1. 3 個技術分類頁（資訊科技／工程師／科技）
      2. **AI 搜尋組**（cfg.ai_search_terms，例如 agent／AI）—— 獨立預算，唔會
         同其他字詞爭，而且呢組嘅工有 7 日刊登日期限制（見 scanner）
      3. 其他 IT 關鍵字搜尋（cfg.offertoday_search_terms，會剔走 AI 組已用嘅字詞）
    general track：只有關鍵字搜尋。
    """
    from .tuning import is_ai_search_query, matches_ai_term

    if cfg.name != "it":
        terms = (cfg.offertoday_search_terms or cfg.keywords)[
            : cfg.max_searches or len(cfg.keywords)]
        return [(f"{BASE}/hk/search/{quote(t)}-jobs", t, False) for t in terms]

    targets: list[tuple[str, str, bool]] = [(u, "category", False) for u in SEARCH_URLS]
    ai_terms = (cfg.ai_search_terms or [])[
        : cfg.ai_search_max_searches or len(cfg.ai_search_terms or [])]
    for term in ai_terms:
        targets.append((f"{BASE}/hk/search/{quote(term)}-jobs", term, True))
    # 其他字詞：剔走已經做過 AI 組嘅字詞（唔想同一個字詞開兩次）。
    # 用**渠道自己**嘅 AI 字詞清單比對（全域設定只做後備）。
    other = [t for t in (cfg.offertoday_search_terms or [])
             if not (matches_ai_term(t, ai_terms) or is_ai_search_query(t))]
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

    # 直接打 JSON API（選用；預設關）。API rate limit 好惡 —— 連續打會 429
    # （實測 6 個目標有 5 個退避重試都用盡），所以主要路徑係下面 DOM + 攔截。
    if settings.OFFERTODAY_API_ENABLED:
        try:
            drafts = await _api_scrape(session, track, cfg, skills)
        except Exception as e:  # noqa: BLE001
            log.warning("offertoday API scrape failed (%s) — 改用 DOM 路徑", e)
            drafts = []
        if drafts:
            log.info("offertoday API: 攞到 %s 份工（track=%s）", len(drafts), track)
            return drafts
        log.info("offertoday API: 冇收穫 — 改用 DOM 路徑")

    drafts: list[JobDraft] = []
    seen: set[str] = set()
    # 用戶要求：唔開詳情都知日期。搜尋頁自己會打
    # ``/wapi/geek/recommend/search/list``，我哋喺 context 度攔截佢嘅回應，砌成
    # ``job_id -> 卡片`` 對照表。listener 一定要喺**開頁之前**掛，先收得到第一頁。
    listing: dict[str, dict] = {}
    context = getattr(session, "context", None)
    watcher = _watch_list_api(context, listing)

    async def _dom_scrape() -> list[JobDraft]:
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
                dated = 0
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
                    d = JobDraft(
                        platform="offertoday",
                        job_id=token,
                        title=title,
                        url=urljoin(BASE, href),
                        company="",  # filled by list JSON / detail fetch
                        location="",
                        salary_range=salary,
                        jd_text="",
                        category=category,
                        source_query=query,
                        raw={"card_text": card_text[:500], "priority": priority,
                             "ai_search": is_ai_group},
                    )
                    # 列表 JSON 已經有日期／公司／薪金：唔使開詳情都知，
                    # 過期工之後喺 scanner 就會被丟（唔會開詳情頁）。
                    if _apply_listing(d, listing.get(token)):
                        dated += 1
                    drafts.append(d)
                log.info("offertoday %s%s: took %s normal + %s priority (cap %s + %s/search)%s",
                         url.rsplit("/", 1)[-1], "（AI 組）" if is_ai_group else "",
                         taken_normal, taken_priority, cap, extra,
                         f"，其中 {dated} 份由列表攞到日期" if dated else "")
                await page.close()
            except Exception as e:  # noqa: BLE001
                log.warning("offertoday search %s failed: %s", url, e)
            await human_delay(1.0, 2.5)

        return drafts

    try:
        return await _dom_scrape()
    finally:
        _unwatch_list_api(context, watcher)


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
