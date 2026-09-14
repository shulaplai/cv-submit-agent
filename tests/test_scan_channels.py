"""Scan channel selection: 除咗 IT／一般 track，仲可以逐個渠道揀。

渠道 key：offertoday / govhk_it / govhk_general / govhk_gbayes（+ jobsdb）。
空集合 = 全部渠道（同以往一樣）。
"""
import asyncio
import time

import pytest

from app.services import scanner
from app.services.scraper_base import JobDraft


def _fake_scraper(calls, platform):
    async def _scrape(session, track="it", cfg=None, **kwargs):
        calls.append((platform, track, kwargs.get("channels")))
        return []
    return _scrape


def _run(db, monkeypatch, scrapers, **kwargs):
    """run_scan with stub scrapers (no network) + stubbed browser."""
    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS", tuple(scrapers))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner.settings, "MAX_ENRICH_PER_SCAN", 0)
    return asyncio.run(scanner.run_scan(db, {}, **kwargs))


def test_channels_offertoday_only(db, monkeypatch):
    calls = []
    scrapers = (
        ("govhk", _fake_scraper(calls, "govhk"), None),
        ("offertoday", _fake_scraper(calls, "offertoday"), None),
    )
    _run(db, monkeypatch, scrapers, track="it", channels=["offertoday"])
    assert [c[0] for c in calls] == ["offertoday"]


def test_channels_govhk_gbayes_only(db, monkeypatch):
    calls = []
    scrapers = (
        ("govhk", _fake_scraper(calls, "govhk"), None),
        ("offertoday", _fake_scraper(calls, "offertoday"), None),
    )
    _run(db, monkeypatch, scrapers, track="it", channels=["govhk_gbayes"])
    assert [c[0] for c in calls] == ["govhk"]
    assert calls[0][2] == ["govhk_gbayes"]          # 只要求大灣區


def test_channels_general_track_skips_gbayes(db, monkeypatch):
    """大灣區屬 IT 軌：淨揀大灣區 + 一般 track -> 唔會掃任何嘢。"""
    calls = []
    scrapers = (
        ("govhk", _fake_scraper(calls, "govhk"), None),
        ("offertoday", _fake_scraper(calls, "offertoday"), None),
    )
    _run(db, monkeypatch, scrapers, track="general", channels=["govhk_gbayes"])
    assert calls == []


def test_channels_none_means_everything(db, monkeypatch):
    calls = []
    scrapers = (
        ("govhk", _fake_scraper(calls, "govhk"), None),
        ("offertoday", _fake_scraper(calls, "offertoday"), None),
    )
    _run(db, monkeypatch, scrapers, track="it")
    assert sorted(c[0] for c in calls) == ["govhk", "offertoday"]
    assert calls[0][2] == ["govhk_gbayes", "govhk_it"]   # 兩個 gov.hk 子渠道


def test_channels_both_tracks(db, monkeypatch):
    """track=all + 只揀政府一般 -> 只有一般軌會行，而且只行 govhk_general。"""
    calls = []
    scrapers = (
        ("govhk", _fake_scraper(calls, "govhk"), None),
        ("offertoday", _fake_scraper(calls, "offertoday"), None),
    )
    _run(db, monkeypatch, scrapers, track="all", channels=["govhk_general"])
    assert [(c[0], c[1], c[2]) for c in calls] == [("govhk", "general", ["govhk_general"])]


# ----------------------------------------------------------- gov.hk scraper

def test_govhk_scrape_honours_channels(monkeypatch):
    from app.services import scraper_govhk

    ran = []

    async def fake_general(session, seen, cfg):
        ran.append("general")
        return []

    async def fake_gbayes(session, seen, cfg):
        ran.append("gbayes")
        return []

    async def fake_it(session, seen, cfg):
        ran.append("it")
        return []

    monkeypatch.setattr(scraper_govhk, "_scrape_general", fake_general)
    monkeypatch.setattr(scraper_govhk, "_scrape_gbayes", fake_gbayes)
    monkeypatch.setattr(scraper_govhk, "_scrape_it", fake_it)

    class FakeSession:
        context = object()

    asyncio.run(scraper_govhk.scrape(FakeSession(), "it", None, channels=["govhk_it"]))
    assert ran == ["it"]

    ran.clear()
    asyncio.run(scraper_govhk.scrape(FakeSession(), "it", None, channels=["govhk_gbayes"]))
    assert ran == ["gbayes"]

    ran.clear()
    asyncio.run(scraper_govhk.scrape(FakeSession(), "it", None))   # default = 兩個
    assert ran == ["gbayes", "it"]


# -------------------------------------------------------------------- API

def _stub_run_scan(monkeypatch, seen):
    async def fake_run_scan(db, progress, track=None, channels=None):
        seen.append({"track": track, "channels": channels})
        return scanner.ScanSummary(scanned=0, new_jobs=0)

    monkeypatch.setattr("app.routers.scan.run_scan", fake_run_scan)


