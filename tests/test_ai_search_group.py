"""AI 搜尋組（用戶要求）：

  - 用「agent」「AI」呢啲字詞專門搵 AI 工，獨立搜尋預算（唔同其他字詞爭）
  - 呢組嘅工刊登日期要喺 **7 日內**；一撞到過期就停（停該渠道，或暫停成個 scan）
  - 標題有 AI／agent 嘅工「一定要收」：無視渠道／track 上限
  - AI 相關度分「標題」同「只喺 JD 提到」兩級
"""
import asyncio

import pytest

from app.config import settings
from app.models import JobApplication, Profile
from app.services import scan_control, scanner, tuning
from app.services.classify import TrackConfig
from app.services.scraper_base import JobDraft

from conftest import days_ago_dmy


def _cfg(**kw) -> TrackConfig:
    base = dict(name="it", label="IT", keywords=["developer", "AI"],
                it_keywords=["developer", "AI"], govhk_max_jobs=0,
                offertoday_max_per_search=10, cap_bypass_enabled=True,
                cap_bypass_min_score=70, priority_keywords=["ai", "agent"],
                priority_extra_max=5, ai_search_terms=["agent", "AI"],
                ai_search_max_searches=2, ai_search_max_age_days=7,
                ai_stale_action="channel")
    base.update(kw)
    return TrackConfig(**base)


# --------------------------------------------------------------- 判斷

def test_ai_search_query_detection(db):
    t = tuning.load_tuning(db)
    assert tuning.is_ai_search_query("agent", t) is True
    assert tuning.is_ai_search_query("AI", t) is True
    assert tuning.is_ai_search_query("developer", t) is False
    assert tuning.is_ai_search_query("FDE", t) is False
    assert tuning.is_ai_search_query("", t) is False


def test_ai_search_terms_default_and_override(db):
    assert tuning.ai_search_terms(tuning.load_tuning(db)) == ["agent", "AI"]
    db.add(Profile(id=1, ai_search_terms="agent,AI,LLM"))
    db.commit()
    assert tuning.ai_search_terms(tuning.load_tuning(db)) == ["agent", "AI", "LLM"]


def test_ai_group_max_age(db):
    cfg = _cfg()
    assert scanner._ai_group_max_age("agent", cfg, 14) == (7, True)
    assert scanner._ai_group_max_age("AI", cfg, 14) == (7, True)
    assert scanner._ai_group_max_age("developer", cfg, 14) == (14, False)
    assert scanner._ai_group_max_age("category", cfg, 14) == (14, False)
    # 關掉（0）-> 用原本上限
    cfg_off = _cfg(ai_search_max_age_days=0)
    assert scanner._ai_group_max_age("agent", cfg_off, 14) == (14, False)


# --------------------------------------------------------------- OfferToday 標記

def test_offertoday_ai_group_page_is_marked(monkeypatch):
    """AI 組搜尋頁帶入嘅 draft 要記住 source_query／ai_search（7 日限制靠佢）。"""
    from app.services import scraper_offertoday

    cfg = _cfg(offertoday_search_terms=["developer"])
    calls = []

    class FakeLink:
        def __init__(self, token, title):
            self.token, self.title = token, title

        async def get_attribute(self, name):
            return f"/hk/job/{self.token}"

        async def inner_text(self):
            return self.title

        async def evaluate(self, fn):
            return self.title

        async def count(self):
            return 1

    class FakeLocator:
        def __init__(self, links):
            self.links = links

        async def count(self):
            return len(self.links)

        def nth(self, i):
            return self.links[i]

    class FakePage:
        def __init__(self, links):
            self._links = links

        def locator(self, sel):
            return FakeLocator(self._links)

        async def close(self):
            pass

    counter = {"n": 0}

    async def fake_open_page(ctx, url):
        calls.append(url)
        counter["n"] += 1
        n = counter["n"]                      # 每頁唔同 token（唔會被去重食掉）
        return FakePage([FakeLink(f"t{n}a", "AI Engineer"),
                         FakeLink(f"t{n}b", "Web Developer")])

    async def fake_scroll(page, target):
        pass

    async def fake_delay(*a, **k):
        return None

    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_offertoday, "_scroll_search", fake_scroll)
    monkeypatch.setattr(scraper_offertoday, "human_delay", fake_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_offertoday.scrape(FakeSession(), track="it", cfg=cfg))

    by_query = {d.source_query for d in drafts}
    assert "agent" in by_query or "AI" in by_query        # 有 AI 組嘅 draft
    ai_drafts = [d for d in drafts if d.source_query in ("agent", "AI")]
    assert ai_drafts and all(d.raw.get("ai_search") is True for d in ai_drafts)
    # AI 組頁面真係有開過
    assert any("agent-jobs" in u or "/AI-jobs" in u for u in calls)


# --------------------------------------------------------------- 7 日限制 + 過期即停

def _harness(monkeypatch, drafts, *, posted, fetch_log, ai_stale_action="channel"):
    async def fake_scrape(session, track="it", cfg=None, channels=None):
        if track in ("it", "general"):
            return [d for d in drafts if d.category == track]
        return drafts

    async def fake_fetch_detail(session, d):
        fetch_log.append(d.job_id)
        d.jd_text = f"JD {d.job_id}"
        d.posted_at = posted
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        return (30, "低分", "")

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 14)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0)
    scan_control.clear_stop()
    return ai_stale_action


