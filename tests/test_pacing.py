"""Tests for scan pacing: SCAN_JOB_DELAY_SECONDS politeness gate.

The gate guarantees at least N seconds between per-job page opens during a
scan, even when the fetch tasks run concurrently (semaphore).
"""
import asyncio
import time

from app.services.scanner import _PaceGate


def test_pace_gate_spaces_concurrent_waits():
    """Concurrent wait() callers must be released >= interval apart."""

    async def main():
        gate = _PaceGate(0.05)
        starts = []

        async def one():
            await gate.wait()
            starts.append(time.monotonic())

        await asyncio.gather(*(one() for _ in range(3)))
        return starts

    starts = asyncio.run(main())
    assert len(starts) == 3
    assert starts[1] - starts[0] >= 0.04
    assert starts[2] - starts[1] >= 0.04


def test_pace_gate_zero_interval_no_wait():
    """Interval 0 (tests / manual actions) must not add any delay."""

    async def main():
        gate = _PaceGate(0)
        t0 = time.monotonic()
        await asyncio.gather(*(gate.wait() for _ in range(5)))
        return time.monotonic() - t0

    elapsed = asyncio.run(main())
    assert elapsed < 0.2


def test_run_scan_paces_detail_fetches(db, monkeypatch):
    """run_scan spaces consecutive JD fetches >= SCAN_JOB_DELAY_SECONDS."""
    import time

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)      # no LLM work
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_SECONDS", 0.05)

    drafts = [JobDraft(platform="offertoday", job_id=f"tok{i}",
                       title="AI Developer", posted_at="") for i in range(3)]
    stamps = []

    async def fake_scrape(session, track="it", cfg=None):
        return drafts

    async def fake_fetch_detail(session, d):
        stamps.append(time.monotonic())
        d.jd_text = "職責：開發 AI 系統"
        d.posted_at = "2026-08-01"
        return d

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.details_fetched == 3
    assert len(stamps) == 3
    assert stamps[1] - stamps[0] >= 0.04
    assert stamps[2] - stamps[1] >= 0.04
