"""Tests for scan pacing: 隨機 4–6 秒 politeness gate.

The gate guarantees at least N seconds between per-job page opens during a
scan, even when the fetch tasks run concurrently (semaphore). With a max
interval the per-call spacing is random uniform [min, max].
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
    """run_scan spaces consecutive JD fetches >= min delay."""
    import time

    from app.config import settings
    from app.models import JobApplication
    from app.services import scanner
    from app.services.scraper_base import JobDraft

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)      # no LLM work
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0.05)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0.05)

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


def test_pace_gate_random_range():
    """max > min -> each sequential release spaced a random amount in [min, max]."""
    import random as _random

    async def main():
        gate = _PaceGate(0.02, 0.05)
        t0 = time.monotonic()
        for _ in range(12):          # sequential callers
            await gate.wait()
        return time.monotonic() - t0

    rng_before = _random.random
    try:
        _random.seed(42)
        elapsed = asyncio.run(main())
    finally:
        _random.random = rng_before
    # 12 gaps, each uniform in [0.02, 0.05] -> total ~12 * 0.035 = 0.42s
    assert 12 * 0.02 <= elapsed <= 12 * 0.05 + 0.05, elapsed


def test_pace_gate_accepts_max_below_min():
    """max < min must clamp up to min, not crash."""
    async def main():
        gate = _PaceGate(0.5, 0.1)
        t0 = time.monotonic()
        for _ in range(3):           # first call is instant; the rest spaced 0.5s
            await gate.wait()
        return time.monotonic() - t0

    elapsed = asyncio.run(main())
    assert 0.9 <= elapsed < 2.0, elapsed
