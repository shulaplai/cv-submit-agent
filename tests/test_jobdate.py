"""Tests for posting-date parsing and the scan freshness filter."""
import asyncio
from datetime import date, timedelta

import pytest

from app.services import scan_control
from app.services.jobdate import is_fresh, parse_posted_date
from app.services.scraper_base import JobDraft


def test_scan_control_flag():
    scan_control.clear_stop()
    assert scan_control.stop_requested() is False
    scan_control.request_stop()
    assert scan_control.stop_requested() is True
    scan_control.clear_stop()
    assert scan_control.stop_requested() is False


def test_parse_govhk_dmy():
    assert parse_posted_date("11/08/2026") == date(2026, 8, 11)
    assert parse_posted_date("01/04/2026") == date(2026, 4, 1)


def test_parse_jobsdb_relative():
    today = date.today()
    assert parse_posted_date("1d ago") == today - timedelta(days=1)
    assert parse_posted_date("30+ days ago") == today - timedelta(days=30)
    assert parse_posted_date("2w ago") == today - timedelta(days=14)
    assert parse_posted_date("3mo ago") == today - timedelta(days=90)
    assert parse_posted_date("1y ago") == today - timedelta(days=365)
    assert parse_posted_date("today") == today
    assert parse_posted_date("Yesterday") == today - timedelta(days=1)


def test_parse_jobsdb_decorated():
    # SEEK cards sometimes append "•\nExpiring"
    assert parse_posted_date("24d ago\n•\nExpiring") == date.today() - timedelta(days=24)


def test_parse_unknown_or_empty():
    assert parse_posted_date("") is None
    assert parse_posted_date("最新") is None
    assert parse_posted_date("成為最早的申請者") is None


def test_is_fresh_keeps_recent_and_unknown():
    assert is_fresh("1d ago", 60) is True
    assert is_fresh("11/08/2026", 60) is True
    # OfferToday has no posting date -> kept
    assert is_fresh("", 60) is True
    assert is_fresh("成為最早的申請者", 60) is True


def test_is_fresh_drops_stale():
    assert is_fresh("3mo ago", 60) is False
    assert is_fresh("01/04/2026", 60) is False


def test_is_fresh_boundary():
    # exactly 60 days old -> kept (within two months)
    assert is_fresh((date.today() - timedelta(days=60)).strftime("%d/%m/%Y"), 60) is True
    assert is_fresh((date.today() - timedelta(days=61)).strftime("%d/%m/%Y"), 60) is False


# ------------------------------------------------------------ scan integration

def test_run_scan_filters_stale_drafts(db, monkeypatch):
    """run_scan must not persist jobs posted more than 60 days ago."""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)  # no LLM in tests

    fresh = JobDraft(platform="govhk", job_id="11-26-0000101",
                     title="AI 工程師", posted_at="01/08/2026")
    stale = JobDraft(platform="govhk", job_id="11-26-0000102",
                     title="AI 工程師", posted_at="01/03/2026")
    unknown = JobDraft(platform="offertoday", job_id="tok",
                       title="AI Developer", posted_at="")

    async def fake_scrape(session):
        return [fresh, stale, unknown]

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}))
    assert summary.skipped_old == 1
    assert summary.scanned == 3

    from app.models import JobApplication
    ids = {r.job_id_on_platform for r in db.query(JobApplication).all()}
    assert "11-26-0000101" in ids
    assert "11-26-0000102" not in ids  # stale dropped
    assert "tok" in ids                # unknown date kept


def test_run_scan_caps_at_max_jobs_fair_share(db, monkeypatch):
    """MAX_SCAN_JOBS caps total drafts per scan, round-robin across platforms."""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 3)

    # gov.hk returns 3 fresh jobs, offertoday returns 3 -> cap 3 total
    gov = [JobDraft(platform="govhk", job_id=f"11-26-00002{i:02d}",
                    title="AI 工程師", posted_at="01/08/2026") for i in range(3)]
    ot = [JobDraft(platform="offertoday", job_id=f"tok{i}",
                   title="AI Developer", posted_at="") for i in range(3)]

    async def fake_scrape(session):
        return gov + ot

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}))
    assert summary.capped == 3  # 6 drafts -> 3 kept
    assert summary.new_jobs == 3

    from app.models import JobApplication
    rows = db.query(JobApplication).all()
    platforms = sorted({r.platform for r in rows})
    assert platforms == ["govhk", "offertoday"]  # fair share: 2 + 1 or 1 + 2


