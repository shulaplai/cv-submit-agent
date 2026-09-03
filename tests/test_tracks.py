"""Tests for the 一般 (non-IT) track channels + OfferToday publish date."""
import asyncio
from pathlib import Path

from app.services.classify import TrackConfig
from app.services.scraper_base import JobDraft

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------ OfferToday date

def test_offertoday_fetch_detail_extracts_dateposted(monkeypatch):
    """OfferToday publishes the date via JSON-LD datePosted — must land in posted_at."""
    from app.services import scraper_offertoday

    html = '<html><script type="application/ld+json">{"@type":"JobPosting",' \
           '"datePosted":"2026-08-05","validThrough":"2027-08-06"}</script></html>'
    text = ("首頁\nHelpdesk Support ( 5-days)\nDELKEN GROUP LIMITED·人力資源管理/顧問\n"
            "HK $30K-33K/月\n工作內容\nDo things.\n語言技能\n英文")

    class FakeBody:
        async def inner_text(self):
            return text

    class FakePage:
        async def content(self):
            return html

        def locator(self, sel):
            assert sel == "body"
            return FakeBody()

        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        return FakePage()

    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)

    class FakeSession:
        context = object()

    draft = JobDraft(platform="offertoday", job_id="tok", title="Helpdesk Support ( 5-days)",
                     url="https://www.offertoday.com/hk/job/tok")
    draft = asyncio.run(scraper_offertoday.fetch_detail(FakeSession(), draft))
    assert draft.posted_at == "2026-08-05"


def test_offertoday_fetch_detail_keeps_empty_when_no_jsonld(monkeypatch):
    from app.services import scraper_offertoday

    class FakePage:
        async def content(self):
            return "<html>no schema here</html>"

        def locator(self, sel):
            class B:
                async def inner_text(self):
                    return "title\n工作內容\nno salary"
            return B()

        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        return FakePage()

    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)
    draft = JobDraft(platform="offertoday", job_id="tok", title="title",
                     url="https://www.offertoday.com/hk/job/tok")
    class FakeSession:
        context = object()

    draft = asyncio.run(scraper_offertoday.fetch_detail(FakeSession(), draft))
    assert draft.posted_at == ""  # unknown date -> kept by the freshness filter


# ------------------------------------------------------------ OfferToday 一般 track

def test_offertoday_it_track_adds_keyword_searches():
    """IT track: 3 個分類頁 + 額外關鍵字搜尋（AI agent 等）。"""
    from urllib.parse import quote

    from app.services import scraper_offertoday

    cfg = TrackConfig.defaults("it")
    cfg.offertoday_search_terms = ["AI Agent", "人工智能"]
    cfg.max_searches = 4

    urls = scraper_offertoday._search_urls_for(cfg)
    assert len(urls) == 3 + 2
    assert urls[0] == scraper_offertoday.SEARCH_URLS[0]
    assert "AI%20Agent-jobs" in urls[3]      # quote("AI Agent") -> AI%20Agent
    assert quote("人工智能") in urls[4]       # 中文 -> percent-encoded

    # 冇 terms -> 淨係分類頁
    cfg.offertoday_search_terms = []
    assert len(scraper_offertoday._search_urls_for(cfg)) == 3


def test_offertoday_general_track_keyword_search(monkeypatch):
    """一般 track searches <kw>-jobs pages and keeps only general-classified titles."""
    from app.services import scraper_offertoday

    cfg = TrackConfig.defaults("general")
    cfg.offertoday_max_per_search = 10

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
        def __init__(self):
            self._links = [FakeLink("t1", "文員"), FakeLink("t2", "AI Developer"),
                           FakeLink("t3", "行政助理"), FakeLink("t4", "Software Engineer")]

        def locator(self, sel):
            return FakeLocator(self._links)

        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        assert "search/文員-jobs" in url or "search/%E6%96%87%E5%93%A1-jobs" in url
        return FakePage()

    async def fake_scroll(page, target):
        pass

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_offertoday, "_scroll_search", fake_scroll)
    monkeypatch.setattr(scraper_offertoday, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_offertoday.scrape(FakeSession(), track="general", cfg=cfg))
    titles = {d.title for d in drafts}
    assert titles == {"文員", "行政助理"}  # IT titles excluded
    assert all(d.category == "general" for d in drafts)


