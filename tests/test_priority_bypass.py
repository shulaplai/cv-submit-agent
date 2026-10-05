"""高分豁免上限（用戶要求）：評級高／AI 相關嘅工無視渠道同 track 上限照收。

覆蓋：
  - is_priority_job 判斷（優先字詞／分數門檻／開關）
  - gov.hk 渠道軟上限只計非優先工，優先工另有豁免額
  - track 級 MAX_SCAN_JOBS 一樣有豁免
  - 豁免關掉 = 完全舊行為
"""
from conftest import days_ago
import asyncio

from app.config import settings
from app.services import scanner
from app.services.classify import TrackConfig, is_priority_job
from app.services.scraper_base import JobDraft


def _cfg(**kw) -> TrackConfig:
    base = dict(
        name="it", label="IT", keywords=["developer", "engineer", "工程師"],
        it_keywords=["developer", "工程師"], govhk_max_jobs=5,
        offertoday_max_per_search=10, cap_bypass_enabled=True,
        cap_bypass_min_score=70, priority_keywords=["ai", "人工智能"],
        priority_extra_max=2,
    )
    base.update(kw)
    return TrackConfig(**base)


# ---------------------------------------------------------------- unit

def test_priority_by_ai_keyword():
    cfg = _cfg()
    assert is_priority_job("AI Engineer", "", [], cfg) is True
    assert is_priority_job("人工智能工程師", "", [], cfg) is True
    assert is_priority_job("Web Developer", "", [], cfg) is False


def test_priority_by_custom_keyword():
    cfg = _cfg(priority_keywords=["react", "laravel"])
    assert is_priority_job("React Developer", "", [], cfg) is True
    assert is_priority_job("Java Developer", "", [], cfg) is False


def test_priority_by_score_threshold():
    cfg = _cfg(priority_keywords=[], cap_bypass_min_score=10)
    # 冇技能清單 -> keyword_score 中性 50 分 -> 過 10 分門檻
    assert is_priority_job("系統工程師", "", [], cfg) is True
    cfg_high = _cfg(priority_keywords=[], cap_bypass_min_score=90)
    assert is_priority_job("系統工程師", "", [], cfg_high) is False


def test_priority_disabled_when_bypass_off():
    cfg = _cfg(cap_bypass_enabled=False)
    assert is_priority_job("AI Engineer", "", [], cfg) is False


# ---------------------------------------------------------------- gov.hk 渠道

async def _noop_delay(*a, **k):
    return None


def _govhk_harness(monkeypatch, titles, cfg):
    """Fake gov.hk IT list（parse_joblist_html 直接回我哋嘅 items）。"""
    from app.services import scraper_govhk

    items = [{"job_id": f"11-26-{i:07d}", "title": t, "detail_url": f"https://x/{i}",
              "location": "", "salary_range": ""} for i, t in enumerate(titles)]
    fetched: list[str] = []

    monkeypatch.setattr(scraper_govhk, "parse_joblist_html", lambda html: items)
    monkeypatch.setattr(scraper_govhk, "human_delay", _noop_delay)
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 0)

    async def fake_fetch_detail(session, item, platform, category=""):
        fetched.append(item["job_id"])
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="")

    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)

    class FakeResp:
        async def text(self):
            return "<html></html>"

        async def dispose(self):
            pass

    class FakeRequest:
        async def post(self, *a, **k):
            return FakeResp()

        async def get(self, *a, **k):
            return FakeResp()

    class FakeSession:
        context = type("Ctx", (), {"request": FakeRequest()})()

    return scraper_govhk, FakeSession(), fetched


def test_govhk_soft_cap_counts_only_normal_jobs(monkeypatch):
    """軟上限 3：頭 3 份普通工收；之後嘅普通工唔會再開詳情頁。"""
    cfg = _cfg(govhk_max_jobs=3, priority_extra_max=0)
    titles = ["Web Developer"] * 6
    scraper_govhk, session, fetched = _govhk_harness(monkeypatch, titles, cfg)

    drafts = asyncio.run(scraper_govhk._scrape_it(session, set(), cfg))
    assert len(drafts) == 3
    assert len(fetched) == 3          # 冇為咗被篩走嘅工白開詳情頁


