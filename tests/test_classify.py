"""Tests for the IT / 一般 track classification + per-track scanning."""
import asyncio

import pytest

from app.services.classify import (
    DEFAULT_GENERAL_KEYWORDS,
    DEFAULT_IT_KEYWORDS,
    TrackConfig,
    classify,
    match_keyword,
    parse_keywords,
    resolve_general_keywords,
    resolve_it_keywords,
    title_matches,
)
from app.services.scraper_base import JobDraft


# ---------------------------------------------------------------- keywords

def test_parse_keywords():
    assert parse_keywords(" a, b ,,  c ") == ["a", "b", "c"]
    assert parse_keywords("") == []


def test_match_keyword_cjk_substring_latin_word():
    assert match_keyword("工程師", "見習土木工程師")
    assert match_keyword("developer", "Software Developer")
    assert not match_keyword("developer", "developers")  # latin whole-word
    assert match_keyword("data", "data engineer")
    assert not match_keyword("data", "database")
    assert not match_keyword("data", "database engineer")
    # latin keyword glued to CJK still matches (AI基礎架構)
    assert match_keyword("ai", "AI基礎架構主任")
    assert match_keyword("AI", "人工智能工程師") is False


def test_title_matches():
    assert title_matches("Frontend Developer", ["frontend", "ui"])
    assert not title_matches("行政秘書", ["frontend", "ui"])


def test_classify_it_vs_general():
    assert classify("AI Engineer") == "it"
    assert classify("Software Developer") == "it"
    assert classify("IT Support") == "it"
    assert classify("文員") == "general"
    assert classify("行政助理") == "general"
    assert classify("客戶服務員") == "general"


def test_resolve_keywords_falls_back_to_defaults(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOB_KEYWORDS", "")
    monkeypatch.setattr(settings, "GENERAL_JOB_KEYWORDS", "")
    it_kws = resolve_it_keywords("")
    assert "developer" in it_kws and "ai" in it_kws
    gen_kws = resolve_general_keywords("")
    assert "文員" in gen_kws


def test_resolve_keywords_profile_overrides_and_unions():
    # user keywords come first, defaults still appended (union)
    it_kws = resolve_it_keywords("AI 工程師")
    assert it_kws[0] == "AI 工程師"
    assert "software" in it_kws  # built-in safety net kept
    # general is a whitelist: user list replaces the defaults
    gen_kws = resolve_general_keywords("文員,接待員")
    assert gen_kws == ["文員", "接待員"]


def test_track_config_defaults():
    cfg = TrackConfig.defaults("it")
    assert cfg.name == "it" and cfg.label == "IT"
    assert cfg.keywords and cfg.it_keywords == cfg.keywords
    cfg_g = TrackConfig.defaults("general")
    assert cfg_g.name == "general" and cfg_g.label == "一般"
    assert cfg_g.it_keywords != cfg_g.keywords  # exclusion list is separate


# ---------------------------------------------------------------- per-track scan

def test_run_scan_persists_category_per_track(db, monkeypatch):
    """IT + 一般 tracks both persist, each draft tagged with its category."""
    from app.config import settings
    from app.services import scanner
    from app.models import JobApplication

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)
    monkeypatch.setattr(settings, "IT_TRACK_ENABLED", True)
    monkeypatch.setattr(settings, "GENERAL_TRACK_ENABLED", True)

    it_draft = JobDraft(platform="offertoday", job_id="tokIT",
                        title="AI Developer", posted_at="")
    gen_draft = JobDraft(platform="offertoday", job_id="tokGEN",
                         title="文員", posted_at="")

    async def fake_scrape(session, track="it", cfg=None):
        return [it_draft if track == "it" else gen_draft]

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS", (("offertoday", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="all"))
    assert summary.new_jobs == 2
    assert set(summary.tracks) == {"it", "general"}
    assert summary.tracks["it"]["scanned"] == 1
    assert summary.tracks["general"]["scanned"] == 1

    cats = {r.job_id_on_platform: r.category for r in db.query(JobApplication).all()}
    assert cats == {"tokIT": "it", "tokGEN": "general"}


def test_run_scan_track_general_only(db, monkeypatch):
    """track='general' runs only the general track (ignores enable toggles)."""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "GENERAL_TRACK_ENABLED", False)  # explicit still runs

    calls = []

    async def fake_scrape(session, track="it", cfg=None):
        calls.append(track)
        return [JobDraft(platform="offertoday", job_id="tok1",
                         title="文員", posted_at="")]

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS", (("offertoday", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="general"))
    assert calls == ["general"]
    assert summary.tracks == {"general": {"scanned": 1, "new_jobs": 1,
                                          "skipped_old": 0, "capped": 0}}


def test_run_scan_respects_enable_toggles(db, monkeypatch):
    """Disabled tracks are skipped on a plain (track=None) scan."""
    from app.config import settings
    from app.services import scanner

    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "IT_TRACK_ENABLED", False)

    calls = []

    async def fake_scrape(session, track="it", cfg=None):
        calls.append(track)
        return [JobDraft(platform="offertoday", job_id="tok1",
                         title="AI Developer", posted_at="")]

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS", (("offertoday", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    asyncio.run(scanner.run_scan(db, {}))
    assert calls == ["general"]  # IT disabled -> only the general track ran