def test_ai_group_drops_10_day_old_job_but_keeps_dev_job(db, monkeypatch):
    """AI 組（agent 字詞）>7 日 -> 丟；同一日嘅 developer 字詞 -> 保留（14 日窗口）。"""
    db.add(Profile(id=1, ai_search_terms="agent,AI", ai_search_max_age_days=7,
                   ai_stale_action="channel", ai_search_max_searches=2))
    db.commit()

    drafts = [
        JobDraft(platform="offertoday", job_id="ai1", title="AI Engineer",
                 posted_at="", category="it", source_query="agent"),
        JobDraft(platform="offertoday", job_id="dev1", title="Web Developer",
                 posted_at="", category="it", source_query="developer"),
    ]
    fetch_log: list[str] = []
    _harness(monkeypatch, drafts, posted=days_ago_dmy(10), fetch_log=fetch_log)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))

    ids = {r.job_id_on_platform for r in db.query(JobApplication).all()}
    assert "ai1" not in ids          # AI 組 10 日前 -> 丟
    assert "dev1" in ids             # 其他字詞 10 日前 -> 留（14 日內）
    assert summary.skipped_old >= 1
    scan_control.clear_stop()


def test_ai_group_stale_stops_the_channel(db, monkeypatch):
    """channel 模式：一撞到過期 AI 工就唔再揭呢個渠道嘅其他工。"""
    db.add(Profile(id=1, ai_search_terms="agent", ai_search_max_age_days=7,
                   ai_stale_action="channel", ai_search_max_searches=2))
    db.commit()

    drafts = [
        JobDraft(platform="offertoday", job_id="ai1", title="AI Engineer",
                 posted_at="", category="it", source_query="agent"),
        JobDraft(platform="offertoday", job_id="ai2", title="AI Developer",
                 posted_at="", category="it", source_query="agent"),
        JobDraft(platform="offertoday", job_id="ai3", title="Agent Engineer",
                 posted_at="", category="it", source_query="agent"),
    ]
    fetch_log: list[str] = []
    _harness(monkeypatch, drafts, posted=days_ago_dmy(9), fetch_log=fetch_log)

    asyncio.run(scanner.run_scan(db, {}, track="it"))
    # 第一個就已經過期 -> 停渠道 -> 之後兩個唔會再揭
    assert fetch_log == ["ai1"]
    scan_control.clear_stop()


def test_ai_group_stale_can_pause_the_whole_scan(db, monkeypatch):
    """scan 模式：撞到過期 AI 工 -> 暫停成個掃描。"""
    db.add(Profile(id=1, ai_search_terms="agent", ai_search_max_age_days=7,
                   ai_stale_action="scan", ai_search_max_searches=2))
    db.commit()

    drafts = [JobDraft(platform="offertoday", job_id="ai1", title="AI Engineer",
                       posted_at="", category="it", source_query="agent"),
              JobDraft(platform="offertoday", job_id="dev1", title="Web Developer",
                       posted_at="", category="it", source_query="developer")]
    fetch_log: list[str] = []
    _harness(monkeypatch, drafts, posted=days_ago_dmy(30), fetch_log=fetch_log)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.stopped is True
    assert scan_control.stop_requested() is True
    scan_control.clear_stop()


def test_ai_group_titles_always_kept(db, monkeypatch):
    """用戶要求：標題有 AI／agent 嘅工 scan 到就一定要收（無視上限）。"""
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 1)
    monkeypatch.setattr(settings, "PRIORITY_EXTRA_MAX", 10)
    monkeypatch.setattr(settings, "CAP_BYPASS_ENABLED", True)

    drafts = [
        JobDraft(platform="offertoday", job_id="ai1", title="AI Engineer",
                 posted_at="", category="it", source_query="agent"),
        JobDraft(platform="offertoday", job_id="web1", title="Web Developer",
                 posted_at="", category="it", source_query="developer"),
        JobDraft(platform="offertoday", job_id="web2", title="Web Developer",
                 posted_at="", category="it", source_query="developer"),
        JobDraft(platform="offertoday", job_id="web3", title="Web Developer",
                 posted_at="", category="it", source_query="developer"),
    ]
    fetch_log: list[str] = []
    _harness(monkeypatch, drafts, posted=days_ago_dmy(1), fetch_log=fetch_log)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    ids = {r.job_id_on_platform for r in db.query(JobApplication).all()}
    assert "ai1" in ids                 # AI 工唔會被 MAX_SCAN_JOBS 擋
    assert summary.priority_kept >= 1
    scan_control.clear_stop()


def test_source_query_is_persisted(db, monkeypatch):
    """邊個字詞帶入呢份工要入 DB（睇 AI 搜尋組成效）。"""
    drafts = [JobDraft(platform="offertoday", job_id="q1", title="AI Engineer",
                       posted_at="", category="it", source_query="agent")]
    fetch_log: list[str] = []
    _harness(monkeypatch, drafts, posted=days_ago_dmy(1), fetch_log=fetch_log)
    asyncio.run(scanner.run_scan(db, {}, track="it"))
    row = db.query(JobApplication).filter_by(job_id_on_platform="q1").one()
    assert row.source_query == "agent"
    assert row.ai_match is True
    assert row.ai_strength == "title"
    scan_control.clear_stop()