def test_run_scan_cap_disabled_when_zero(db, monkeypatch):
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)

    drafts = [JobDraft(platform="govhk", job_id=f"11-26-00003{i:02d}",
                       title="AI 工程師", posted_at="01/08/2026") for i in range(4)]

    async def fake_scrape(session):
        return drafts

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}))
    assert summary.capped == 0
    assert summary.new_jobs == 4


# ------------------------------------------------------------ stop / 暫停

def test_run_scan_stop_persists_scraped_drafts(db, monkeypatch):
    """暫停掣：request_stop 後 run_scan 中斷，但已掃到嘅 drafts 照樣入庫。"""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)

    first = JobDraft(platform="govhk", job_id="11-26-0000401",
                     title="AI 工程師", posted_at="01/08/2026")
    second = JobDraft(platform="govhk", job_id="11-26-0000402",
                      title="AI 工程師", posted_at="01/08/2026")

    async def fake_scrape(session):
        scan_control.request_stop()  # stop requested DURING scraping
        return [first, second]

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    scan_control.clear_stop()

    summary = asyncio.run(scanner.run_scan(db, {}))
    assert summary.stopped is True
    assert summary.scanned == 2
    assert summary.enriched == 0  # LLM phase skipped on stop

    from app.models import JobApplication
    ids = {r.job_id_on_platform for r in db.query(JobApplication).all()}
    assert "11-26-0000401" in ids
    assert "11-26-0000402" in ids


def test_run_scan_stop_between_platforms(db, monkeypatch):
    """Stop requested between platforms -> later platforms not scraped."""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)

    called = {"n": 0}
    state = {"stop": False}

    async def scrape_a(session):
        called["n"] += 1
        state["stop"] = True  # request stop right after platform A completes
        return [JobDraft(platform="govhk", job_id="11-26-0000501",
                         title="AI 工程師", posted_at="01/08/2026")]

    async def scrape_b(session):
        called["n"] += 1
        return [JobDraft(platform="offertoday", job_id="tokB",
                         title="AI Developer", posted_at="")]

    async def scrape_c(session):
        called["n"] += 1
        return []

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", scrape_a, None),
                         ("offertoday", scrape_b, None),
                         ("jobsdb", scrape_c, None)))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner.scan_control, "stop_requested",
                        lambda: state["stop"])
    scan_control.clear_stop()

    summary = asyncio.run(scanner.run_scan(db, {}))
    assert summary.stopped is True
    assert called["n"] == 1  # only platform A ran

    from app.models import JobApplication
    ids = {r.job_id_on_platform for r in db.query(JobApplication).all()}
    assert "11-26-0000501" in ids
    assert "tokB" not in ids


# ------------------------------------------------------------ gov.hk early stop

def test_govhk_too_old_helper(monkeypatch):
    from app.config import settings
    from app.services.scraper_govhk import _too_old

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    assert _too_old("01/03/2026") is True   # older than 60 days
    assert _too_old("01/08/2026") is False  # recent
    assert _too_old("") is False            # unknown -> keep
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 0)
    assert _too_old("01/03/2026") is False  # filter disabled


