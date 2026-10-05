"""掃描量設定（Settings 頁）→ 有效 cap：渠道上限、track 總上限、每個搜尋頁幾多份。

用戶要求：唔止 `.env`，設定頁都可以加大／唔設限；數值要即刻生效。
"""
from conftest import days_ago, days_ago_dmy
import asyncio

from app.config import settings
from app.models import Profile
from app.services import scanner
from app.services.classify import TrackConfig
from app.services.scraper_base import JobDraft
from app.services.tuning import load_tuning


# ---------------------------------------------------------------- helper 數學

def test_channel_soft_budget_math():
    cfg = TrackConfig(name="it", label="IT", keywords=[], it_keywords=[],
                      govhk_max_jobs=120, offertoday_max_per_search=40, max_searches=10)
    # gov.hk 資訊及科技界 = 單一上限
    assert scanner._channel_soft_budget("govhk_it", cfg) == 120
    # 大灣區一向冇上限
    assert scanner._channel_soft_budget("govhk_gbayes", cfg) == 0
    # OfferToday IT = 每頁 40 x (3 個分類頁 + 10 個字詞搜尋)
    assert scanner._channel_soft_budget("offertoday", cfg) == 40 * 13
    # 每頁 0 = 唔設限
    cfg2 = TrackConfig(name="general", label="一般", keywords=[], it_keywords=[],
                       govhk_max_jobs=80, offertoday_max_per_search=0, max_searches=4)
    assert scanner._channel_soft_budget("offertoday", cfg2) == 0
    assert scanner._channel_soft_budget("govhk_general", cfg2) == 80


# ---------------------------------------------------------------- profile → cfg

def test_profile_caps_flow_into_track_config(db):
    db.add(Profile(id=1, govhk_it_max_jobs=200, govhk_general_max_jobs=130,
                   offertoday_it_max_per_search=175, offertoday_general_max_per_search=90,
                   offertoday_it_max_searches=12, offertoday_general_max_searches=15,
                   max_scan_jobs=500, priority_extra_max=70, cap_bypass_min_score=65,
                   cap_bypass_enabled=True, priority_keywords="react, laravel"))
    db.commit()

    from app.services.scanner import load_track_configs

    it_cfg = load_track_configs(db, "it")[0]
    gen_cfg = load_track_configs(db, "general")[0]
    assert it_cfg.govhk_max_jobs == 200
    assert it_cfg.offertoday_max_per_search == 175
    assert it_cfg.max_searches == 12
    assert gen_cfg.govhk_max_jobs == 130
    assert gen_cfg.offertoday_max_per_search == 90
    assert gen_cfg.max_searches == 15
    for cfg in (it_cfg, gen_cfg):
        assert cfg.priority_extra_max == 70
        assert cfg.cap_bypass_min_score == 65
        assert "react" in cfg.priority_keywords          # 用戶自訂優先字詞
        assert "ai" in cfg.priority_keywords             # AI 字眼永遠有效


def test_tuning_falls_back_to_env_when_profile_zero(db):
    db.add(Profile(id=1))          # 全 0 / -1 = 用 .env
    db.commit()
    t = load_tuning(db)
    assert t.max_scan_jobs == settings.MAX_SCAN_JOBS
    assert t.cap_bypass_min_score == settings.CAP_BYPASS_MIN_SCORE
    assert t.priority_extra_max == settings.PRIORITY_EXTRA_MAX
    assert t.max_enrich_per_scan == settings.MAX_ENRICH_PER_SCAN
    assert t.offertoday_it_max_searches == settings.OFFERTODAY_IT_MAX_SEARCHES


def test_tuning_zero_means_unlimited_for_enrich_ceiling(db):
    """max_enrich_it_per_scan = 0 = 唔設上限（用戶要求唔限）。"""
    db.add(Profile(id=1, max_enrich_it_per_scan=0))
    db.commit()
    assert load_tuning(db).max_enrich_it_per_scan == 0


# ---------------------------------------------------------------- summary 統計

def test_summary_reports_bypass_extra_from_channel(db, monkeypatch):
    """渠道實收份數 > 軟上限 -> 側邊欄可以顯示「豁免收多 N 份」。"""
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "CAP_BYPASS_ENABLED", True)

    drafts = [JobDraft(platform="govhk_general", job_id=f"11-26-{i:07d}",
                       title="文員", posted_at=days_ago_dmy(1), category="general")
              for i in range(10)]

    async def fake_scrape(session, track="general", cfg=None, channels=None):
        return drafts

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("govhk", fake_scrape, None),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    # 軟上限 4 -> 10 份入面 6 份係「豁免收多」
    db.add(Profile(id=1, govhk_general_max_jobs=4, general_track_enabled=True,
                   it_track_enabled=False, max_scan_jobs=0))
    db.commit()

    summary = asyncio.run(scanner.run_scan(db, {}, track="general"))
    assert summary.new_jobs == 10
    assert summary.priority_kept == 6


def test_max_scan_jobs_zero_keeps_everything(db, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(settings, "MAX_SCAN_JOBS", 0)

    drafts = [JobDraft(platform="offertoday", job_id=f"tok{i}", title="Web Developer",
                       posted_at="", category="it") for i in range(25)]

    async def fake_scrape(session, track="it", cfg=None, channels=None):
        return drafts

    async def fake_fetch_detail(session, d):
        d.jd_text = "JD"
        d.posted_at = days_ago(1)
        return d

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)

    summary = asyncio.run(scanner.run_scan(db, {}, track="it"))
    assert summary.new_jobs == 25
    assert summary.capped == 0