def _wait_idle(client, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get("/api/scan/status").json()["running"]:
            return True
        time.sleep(0.05)
    return False


def test_api_scan_accepts_channels(client, monkeypatch):
    seen = []
    _stub_run_scan(monkeypatch, seen)
    from app.routers import scan as scan_router
    scan_router._state["running"] = False

    r = client.post("/api/scan", json={"track": "it", "channels": ["offertoday", "govhk_gbayes"]})
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is True
    assert "OfferToday" in body["message"] and "大灣區計劃" in body["message"]

    assert _wait_idle(client)
    assert seen == [{"track": "it", "channels": ["offertoday", "govhk_gbayes"]}]

    status = client.get("/api/scan/status").json()
    assert status["channels"] == ["offertoday", "govhk_gbayes"]
    assert status["last"]["channels"] == ["offertoday", "govhk_gbayes"]


def test_api_scan_empty_channels_means_all(client, monkeypatch):
    seen = []
    _stub_run_scan(monkeypatch, seen)
    from app.routers import scan as scan_router
    scan_router._state["running"] = False

    r = client.post("/api/scan", json={"track": "all"})
    assert r.status_code == 200
    assert "全部渠道" in r.json()["message"]
    assert _wait_idle(client)
    assert seen == [{"track": "all", "channels": None}]


def test_api_scan_rejects_unknown_channel(client, monkeypatch):
    _stub_run_scan(monkeypatch, [])
    from app.routers import scan as scan_router
    scan_router._state["running"] = False

    r = client.post("/api/scan", json={"track": "it", "channels": ["linkedin"]})
    assert r.status_code == 400
    assert "linkedin" in r.json()["detail"]


def test_api_scan_rejects_non_list_channels(client, monkeypatch):
    _stub_run_scan(monkeypatch, [])
    from app.routers import scan as scan_router
    scan_router._state["running"] = False

    r = client.post("/api/scan", json={"track": "it", "channels": "offertoday"})
    assert r.status_code == 400


# ------------------------------- 新工 JD：失敗自動重試；舊工永不自動補

def test_old_jobs_never_backfilled_by_default():
    """用戶決定：舊工唔自動補 JD（想 in 邊份就自己撳「更新 JD」）。"""
    from app.config import settings

    assert settings.DETAIL_BACKFILL_PER_SCAN == 0


def test_new_job_jd_retries_once_on_transient_failure(db, monkeypatch):
    """新工攞 JD 撞到 CDP 一閃 -> 自動重試一次，最終有 JD。"""
    calls = []

    async def fake_scrape(session, track="it", cfg=None, **kwargs):
        return [JobDraft(platform="offertoday", job_id="tokRetry", title="AI Engineer",
                         url="https://www.offertoday.com/hk/job/tokRetry", category="it")]

    async def fake_fill(db_, row, fetch_detail, pace=None, prune=True):
        calls.append(row.id)
        if len(calls) == 1:
            raise RuntimeError("BrowserContext.new_page: Target page closed")
        row.jd_text = "重試之後攞到嘅 JD"
        db_.flush()
        return False

    monkeypatch.setattr(scanner, "_fill_detail", fake_fill)
    # 關閉 enrich phase，聚焦睇 phase A（新工攞 JD）嘅重試行為
    monkeypatch.setattr(scanner.settings, "ENRICH_ALL_IT", False)
    scrapers = (("offertoday", fake_scrape, None),)
    _run(db, monkeypatch, scrapers, track="it", channels=["offertoday"])

    from app.models import JobApplication

    row = (db.query(JobApplication)
           .filter(JobApplication.job_id_on_platform == "tokRetry").first())
    assert row is not None
    assert len(calls) == 2                    # 第一次失敗 -> 重試一次
    assert row.jd_text == "重試之後攞到嘅 JD"


def test_new_job_jd_retry_is_capped(db, monkeypatch):
    """連續失敗 -> 最多試 2 次就放棄（唔會無限重試）。"""
    calls = []

    async def fake_scrape(session, track="it", cfg=None, **kwargs):
        return [JobDraft(platform="offertoday", job_id="tokFail", title="AI Engineer",
                         url="https://www.offertoday.com/hk/job/tokFail", category="it")]

    async def fake_fill(db_, row, fetch_detail, pace=None, prune=True):
        calls.append(row.id)
        raise RuntimeError("boom")

    monkeypatch.setattr(scanner, "_fill_detail", fake_fill)
    monkeypatch.setattr(scanner.settings, "ENRICH_ALL_IT", False)
    scrapers = (("offertoday", fake_scrape, None),)
    summary = _run(db, monkeypatch, scrapers, track="it", channels=["offertoday"])

    assert len(calls) == 2                    # 唔會多過 2 次
    assert any("boom" in e for e in summary.errors)
