"""暫停（⏸）：撳咗就要即刻停 —— 連 JD 階段同 LLM enrich 階段都要停得切。

用戶實測痛點：撳咗「⏸ 暫停」之後，掃描仲繼續開 JD 頁（158 份，每份隔 4–6 秒），
跟住仲會照跑 LLM 評分／生成 CL。以下測試鎖住呢個行為。
"""
from conftest import days_ago
import asyncio

import pytest

from app.config import settings
from app.models import CoverLetter, JobApplication
from app.services import scan_control, scanner
from app.services.scraper_base import JobDraft


def _harness(monkeypatch, drafts, fetched: list, scored: list, stop_after_fetch: int | None = None):
    async def fake_scrape(session, track="it", cfg=None, channels=None):
        if track in ("it", "general"):
            return [d for d in drafts if d.category == track]
        return drafts

    async def fake_fetch_detail(session, d):
        fetched.append(d.job_id)
        d.jd_text = f"JD {d.job_id}"
        d.posted_at = days_ago(1)
        if stop_after_fetch is not None and len(fetched) >= stop_after_fetch:
            scan_control.request_stop()      # 模擬用戶中途撳「⏸ 暫停」
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        scored.append(job_dict.get("title"))
        return (80, "啱你", "fit")

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0)
    scan_control.clear_stop()


def _drafts(n=12):
    return [JobDraft(platform="offertoday", job_id=f"it{i}", title=f"Software Engineer {i}",
                     posted_at="", category="it") for i in range(n)]


def test_pause_during_jd_phase_stops_fetching_and_skips_llm(db, monkeypatch):
    fetched: list = []
    scored: list = []
    _harness(monkeypatch, _drafts(12), fetched, scored, stop_after_fetch=3)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))

    assert summary.stopped is True
    assert len(fetched) < 12          # 冇為咗被暫停嘅工繼續開頁
    assert scored == []               # 完全冇洗 LLM
    # 已經攞到嘅 JD 要落實（唔會因為暫停而白做）
    with_jd = [r for r in db.query(JobApplication).all() if r.jd_text]
    assert with_jd, "已攞到嘅 JD 應該 commit 咗"
    assert db.query(CoverLetter).count() == 0


def test_pause_stops_enrich_phase(db, monkeypatch):
    """JD 階段行完先撳暫停 -> 唔會再入 LLM 評分。"""
    fetched: list = []
    scored: list = []
    _harness(monkeypatch, _drafts(4), fetched, scored)

    # 令 JD 全部攞完之後先 request stop：喺 score_job 之前就停
    orig_fill = scanner._fill_detail

    async def fill_then_stop(db_, row, fetch_detail, pace=None, prune=True):
        res = await orig_fill(db_, row, fetch_detail, pace=pace, prune=prune)
        if row.job_id_on_platform == "it3":
            scan_control.request_stop()
        return res

    monkeypatch.setattr(scanner, "_fill_detail", fill_then_stop)
    asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert scored == []
    scan_control.clear_stop()


def test_no_stop_means_normal_enrich(db, monkeypatch):
    """冇撳暫停 -> 一切照常（評分照做）。"""
    fetched: list = []
    scored: list = []
    _harness(monkeypatch, _drafts(3), fetched, scored)
    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.stopped is False
    assert sorted(scored) == ["Software Engineer 0", "Software Engineer 1", "Software Engineer 2"]