# ------------------------------------------------------------ gov.hk 一般 channel

def test_govhk_general_channel_filters_and_caps(monkeypatch):
    """gov.hk 一般 channel: main quickview, general keywords only, IT excluded, capped."""
    from app.services import scraper_govhk

    fixture = (FIXTURES / "govhk_quickview_general.html").read_text(encoding="utf-8")
    cfg = TrackConfig.defaults("general")
    cfg.govhk_max_jobs = 10

    class FakePage:
        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        assert "quickview/?direct=False" in url
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_fetch_detail(session, item, platform, category=""):
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="30/08/2026",
                        category=category)

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_govhk, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_govhk, "grab_html", fake_grab_html)
    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)
    monkeypatch.setattr(scraper_govhk, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_govhk._scrape_general(FakeSession(), set(), cfg))
    titles = {d.title for d in drafts}
    assert titles == {"文員", "行政助理"}          # 資訊科技工程師 excluded
    assert all(d.category == "general" for d in drafts)
    assert all(d.platform == "govhk_general" for d in drafts)


def test_govhk_general_channel_caps_at_limit(monkeypatch):
    """一般 channel stops after cfg.govhk_max_jobs drafts."""
    from app.services import scraper_govhk

    # fixture has 2 general items; cap=1 -> exactly 1 kept
    fixture = (FIXTURES / "govhk_quickview_general.html").read_text(encoding="utf-8")
    cfg = TrackConfig.defaults("general")
    cfg.govhk_max_jobs = 1

    class FakePage:
        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_fetch_detail(session, item, platform, category=""):
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="30/08/2026",
                        category=category)

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_govhk, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_govhk, "grab_html", fake_grab_html)
    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)
    monkeypatch.setattr(scraper_govhk, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_govhk._scrape_general(FakeSession(), set(), cfg))
    assert len(drafts) == 1
    assert drafts[0].title == "文員"


def test_govhk_scrape_dispatches_by_track(monkeypatch):
    """scrape(track='general') only runs the general channel; 'it' runs gbayes+it."""
    from app.services import scraper_govhk

    ran = {"general": 0, "gbayes": 0, "it": 0}

    async def fake_general(session, seen, cfg):
        ran["general"] += 1
        return []

    async def fake_gbayes(session, seen, cfg):
        ran["gbayes"] += 1
        return []

    async def fake_it(session, seen, cfg):
        ran["it"] += 1
        return []

    monkeypatch.setattr(scraper_govhk, "_scrape_general", fake_general)
    monkeypatch.setattr(scraper_govhk, "_scrape_gbayes", fake_gbayes)
    monkeypatch.setattr(scraper_govhk, "_scrape_it", fake_it)

    asyncio.run(scraper_govhk.scrape(object(), track="general"))
    assert ran == {"general": 1, "gbayes": 0, "it": 0}

    asyncio.run(scraper_govhk.scrape(object(), track="it"))
    assert ran == {"general": 1, "gbayes": 1, "it": 1}


def test_run_scan_drops_stale_offertoday_after_detail(db, monkeypatch):
    """OfferToday datePosted discovered at enrich time -> row dropped + counted."""
    import asyncio

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 5)  # enrich enabled
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)

    draft = JobDraft(platform="offertoday", job_id="tokOld",
                     title="AI Developer", posted_at="")  # date unknown at scrape

    async def fake_scrape(session, track="it", cfg=None):
        return [draft]

    async def fake_fetch_detail(session, d):
        d.posted_at = "01/01/2026"  # stale (>60d) — revealed by the detail fetch
        return d

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.new_jobs == 0          # persisted then dropped
    assert summary.skipped_old == 1       # counted as stale
    assert summary.enriched == 0
    assert db.query(JobApplication).count() == 0  # row gone


# ------------------------------------------------------------ JD for all jobs