def test_govhk_priority_jobs_bypass_the_cap(monkeypatch):
    """軟上限 3 + 豁免 2 -> 收 3 份普通 + 2 份 AI（AI 無視上限）。"""
    cfg = _cfg(govhk_max_jobs=3, priority_extra_max=2)
    titles = ["Web Developer", "Web Developer", "Web Developer",
              "AI Engineer", "AI Engineer", "AI Engineer", "AI Engineer"]
    scraper_govhk, session, _fetched = _govhk_harness(monkeypatch, titles, cfg)

    drafts = asyncio.run(scraper_govhk._scrape_it(session, set(), cfg))
    titles_out = [d.title for d in drafts]
    assert titles_out.count("AI Engineer") == 2       # 豁免額上限 2
    assert len(drafts) == 5                            # 3 普通 + 2 豁免
    assert "Web Developer" not in titles_out[3:]       # 第 4 份普通工已經唔收


def test_govhk_cap_without_bypass_is_old_behaviour(monkeypatch):
    cfg = _cfg(govhk_max_jobs=2, cap_bypass_enabled=False, priority_extra_max=5)
    titles = ["AI Engineer", "AI Engineer", "AI Engineer", "AI Engineer"]
    scraper_govhk, session, _f = _govhk_harness(monkeypatch, titles, cfg)

    drafts = asyncio.run(scraper_govhk._scrape_it(session, set(), cfg))
    assert len(drafts) == 2                            # 豁免關掉 -> 照舊硬上限


# ---------------------------------------------------------------- track 級

def _scan_harness(monkeypatch, drafts):
    async def fake_scrape(session, track="it", cfg=None, channels=None):
        return drafts

    async def fake_fetch_detail(session, d):
        d.jd_text = f"JD {d.job_id}"
        d.posted_at = days_ago(1)
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        return (30, "低分", "")

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0)


def test_max_scan_jobs_keeps_priority_jobs(db, monkeypatch):
    """MAX_SCAN_JOBS = 2：3 份 AI 工全部照收（豁免），普通工最多 2 份。"""
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 2)
    monkeypatch.setattr(settings, "PRIORITY_EXTRA_MAX", 10)
    monkeypatch.setattr(settings, "CAP_BYPASS_MIN_SCORE", 70)
    monkeypatch.setattr(settings, "CAP_BYPASS_ENABLED", True)

    drafts = [JobDraft(platform="offertoday", job_id=f"ai{i}", title="AI Developer",
                       posted_at="", category="it") for i in range(3)]
    drafts += [JobDraft(platform="offertoday", job_id=f"web{i}", title="Web Developer",
                        posted_at="", category="it") for i in range(4)]
    _scan_harness(monkeypatch, drafts)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.new_jobs == 5              # 3 AI（豁免）+ 2 普通
    assert summary.priority_kept >= 3

    from app.models import JobApplication
    kept = {r.title for r in db.query(JobApplication).all()}
    assert kept == {"AI Developer", "Web Developer"}


def test_max_scan_jobs_priority_extra_caps_the_bypass(db, monkeypatch):
    """豁免額（priority_extra_max）用盡就唔再收 -> 記錄 priority_capped。"""
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 1)
    monkeypatch.setattr(settings, "PRIORITY_EXTRA_MAX", 2)
    monkeypatch.setattr(settings, "CAP_BYPASS_ENABLED", True)

    drafts = [JobDraft(platform="offertoday", job_id=f"ai{i}", title="AI Developer",
                       posted_at="", category="it") for i in range(5)]
    drafts += [JobDraft(platform="offertoday", job_id=f"web{i}", title="Web Developer",
                        posted_at="", category="it") for i in range(3)]
    _scan_harness(monkeypatch, drafts)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.new_jobs == 3              # 1 普通 + 2 豁免
    assert summary.priority_kept == 2
    assert summary.priority_capped == 3       # 3 份 AI 因為豁免額滿被擋
    assert summary.capped == 5                # 2 普通 + 3 AI


def test_max_scan_jobs_without_bypass(db, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 2)
    monkeypatch.setattr(settings, "CAP_BYPASS_ENABLED", False)

    drafts = [JobDraft(platform="offertoday", job_id=f"ai{i}", title="AI Developer",
                       posted_at="", category="it") for i in range(4)]
    _scan_harness(monkeypatch, drafts)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.new_jobs == 2
    assert summary.priority_kept == 0
