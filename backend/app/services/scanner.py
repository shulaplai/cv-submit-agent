"""Scanner pipeline: scrape enabled tracks (IT / 一般) -> persist -> enrich.

Each enabled track runs its own platform pass with per-track caps and keyword
filters; drafts are persisted with their ``category`` (it | general) so the
board can show one page per track. LLM budget (MAX_ENRICH_PER_SCAN) is shared
across tracks: new jobs are prioritized by keyword pre-score, leftover budget
goes to backfilling the oldest un-enriched rows.

高分豁免上限（用戶要求）：標題命中優先字詞（預設 = AI 職位關鍵字）或者關鍵字
pre-score 夠高嘅工，唔計入渠道／track 軟上限，照樣入庫（每渠道另有豁免硬上限
``priority_extra_max``，防止失控）。
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CoverLetter, JobApplication, Profile
from . import scraper_govhk, scraper_jobsdb, scraper_offertoday
from .classify import (TrackConfig, blocked_reason, is_priority_job,
                       known_location_match, parse_keywords,
                       resolve_blocked_keywords, resolve_general_keywords,
                       resolve_it_keywords, resolve_non_it_keywords,
                       resolve_wanted_locations, wanted_location_match)
from .cl_generator import generate_cl_checked
from .cv_loader import CVError, get_cv_text, load_skills
from .jobflags import compute_flags
from .language import detect_language
from .llm import LLMError
from .matcher import keyword_score, score_job
from .scraper_base import get_browser
from .store import persist_drafts
from .tuning import ai_search_terms, load_tuning, priority_keywords
from .jobdate import is_fresh, parse_posted_date
from . import scan_control

log = logging.getLogger(__name__)


def _platform_scrapers() -> tuple:
    """Enabled scrapers, in scan order. JobsDB is gated by JOBSDB_ENABLED."""
    scrapers: list[tuple] = []
    if settings.GOVHK_ENABLED:
        # 政府工都有詳情頁（jobCard）—— 可以補 JD，亦可以補返聯絡 email
        scrapers.append(("govhk", scraper_govhk.scrape, scraper_govhk.fetch_detail))
    if settings.JOBSDB_ENABLED:
        scrapers.append(("jobsdb", scraper_jobsdb.scrape, scraper_jobsdb.fetch_detail))
    scrapers.append(("offertoday", scraper_offertoday.scrape, scraper_offertoday.fetch_detail))
    return tuple(scrapers)


PLATFORM_SCRAPERS = _platform_scrapers()

# 渠道選擇：scan 除咗揀 IT／一般 track，亦可以逐個渠道揀（OfferToday、政府、
# 大灣區計劃…）。API / UI 共用同一套 key；空集合 = 全部渠道。
CHANNELS = ("offertoday", "govhk_gbayes", "govhk_it", "govhk_general", "jobsdb")
# 每個 track 之下，gov.hk 實際會跑嘅子渠道
TRACK_GOVHK_CHANNELS = {
    "it": ("govhk_gbayes", "govhk_it"),
    "general": ("govhk_general",),
}

_WS_RE = re.compile(r"\s+")

# 掃描期間嘅 AI 搜尋組 context（單一 scan 同時只會行一個，所以用 module-level 就夠）。
# 用 module state 而唔係 function 參數，係為咗唔改 `_fill_detail()` 嘅簽名 ——
# 好多測試會 monkeypatch 佢，改簽名會令佢哋全部爆。
_AI_RUN_CTX: dict = {}


def _ai_run_ctx() -> dict:
    return _AI_RUN_CTX


class _PaceGate:
    """Guarantees a minimum interval between page opens (site politeness).

    Concurrent callers (the scan opens up to SEMAPHORE detail pages at once)
    queue on an asyncio lock; each caller is released at least ``min_interval``
    seconds after the previous one, so the job site never sees a burst of
    requests. When ``max_interval`` > ``min_interval``, each caller's spacing is
    drawn randomly from [min, max] (human-like, avoids a fixed scrape rhythm).
    Interval 0 = no pacing (tests / manual actions).
    """

    def __init__(self, min_interval: float, max_interval: float | None = None):
        self.min_interval = max(0.0, min_interval)
        self.max_interval = (max(0.0, max_interval)
                             if max_interval is not None else self.min_interval)
        if self.max_interval < self.min_interval:
            self.max_interval = self.min_interval
        self._lock = asyncio.Lock()
        self._next_open = 0.0

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = self._next_open - now
            if wait > 0:
                await asyncio.sleep(wait)
            # re-read AFTER the sleep so consecutive callers stay spaced apart
            now = asyncio.get_running_loop().time()
            if self.max_interval > self.min_interval:
                self._next_open = now + random.uniform(self.min_interval, self.max_interval)
            else:
                self._next_open = now + self.min_interval


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
    non_it_kws = resolve_non_it_keywords(profile.non_it_keywords if profile else "")
    general_kws = resolve_general_keywords(profile.general_job_keywords if profile else "")
    # 掃描量／高分豁免（Settings 頁 -> .env）
    t = load_tuning(db)
    pr_kws = priority_keywords(t)
    ai_group = dict(
        blocked_keywords=resolve_blocked_keywords(
            getattr(profile, "it_blocked_keywords", "") if profile else ""),
        ai_search_terms=ai_search_terms(t),
        ai_search_max_searches=t.ai_search_max_searches,
        ai_search_max_age_days=t.ai_search_max_age_days,
        ai_stale_action=t.ai_stale_action,
    )
    bypass = dict(
        cap_bypass_enabled=t.cap_bypass_enabled,
        cap_bypass_min_score=t.cap_bypass_min_score,
        priority_keywords=pr_kws,
        priority_extra_max=t.priority_extra_max,
    )

    cfg_it = TrackConfig(
        name="it", label="IT",
        keywords=it_kws, it_keywords=it_kws, non_it_keywords=non_it_kws,
        govhk_max_jobs=(profile.govhk_it_max_jobs if profile else 0) or settings.GOVHK_IT_MAX_JOBS,
        offertoday_max_per_search=(profile.offertoday_it_max_per_search if profile else 0)
        or settings.OFFERTODAY_MAX_PER_SEARCH,
        # 額外 IT 關鍵字搜尋（AI agent 等）— 喺 3 個分類頁之上再開。
        # 用戶可以喺 UI 自定（profile），留空先用 .env。
        offertoday_search_terms=(
            parse_keywords(profile.offertoday_it_search_terms if profile else "")
            or parse_keywords(settings.OFFERTODAY_IT_SEARCH_TERMS)
        ),
        max_searches=t.offertoday_it_max_searches,
        **bypass,
        **ai_group,
    )
    cfg_general = TrackConfig(
        name="general", label="一般",
        keywords=general_kws, it_keywords=it_kws, non_it_keywords=non_it_kws,
        # 用戶要求：一般工只收「想去嘅地點」（UI 可改；空 = 唔篩）
        wanted_locations=resolve_wanted_locations(
            profile.general_wanted_locations if profile else ""),
        govhk_max_jobs=(profile.govhk_general_max_jobs if profile else 0)
        or settings.GOVHK_GENERAL_MAX_JOBS,
        offertoday_max_per_search=(profile.offertoday_general_max_per_search if profile else 0)
        or settings.OFFERTODAY_GENERAL_MAX_PER_SEARCH,
        offertoday_search_terms=(
            parse_keywords(profile.offertoday_general_search_terms if profile else "")
            or parse_keywords(settings.OFFERTODAY_GENERAL_SEARCH_TERMS)
            or general_kws
        ),
        max_searches=t.offertoday_general_max_searches,
        **bypass,
        **ai_group,
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
    skipped_location: int = 0   # 一般工：寫明另一個地區（唔喺想去名單）-> 篩走
    skipped_blocked: int = 0    # IT 軌：保險／地產／sales agent 類 -> 篩走
    location_uncertain: int = 0  # 一般工：冇寫地點 -> 保留但標示
    capped: int = 0
    enriched: int = 0
    backfilled: int = 0
    low_match: int = 0
    details_fetched: int = 0   # JDs fetched (new rows + detail-only backfill)
    # 高分豁免：因為豁免而收多咗幾多份 / 豁免額滿而仍被擋走幾多份
    priority_kept: int = 0
    priority_capped: int = 0
    # 批量 AI 檢查（掃描後自動跑嘅話）
    ai_checked: int = 0
    ai_non_it: int = 0
    ai_llm_calls: int = 0
    stopped: bool = False
    errors: list[str] = field(default_factory=list)
    # per-track breakdown for the UI: {track: {scanned, new_jobs, skipped_old, capped}}
    tracks: dict = field(default_factory=dict)

    def as_state(self) -> dict:
        """掃描摘要 -> JSON-friendly dict（routers/scan.py 記 last scan 用）。"""
        return {
            "scanned": self.scanned,
            "new_jobs": self.new_jobs,
            "skipped_duplicates": self.skipped_duplicates,
            "skipped_old": self.skipped_old,
            "skipped_location": self.skipped_location,
            "location_uncertain": self.location_uncertain,
            "capped": self.capped,
            "enriched": self.enriched,
            "backfilled": self.backfilled,
            "low_match": self.low_match,
            "details_fetched": self.details_fetched,
            "priority_kept": self.priority_kept,
            "priority_capped": self.priority_capped,
            "skipped_blocked": self.skipped_blocked,
            "ai_checked": self.ai_checked,
            "ai_non_it": self.ai_non_it,
            "ai_llm_calls": self.ai_llm_calls,
            "stopped": self.stopped,
            "errors": list(self.errors),
            "tracks": self.tracks,
        }


def _ai_group_max_age(query: str, cfg: TrackConfig, default: int) -> tuple[int, bool]:
    """(刊登日期上限, 係唔係 AI 搜尋組)。

    AI 搜尋組（agent／AI 字詞搵到嘅工）預設收緊到 7 日（用戶要求）；
    未開／唔係 AI 組就回傳原本上限（大灣區 7 日、其他 14 日）。
    """
    from .tuning import is_ai_search_query

    if not query or not is_ai_search_query(query):
        return default, False
    if cfg.ai_search_max_age_days and cfg.ai_search_max_age_days > 0:
        return cfg.ai_search_max_age_days, True
    return default, False


def _draft_extra_text(d) -> str:
    """Draft 用嚟做豁免判斷嘅文字（JD／卡片文字，冇就用標題）。"""
    return (getattr(d, "jd_text", "")
            or (getattr(d, "raw", None) or {}).get("card_text", "")
            or getattr(d, "title", ""))


def _draft_is_priority(d, skills, cfg: TrackConfig) -> bool:
    return is_priority_job(getattr(d, "title", ""), _draft_extra_text(d), skills, cfg)


def _channel_soft_budget(platform: str, cfg: TrackConfig) -> int:
    """嗰個渠道今次掃描「本來」可以收幾多份（唔計豁免）。0 = 冇上限。

    OfferToday 係「每個搜尋頁 N 份」，所以乘開幾多個搜尋頁（3 個分類頁 +
    cfg.max_searches 個關鍵字搜尋）。用嚟計「豁免收多咗幾多份」。
    """
    if platform == "govhk_gbayes":
        return 0                          # 大灣區一向冇上限
    if platform.startswith("govhk"):
        return max(0, int(cfg.govhk_max_jobs))
    if platform == "offertoday":
        per_search = max(0, int(cfg.offertoday_max_per_search))
        if per_search <= 0:
            return 0                      # 每頁不限 = 冇上限
        searches = (3 + max(0, int(cfg.max_searches))) if cfg.name == "it" \
            else max(1, int(cfg.max_searches))
        return per_search * searches
    return 0


def _fair_share(drafts: list, limit: int) -> list:
    """Round-robin across platforms so one platform can't crowd out others."""
    buckets: dict[str, list] = {}
    for d in drafts:
        buckets.setdefault(d.platform, []).append(d)
    picked: list = []
    while len(picked) < limit and buckets:
        for pf in list(buckets):
            if buckets[pf]:
                picked.append(buckets[pf].pop(0))
            if not buckets[pf]:
                del buckets[pf]
            if len(picked) >= limit:
                break
    return picked