def test_gbayes_stops_at_stale(monkeypatch):
    """First stale posting on the (newest-first) list stops the gbayes channel."""
    import asyncio
    from pathlib import Path

    from app.config import settings
    from app.services import scraper_govhk
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    fixture = (Path(__file__).parent / "fixtures" / "govhk_page1.html").read_text(encoding="utf-8")

    class FakePage:
        async def close(self):
            pass

    fetch_count = {"n": 0}

    async def fake_open_page(ctx, url):
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_fetch_detail(session, item, platform):
        fetch_count["n"] += 1
        # first match (資訊科技工程師) is stale -> channel must stop
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="01/03/2026")

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_govhk, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_govhk, "grab_html", fake_grab_html)
    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)
    monkeypatch.setattr(scraper_govhk, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_govhk._scrape_gbayes(FakeSession(), set()))
    assert fetch_count["n"] == 1          # stopped after the first stale job
    assert len(drafts) == 1
    assert drafts[0].job_id == "21-26-0008159"


def test_gbayes_keeps_scanning_while_fresh(monkeypatch):
    """While postings are fresh the channel keeps going to later pages."""
    import asyncio
    from pathlib import Path

    from app.config import settings
    from app.services import scraper_govhk
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    fixture = (Path(__file__).parent / "fixtures" / "govhk_page1.html").read_text(encoding="utf-8")

    class FakePage:
        async def close(self):
            pass

    pages = {"n": 0}

    async def fake_open_page(ctx, url):
        pages["n"] += 1
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_fetch_detail(session, item, platform):
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="01/08/2026")

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_govhk, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_govhk, "grab_html", fake_grab_html)
    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)
    monkeypatch.setattr(scraper_govhk, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_govhk._scrape_gbayes(FakeSession(), set()))
    assert pages["n"] >= 2                # kept scanning pages
    assert len(drafts) >= 1


def test_govhk_it_caps_at_50(monkeypatch):
    """資訊及科技界 channel stops after GOVHK_IT_MAX_JOBS drafts."""
    import asyncio
    from pathlib import Path

    from app.config import settings
    from app.services import scraper_govhk
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "GOVHK_IT_MAX_JOBS", 5)
    fixture = (Path(__file__).parent / "fixtures" / "govhk_joblist_it.html").read_text(encoding="utf-8")

    class FakeResp:
        async def text(self):
            return fixture

        async def dispose(self):
            pass

    class FakeRequest:
        async def post(self, *a, **k):
            return FakeResp()

        async def get(self, *a, **k):
            return FakeResp()

    class FakeSession:
        context = type("Ctx", (), {"request": FakeRequest()})()

    async def fake_fetch_detail(session, item, platform):
        return JobDraft(platform=platform, job_id=item["job_id"],
                        title=item["title"], posted_at="01/08/2026")

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_fetch_detail)
    monkeypatch.setattr(scraper_govhk, "human_delay", fake_human_delay)

    drafts = asyncio.run(scraper_govhk._scrape_it(FakeSession(), set()))
    assert len(drafts) == 5               # capped at GOVHK_IT_MAX_JOBS


# ------------------------------------------------------------ OfferToday cap

def test_offertoday_caps_per_search(monkeypatch):
    """Each OfferToday search result contributes at most MAX_PER_SEARCH drafts."""
    import asyncio

    from app.services import scraper_offertoday
    from app.services.scraper_base import JobDraft

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

    # 60 links per search; tokens unique per search URL so the shared seen-set
    # doesn't hide the per-search cap
    def make_links(search_idx):
        return [FakeLink(f"tok{search_idx}_{i}", "AI Developer") for i in range(60)]

    async def fake_open_page(ctx, url):
        idx = scraper_offertoday.SEARCH_URLS.index(url)
        return FakePage(make_links(idx))

    async def fake_scroll(page, target):
        pass

    async def fake_human_delay(*a, **k):
        pass

    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_offertoday, "_scroll_search", fake_scroll)
    monkeypatch.setattr(scraper_offertoday, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_offertoday.scrape(FakeSession()))
    from app.config import settings
    cap = settings.OFFERTODAY_MAX_PER_SEARCH
    assert cap > 0
    assert len(drafts) == cap * len(scraper_offertoday.SEARCH_URLS)
    # all tokens unique across searches
    tokens = [d.job_id for d in drafts]
    assert len(tokens) == len(set(tokens))