def test_run_scan_fetches_detail_for_all_new_rows(db, monkeypatch):
    """JD fetched for EVERY new row (not just the LLM budget); budget caps LLM only."""
    import asyncio

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 1)   # LLM budget = 1
    monkeypatch.setattr(settings, "ENRICH_ALL_IT", False)     # 呢個測試驗證 budget 上限
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)
    monkeypatch.setattr(settings, "MATCH_THRESHOLD", 90)      # all LLM-scored -> low_match (no CL)

    drafts = [JobDraft(platform="offertoday", job_id=f"tok{i}",
                       title="AI Developer", posted_at="") for i in range(3)]
    fetched = []

    async def fake_scrape(session, track="it", cfg=None):
        return drafts

    async def fake_fetch_detail(session, d):
        fetched.append(d.job_id)
        d.jd_text = f"職責：開發 AI 系統 {d.job_id}"
        d.posted_at = "2026-08-01"
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        return (30, "低分")

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.details_fetched == 3          # all 3 JDs fetched
    assert len(fetched) == 3
    assert summary.enriched == 1                 # LLM budget respected

    rows = db.query(JobApplication).all()
    assert len(rows) == 3
    assert all(r.jd_text for r in rows)          # every row carries a JD
    assert all(r.posted_at == "2026-08-01" for r in rows)


def test_run_scan_enriches_all_it_rows(db, monkeypatch):
    """ENRICH_ALL_IT: 所有新 IT 工都 LLM 完整評分（唔限）；一般工維持 top-N。"""
    import asyncio

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 2)   # 細 budget
    monkeypatch.setattr(settings, "ENRICH_ALL_IT", True)
    monkeypatch.setattr(settings, "MATCH_THRESHOLD", 90)      # all -> low_match (no CL)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0)

    drafts = [JobDraft(platform="offertoday", job_id=f"it{i}",
                       title="AI Developer", posted_at="", category="it") for i in range(5)]
    drafts += [JobDraft(platform="offertoday", job_id=f"gen{i}",
                        title="文員", posted_at="", category="general") for i in range(3)]

    async def fake_scrape(session, track="it", cfg=None):
        return drafts

    async def fake_fetch_detail(session, d):
        d.jd_text = f"職責：{d.title}"
        d.posted_at = "2026-08-01"
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        return (30, "低分")

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))

    rows = db.query(JobApplication).all()
    it_rows = [r for r in rows if r.category == "it"]
    gen_rows = [r for r in rows if r.category == "general"]
    assert len(it_rows) == 5
    assert all(r.match_reason == "低分" for r in it_rows)     # 全部 IT 都 LLM 評咗分
    assert len(gen_rows) == 3
    assert sum(1 for r in gen_rows if r.match_reason) <= 2    # 一般工維持 top-N（budget=2）
    assert summary.enriched == 7


def test_run_scan_detail_backfill_fills_old_rows(db, monkeypatch):
    """Old rows missing a JD get one via the detail-only backfill (no LLM)."""
    import asyncio

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)  # no LLM at all
    monkeypatch.setattr(settings, "DETAIL_BACKFILL_PER_SCAN", 5)

    # an old row stuck without a JD
    old = JobApplication(platform="offertoday", job_id_on_platform="oldTok",
                         title="AI Developer", jd_text="", status="pending_review")
    db.add(old)
    db.commit()

    async def fake_scrape(session, track="it", cfg=None):
        return []

    async def fake_fetch_detail(session, d):
        d.jd_text = "職責：補返 JD"
        d.posted_at = "2026-08-01"
        return d

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.details_fetched == 1
    assert summary.enriched == 0                 # no LLM work

    row = db.get(JobApplication, old.id)
    assert row.jd_text == "職責：補返 JD"


def test_backfill_skips_low_match_rows(db, monkeypatch):
    """low_match rows must not be re-LLM-scored by the backfill every scan."""
    from app.services.scanner import _backfill_candidates

    from app.models import JobApplication

    low = JobApplication(platform="offertoday", job_id_on_platform="lowTok",
                         title="AI Developer", status="low_match", match_score=20)
    pending = JobApplication(platform="offertoday", job_id_on_platform="pendTok",
                             title="AI Developer", status="pending_review")
    db.add_all([low, pending])
    db.commit()

    rows = _backfill_candidates(db, 10)
    ids = {r.job_id_on_platform for r in rows}
    assert ids == {"pendTok"}   # low_match excluded