async def run_scan(db: Session, progress: dict | None = None,
                   track: str | None = None,
                   channels: list[str] | tuple[str, ...] | None = None) -> ScanSummary:
    """Full scan. `progress` is a shared dict mutated in place for live UI updates.

    ``channels`` narrows the scan to specific sources (OfferToday / 政府 IT /
    政府一般 / 大灣區計劃 / JobsDB). Empty or None = every channel (default).

    Polls ``scan_control.stop_requested()`` between tracks/platforms (and
    between pages inside the scrapers). When a stop is requested the loop
    breaks immediately, but drafts already scraped ARE still persisted — the
    user's 暫停 button must not lose the work done so far.
    """
    summary = ScanSummary()
    all_drafts = []
    selected = {c.strip() for c in (channels or []) if c and c.strip()}

    def set_progress(platform: str, phase: str, count: int):
        if progress is not None:
            progress.update({"platform": platform, "phase": phase, "count": count})

    tuning = load_tuning(db)
    skills = load_skills()
    track_cfgs = list(load_track_configs(db, track))
    # AI 搜尋組一遇到「過期」就停：呢個 set 記住邊個 platform 已經停（channel 模式）
    stale_stopped: set[str] = set()

    def _cfg_of(category: str) -> TrackConfig | None:
        for c in track_cfgs:
            if c.name == category:
                return c
        return track_cfgs[0] if track_cfgs else None

    def _on_ai_stale(platform: str) -> None:
        """AI 搜尋組撞到過期工：跟設定停該渠道，或者暫停成個掃描。"""
        action = next((c.ai_stale_action for c in track_cfgs if c.ai_stale_action), "channel")
        if action == "scan":
            log.info("AI 搜尋組撞到過期工（%s）-> 暫停成個掃描", platform)
            scan_control.request_stop()
        else:
            log.info("AI 搜尋組撞到過期工（%s）-> 停呢個渠道，繼續其他", platform)
            stale_stopped.add(platform)

    _AI_RUN_CTX.clear()
    _AI_RUN_CTX.update({"cfg_of": _cfg_of, "stale_action": _on_ai_stale})
    # 一般 track 嘅「想去嘅地點」白名單（空 = 唔篩）；IT track 唔關事。
    wanted_locations: list[str] = []
    for _c in track_cfgs:
        if _c.name == "general":
            wanted_locations = list(_c.wanted_locations or [])

    def _location_verdict(platform: str, category: str, texts,
                          stage: str = "detail") -> str:
        """一般工嘅地點判斷，回 "ok" / "drop" / "uncertain"。

        - IT 工、大灣區計劃、冇設名單 -> "ok"
        - 命中「想去嘅地點」 -> "ok"
        - 寫咗**另一個**香港地區（例如屯門） -> "drop"（肯定唔想去）
        - 完全冇寫地點／只寫「港九新界」等泛指 -> "uncertain"
          （用戶要求：保護供應，唔確定就保留但標示，唔好靜靜篩走）
        ``stage="list"``：列表冇工地點時（OfferToday 常見）唔可以即刻落判斷，
        要留到開完詳情用 JD 內文再算。
        """
        if not wanted_locations or category != "general":
            return "ok"
        if platform == "govhk_gbayes":
            return "ok"          # 大灣區計劃（深圳／廣州…）唔跟香港地區名單
        if wanted_location_match(texts, wanted_locations):
            return "ok"
        if stage == "list" and not (texts[0] or "").strip():
            return "ok"          # 未知 -> 等詳情階段再算
        if known_location_match(texts):
            return "drop"        # 寫明另一個地區：肯定唔喺想去名單
        return "uncertain"       # 冇寫地點 -> 保留但標示
    for tcfg in track_cfgs:
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
            # 渠道過濾：gov.hk 底下嘅子渠道要再按 track 收窄
            govhk_sub: list[str] | None = None
            if platform == "govhk":
                allowed = set(TRACK_GOVHK_CHANNELS.get(tcfg.name, ()))
                wanted = allowed if not selected else (selected & allowed)
                if not wanted:
                    continue      # 呢個 track 下面冇揀到任何 gov.hk 渠道
                govhk_sub = sorted(wanted)
            elif selected and platform not in selected:
                continue          # 冇揀到呢個平台
            if scan_control.stop_requested():
                log.info("scan stop requested — breaking before platform %s", platform)
                summary.stopped = True
                break
            try:
                set_progress(platform, f"scraping ({tcfg.label})", 0)
                session = await get_browser(platform)
                if platform == "govhk":
                    drafts = await scrape_fn(session, track=tcfg.name, cfg=tcfg,
                                             channels=govhk_sub)
                else:
                    drafts = await scrape_fn(session, track=tcfg.name, cfg=tcfg)
                t_drafts.extend(drafts)
                summary.scanned += len(drafts)
                set_progress(platform, f"scraped ({tcfg.label})", len(drafts))
                # 高分豁免收多咗幾多份：實收份數 - 嗰個渠道原本嘅軟上限預算
                soft = _channel_soft_budget(platform, tcfg)
                if tcfg.cap_bypass_enabled and soft > 0 and len(drafts) > soft:
                    extra = len(drafts) - soft
                    summary.priority_kept += extra
                    log.info("%s/%s: 高分豁免收多咗 %s 份（軟上限 %s，實收 %s）",
                             tcfg.name, platform, extra, soft, len(drafts))
            except Exception as e:  # noqa: BLE001
                log.exception("scan failed for %s/%s", tcfg.name, platform)
                summary.errors.append(f"{tcfg.name}/{platform}: {e}")
            if scan_control.stop_requested():
                log.info("scan stop requested — stopping after platform %s", platform)
                summary.stopped = True
                break

        # freshness filter: drop jobs posted more than the window ago.
        # 大灣區 = GBAY_MAX_JOB_AGE_DAYS（7日）；其他渠道 =
        # MAX_JOB_AGE_DAYS（14日 — 淨係收刊登日期喺附近嘅新工）。
        kept: list = []
        t_skipped_loc = 0
        t_skipped_blocked = 0
        blocked_kws = list(tcfg.blocked_keywords or [])
        for d in t_drafts:
            # 用戶要求：保險／地產／sales agent 類職位「都唔要」—— 絕對否決
            # （優先於 AI／agent 一定要收嘅規則；呢類唔係你想做嘅工）
            if blocked_kws and tcfg.name == "it":
                hit = blocked_reason(getattr(d, "title", ""), blocked_kws)
                if hit:
                    t_skipped_blocked += 1
                    summary.skipped_blocked += 1
                    log.info("dropping blocked job %s/%s（命中「%s」）",
                             d.platform, d.job_id, hit)
                    continue
            max_age = (settings.GBAY_MAX_JOB_AGE_DAYS if d.platform == "govhk_gbayes"
                       else settings.MAX_JOB_AGE_DAYS)
            if max_age > 0 and not is_fresh(d.posted_at, max_age):
                t_skipped_old += 1
                summary.skipped_old += 1
                log.info("dropping stale job %s/%s (posted %r, >%sd old)",
                         d.platform, d.job_id, d.posted_at, max_age)
                continue
            # 一般 track：只收「想去嘅地點」（gov.hk 列表已經有工地點；
            # OfferToday 好多時要開完詳情先知，嗰層喺 phase A 再篩）。
            _verdict = _location_verdict(d.platform, tcfg.name, (d.location, d.title),
                                         stage="list")
            if _verdict == "drop":
                t_skipped_loc += 1
                summary.skipped_location += 1
                log.info("dropping off-list location job %s/%s (工地點 %r 唔喺想去名單)",
                         d.platform, d.job_id, d.location)
                continue
            if _verdict == "uncertain":
                d.raw = {**(d.raw or {}), "location_uncertain": True}
            kept.append(d)
        t_drafts = kept

        # per-track global cap (MAX_SCAN_JOBS) —— 用戶要求：高分／優先工唔受呢個
        # 總上限限制，照樣保留（額外最多 priority_extra_max 份防止失控）；軟上限
        # 只計其餘嘅工，fair-share round-robin 分配，免得一個平台擠走其他平台。
        max_jobs = tuning.max_scan_jobs if tuning.max_scan_jobs > 0 else 0
        if max_jobs > 0 and len(t_drafts) > max_jobs:
            extra = max(0, tcfg.priority_extra_max) if tcfg.cap_bypass_enabled else 0
            priority_drafts = ([d for d in t_drafts if _draft_is_priority(d, skills, tcfg)]
                               if extra else [])
            keep_priority = priority_drafts[:extra]
            prio_ids = {id(d) for d in priority_drafts}
            normal_drafts = [d for d in t_drafts if id(d) not in prio_ids]
            capped_normal = _fair_share(normal_drafts, max_jobs)
            dropped_priority = len(priority_drafts) - len(keep_priority)
            t_capped = (len(normal_drafts) - len(capped_normal)) + dropped_priority
            summary.capped += t_capped
            summary.priority_kept += len(keep_priority)
            summary.priority_capped += dropped_priority
            log.info("per-scan cap %s (%s track): kept %s normal + %s priority of %s drafts",
                     max_jobs, tcfg.name, len(capped_normal), len(keep_priority),
                     len(t_drafts))
            t_drafts = keep_priority + capped_normal

        # ensure every draft carries its track category
        for d in t_drafts:
            if not d.category:
                d.category = tcfg.name
            if (d.raw or {}).get("location_uncertain"):
                summary.location_uncertain += 1
        all_drafts.extend(t_drafts)
        summary.tracks[tcfg.name] = {
            "scanned": len(t_drafts),
            "new_jobs": 0,          # filled after persist
            "skipped_old": t_skipped_old,
            "skipped_location": t_skipped_loc,
            "skipped_blocked": t_skipped_blocked,
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
        budget = tuning.max_enrich_per_scan
        it_ceiling = tuning.max_enrich_it_per_scan
        candidates: list[JobApplication] = []
        # 用戶要求：「唔係份份工都要評分，只有 IT 工需要」——
        # ENRICH_GENERAL_JOBS 預設關：一般工唔會入 LLM（唔評分、唔生成 CL、唔寫摘要），
        # 只做「平價整理」（JD + 語言 + AI／合約／外派標籤 + 關鍵字分數）。
        cheap_rows: list[JobApplication] = []
        if new_rows:
            # new rows: prioritize by keyword pre-score (cheap, no LLM)
            scored = []
            for row in new_rows:
                pre = keyword_score(row.title, row.jd_text, skills)
                row.match_score = pre  # provisional; LLM re-scores if enriched
                scored.append((pre, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            ordered = [r for _, r in scored]
            it_rows = [r for r in ordered if r.category == "it"]
            gen_rows = [r for r in ordered if r.category != "it"]
            if tuning.enrich_all_it:
                # 用戶要求：所有新 IT 工都要 LLM 完整評分（唔限）。
                # MAX_ENRICH_IT_PER_SCAN 可以設一個安全上限（0 = 唔限）。
                it_rows = it_rows[:it_ceiling] if it_ceiling > 0 else it_rows
            else:
                it_rows = it_rows[: max(0, budget)]
            if tuning.enrich_general_jobs:
                gen_rows = gen_rows[: max(0, budget)]
            else:
                cheap_rows = gen_rows          # 唔入 LLM，下面做平價整理
                gen_rows = []
            candidates = it_rows + gen_rows
            cheap_ids = {r.id for r in cheap_rows}
            summary.low_match = sum(
                1 for pre, r in scored
                if pre < settings.MATCH_THRESHOLD and r.id not in cheap_ids)

        # ---- A. fetch the full JD for EVERY new row (no LLM) ----
        # The user wants the whole board to carry a description, not just the
        # LLM-budget top-N. Detail fetch also reveals the OfferToday datePosted,
        # so stale jobs get dropped here like scan-time freshness filtering.
        sem = asyncio.Semaphore(6)
        # Politeness: 每份工之間隔至少 N 秒（預設隨機 4–6 秒，設定頁可調），
        # shared across every phase of this scan（new rows／backfill／enrich）。
        # OfferToday 最怕 request burst（anti-WAF），所以唔好快過 4 秒。
        pace = _PaceGate(tuning.scan_job_delay_min, tuning.scan_job_delay_max)
        dropped_ids: set[int] = set()
        dropped_by_track: dict[str, int] = {}
        fetch_count = {"n": 0}

        async def fill_detail(row: JobApplication, attempts: int = 2) -> None:
            """攞一份工嘅完整 JD。

            用戶要求：新工入庫時就要連 JD 一齊攞到，所以如果中途出錯（例如
            CDP／瀏覽器一閃）會自動重試，重試之前照跟 pacing，唔會突然連環
            開頁。試完都失敗先記錄落 errors，卡片會顯示「未有 JD」，你想睇就
            逐份撳「🔄 更新 JD」。
            """
            async with sem:
                if scan_control.stop_requested():
                    return          # 用戶撳咗暫停：唔好再開新頁（暫停要即刻有效）
                if row.platform in stale_stopped:
                    return          # AI 搜尋組已經撞到過期工 -> 唔再揭呢個渠道
                for attempt in range(1, attempts + 1):
                    try:
                        if await _fill_detail(db, row, _fetch_detail_for(row.platform), pace=pace):
                            dropped_ids.add(row.id)
                            summary.skipped_old += 1
                            dropped_by_track[row.category] = dropped_by_track.get(row.category, 0) + 1
                            return
                        # 一般工：開完詳情先知工地點 -> 唔喺「想去名單」就篩走
                        # （OfferToday 列表唔講工地點，好多時只有 JD 入面寫住）。
                        _verdict = _location_verdict(
                            row.platform, row.category,
                            (row.location, row.title, row.jd_text))
                        if _verdict == "drop" and row.status != "applied":
                            log.info("dropping off-list location job %s/%s (status=%s)",
                                     row.platform, row.job_id_on_platform, row.status)
                            db.delete(row)
                            db.flush()
                            dropped_ids.add(row.id)
                            summary.skipped_location += 1
                            dropped_by_track[row.category] = dropped_by_track.get(row.category, 0) + 1
                            return
                        if _verdict == "uncertain" and not row.location_uncertain:
                            # 保留但標示（供應保護）：UI 會出「⚠ 地點未確定」
                            row.location_uncertain = True
                            summary.location_uncertain += 1
                            db.flush()
                        if row.jd_text:
                            summary.details_fetched += 1
                            fetch_count["n"] += 1
                            set_progress(row.platform, "攞緊 JD", fetch_count["n"])
                        elif attempt < attempts:
                            # 冇例外但今次攞唔到（例如頁面未 load 完）— 等一等再試
                            log.info("no JD yet for %s/%s (第 %d/%d 次)，重試",
                                     row.platform, row.job_id_on_platform, attempt, attempts)
                            await pace.wait()
                            continue
                        return
                    except Exception as e:  # noqa: BLE001
                        if attempt < attempts:
                            log.warning("detail fetch failed for %s/%s (第 %d/%d 次，等陣重試): %s",
                                        row.platform, row.job_id_on_platform,
                                        attempt, attempts, e)
                            await pace.wait()
                            continue
                        log.warning("detail fetch failed for %s/%s (試咗 %d 次，放棄): %s",
                                    row.platform, row.job_id_on_platform, attempt, e)
                        summary.errors.append(f"{row.platform}/{row.job_id_on_platform}: {e}")
                        return

        if new_rows:
            # AI 搜尋組逐份揭（唔並行）：咁樣一撞到「過期」就可以即刻停成個渠道，
            # 唔會出現 6 個 request 同時飛咗出去先發現要停。其他工照舊並行（快）。
            from .tuning import is_ai_search_query as _is_ai_q

            ai_rows = [r for r in new_rows if _is_ai_q(r.source_query or "", tuning)]
            other_rows = [r for r in new_rows if r not in ai_rows]
            await asyncio.gather(*(fill_detail(r) for r in other_rows))
            for r in ai_rows:
                await fill_detail(r)
                if r.platform in stale_stopped or scan_control.stop_requested():
                    break
            candidates = [r for r in candidates if r.id not in dropped_ids]
            # 一般工（冇 LLM）：JD 已經攞到，做平價整理就入庫，唔洗 API 錢
            for row in cheap_rows:
                if row.id in dropped_ids:
                    continue
                _cheap_enrich(row)
                summary.enriched += 1
            if cheap_rows:
                db.flush()
            if scan_control.stop_requested():
                # 用戶要求「撳暫停就要即刻停」：JD 階段未完都唔好再等，
                # 已攞到嘅 JD 即刻 commit（唔會白做），LLM 評分／CL 全部跳過。
                summary.stopped = True
                db.commit()
                log.info("scan stop requested during JD phase — 已攞到嘅 JD 已入庫，"
                         "跳過 LLM 評分／生成 CL")
            if dropped_ids:
                summary.new_jobs = max(0, summary.new_jobs - len(dropped_ids))
                for tname, n in dropped_by_track.items():
                    if tname in summary.tracks:
                        summary.tracks[tname]["new_jobs"] = max(0, summary.tracks[tname]["new_jobs"] - n)

        # ---- B. detail-only backfill: oldest rows still missing a JD ----
        # (只攞 JD，唔用 LLM — 舊工照樣有職位介紹)
        if settings.DETAIL_BACKFILL_PER_SCAN > 0:
            old_rows = _detail_backfill_candidates(db, settings.DETAIL_BACKFILL_PER_SCAN)
            if old_rows:
                await asyncio.gather(*(fill_detail(r) for r in old_rows))

        tasks = []

        async def enrich(row: JobApplication, platform: str, fetch_detail, kind: str) -> None:
            async with sem:
                if scan_control.stop_requested():
                    return          # 用戶撳咗暫停：唔好再洗 LLM 評分／CL
                if row.platform in stale_stopped:
                    return          # AI 搜尋組撞到過期 -> 呢個渠道收工（連 JD 都唔再揭）
                try:
                    set_progress(platform, f"enriching ({kind})", row.title[:40])
                    dropped = await _enrich_one(db, row, platform, fetch_detail, skills, pace=pace)
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

        for row in candidates:
            tasks.append(enrich(row, row.platform, _fetch_detail_for(row.platform), "new"))

        if tasks:
            await asyncio.gather(*tasks)

        # 用戶要求：第二次 scan 起只有「新工」先入 LLM —— scan 期間唔再自動
        # 補評舊工（慳 LLM 錢）。舊工要用 Dashboard「⇪ 補齊」掣 / 詳情頁 refresh
        # 先會手動補。（_backfill_candidates 保留畀嗰啲手動流程用。）

    set_progress("", "done", 0)
    db.commit()
    _AI_RUN_CTX.clear()
    # 用戶要求（可選）：掃描後自動跑一次批量 AI 檢查（預設關，唔想偷偷洗錢）
    if tuning.ai_check_enabled and tuning.ai_check_after_scan and not summary.stopped:
        try:
            from .ai_filter import run_ai_check

            res = await run_ai_check(db, limit=tuning.ai_check_limit,
                                     batch_size=tuning.ai_check_batch_size)
            summary.ai_checked = res.checked
            summary.ai_non_it = res.non_it
            summary.ai_llm_calls = res.batches
            log.info("auto ai check: 檢查 %s 份（%s 次 LLM），標低匹配 %s 份",
                     res.checked, res.batches, res.non_it)
        except Exception as e:  # noqa: BLE001
            log.warning("auto ai check failed: %s", e)
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


async def _fill_detail(db: Session, row: JobApplication, fetch_detail,
                       pace: _PaceGate | None = None, prune: bool = True) -> bool:
    """Fetch the full JD for one row if missing (jobsdb/offertoday only).

    Also records the posted date (incl. OfferToday's JSON-LD datePosted) and
    returns True when the row was DELETED as stale (> MAX_JOB_AGE_DAYS).
    ``pace`` (optional) spaces consecutive page opens during a scan.

    ``prune=False``（補 JD 舊記錄時用）永遠唔會刪行：已申請嘅記錄係你嘅申請
    歷史，其他就只標記為過期（low_match）而唔係刪走。
    """
    if row.jd_text or fetch_detail is None or not can_fetch_detail(row.platform):
        return False
    if pace is not None:
        await pace.wait()
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
        row.posted_date = parse_posted_date(draft.posted_at)
        # 大灣區 7 日（一個星期）；其他渠道 14 日；
        # **AI 搜尋組**（agent／AI 呢啲字詞搵到嘅工）另外收緊到 7 日（用戶要求）。
        max_age = (settings.GBAY_MAX_JOB_AGE_DAYS if row.platform == "govhk_gbayes"
                   else settings.MAX_JOB_AGE_DAYS)
        # AI 搜尋組（agent／AI 字詞搵到嘅工）收緊到 7 日（用戶要求）；
        # context 由 run_scan 設定，冇 context（例如測試直接 call）就照原本上限。
        ctx = _ai_run_ctx()
        stale_action = ctx.get("stale_action")
        is_ai_group = False
        if ctx and getattr(row, "source_query", ""):
            cfg = ctx["cfg_of"](row.category)
            if cfg is not None:
                max_age, is_ai_group = _ai_group_max_age(row.source_query, cfg, max_age)
        if max_age > 0 and not is_fresh(draft.posted_at, max_age):
            if is_ai_group and stale_action is not None:
                # 用戶要求：AI 搜尋組一撞到過期就停（停渠道／停掃描）
                log.info("AI 搜尋組 %s/%s 撞到過期（%s > %s 日）",
                         row.platform, row.job_id_on_platform, draft.posted_at, max_age)
                stale_action(row.platform)
            if prune and row.status != "applied":
                log.info("dropping stale job %s/%s after detail fetch (posted %r, >%sd old)",
                         row.platform, row.job_id_on_platform, draft.posted_at, max_age)
                db.delete(row)
                db.flush()
                return True
            # 保留記錄：已申請嘅工係你嘅申請歷史，唔可以因為過期就刪；補 JD 模式
            # （prune=False）亦一律唔刪，改為標記過期。
            log.info("keeping stale job %s/%s (status=%s, posted %r, >%sd old)",
                     row.platform, row.job_id_on_platform, row.status, draft.posted_at, max_age)
            if row.status != "applied":
                row.status = "low_match"
                row.match_reason = f"刊登日期已超過 {max_age} 日，已過期"
            db.flush()
            return False
    if draft.external_url:
        row.external_url = draft.external_url
        row.apply_method = "external_link"
    _refresh_flags(row)
    db.flush()
    return False


# 一般工未評分嘅標示（UI 會顯示「未評分」chip；詳情頁可以逐份撳「重新整理」補評分）
UNSCORED_REASON = "未 LLM 評分（一般工預設省 API；喺詳情頁撳「重新整理」就可以即刻評分）"


def _cheap_enrich(row: JobApplication) -> None:
    """零 LLM 成本嘅入庫整理：語言、AI／合約／外派標籤、去重 key、狀態、標示。

    用戶要求：一般工唔需要 LLM 評分（只有 IT 工要）。所以呢啲工照樣入職位台、
    照樣有 JD 同標籤，但 `match_score` 係關鍵字分數、`match_level` 留空、
    亦唔會預先生成 CL（想投嘅時候撳「申請」會即場生成，或者詳情頁手動生成）。
    """
    row.jd_language = detect_language(row.jd_text or row.title)
    _refresh_flags(row)
    if row.company and row.title:
        row.dup_key = make_dup_key(row.company, row.title)
    if row.status != "applied":
        # 保留喺職位台（唔會因為「未評分」而被當 low_match 隱藏）
        row.status = "pending_review"
    row.match_reason = UNSCORED_REASON
    row.match_level = ""


def _refresh_flags(row) -> None:
    """重算 AI／合約／外派 flag（攞到 JD 之後會更準）。"""
    flags = compute_flags(row)
    row.ai_match = flags["ai_match"]
    row.ai_strength = flags.get("ai_strength", "")
    row.is_contract = flags["is_contract"]
    row.is_agency = flags["is_agency"]


def _fetch_detail_for(platform: str):
    """邊個 scraper 可以幫呢個 platform 補詳情。

    政府工嘅 platform 係 govhk_it / govhk_gbayes / govhk_general（三個子渠道），
    但 scraper 只有一個 "govhk"，所以要做 prefix 對應。
    """
    for p, _, fetch_detail in PLATFORM_SCRAPERS:
        if p == platform:
            return fetch_detail
    if (platform or "").startswith("govhk"):
        for p, _, fetch_detail in PLATFORM_SCRAPERS:
            if p == "govhk":
                return fetch_detail
    return None


def can_fetch_detail(platform: str) -> bool:
    """呢個 platform 支唔支援補詳情（jobsdb / offertoday / 政府工）。"""
    return platform in ("jobsdb", "offertoday") or (platform or "").startswith("govhk")


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


def unscored_it_candidates(db: Session, limit: int) -> list[JobApplication]:
    """未評分嘅 IT 工（match_score = 0 又未投）—— 「補齊未評分 IT 工」用。

    舊資料好多 IT 工從來冇入過 LLM（scan 預算用喺其他地方），分數永遠係 0，
    排序上永遠沉底。呢個 query 就係為咗一次過補返。
    """
    return (
        db.query(JobApplication)
        .filter(JobApplication.category == "it",
                JobApplication.match_score == 0,
                JobApplication.status != "applied")
        .order_by(JobApplication.created_at.asc())
        .limit(limit)
        .all()
    )


async def _enrich_one(db: Session, row: JobApplication, platform: str,
                      fetch_detail, skills: list[str],
                      pace: _PaceGate | None = None) -> bool:
    """Detail fetch (if missing) + language + match score + CL for one job.

    Returns True when the row was DELETED (OfferToday's datePosted turned out
    to be stale — same treatment as scan-time freshness filtering).
    """
    # 1. full JD if the list only gave a stub (skips when already fetched)
    if await _fill_detail(db, row, fetch_detail, pace=pace):
        return True

    row.jd_language = detect_language(row.jd_text or row.title)
    job_dict = {
        "title": row.title, "company": row.company, "location": row.location,
        "salary_range": row.salary_range, "jd_text": row.jd_text,
        "short_desc": "",
    }
    score, reason, level = await score_job(job_dict, skills)
    row.match_score = score
    row.match_reason = reason
    row.match_level = level
    _refresh_flags(row)
    if row.company and row.title:
        row.dup_key = make_dup_key(row.company, row.title)
    db.flush()

    if score < settings.MATCH_THRESHOLD:
        row.status = "low_match"
        db.flush()
        return False

    # 2. generate CL (with quality check + one retry)
    try:
        cv_text = get_cv_text(row.jd_language, row.title)
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
    except (LLMError, CVError) as e:
        # 冇 LLM key 或者未設定 CV：唔應該令評分／入庫失敗，只係冇 CL
        log.warning("CL generation skipped for %s/%s (score kept): %s",
                    platform, row.job_id_on_platform, e)
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
