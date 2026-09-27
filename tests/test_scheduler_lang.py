"""A：排程容錯 / catch-up；B：jd_language 校正。"""
import json
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------- A. 排程

def test_scheduler_has_misfire_grace(monkeypatch):
    """APScheduler 預設 grace = 1 秒 -> 會令掃描被靜靜跳過；一定要設定大啲。"""
    from app.config import settings

    assert settings.SCAN_MISFIRE_GRACE_SECONDS >= 600
    assert settings.SCAN_CATCHUP_HOURS > 0


def test_built_scheduler_has_grace_and_coalesce(monkeypatch):
    """真正建出嚟嘅 scheduler：job 要帶 misfire_grace_time（唔係預設 1 秒）。"""
    from app.config import settings
    from app.main import build_scheduler

    monkeypatch.setattr(settings, "SCAN_DAY_INTERVAL", 2)
    sched = build_scheduler()
    assert sched is not None
    job = sched.get_job("scan_jobs")
    assert job is not None
    assert job.misfire_grace_time == settings.SCAN_MISFIRE_GRACE_SECONDS
    assert job.misfire_grace_time >= 600          # 唔再係 1 秒
    assert job.coalesce is True
    assert job.max_instances == 1
    assert sched._job_defaults["misfire_grace_time"] == settings.SCAN_MISFIRE_GRACE_SECONDS


def test_scheduler_disabled_when_interval_zero(monkeypatch):
    from app.config import settings
    from app.main import build_scheduler

    monkeypatch.setattr(settings, "SCAN_DAY_INTERVAL", 0)
    assert build_scheduler() is None


def test_last_scan_age_and_record(tmp_path, monkeypatch):
    from app.routers import scan as scan_router

    f = tmp_path / "last_scan.json"
    monkeypatch.setattr(scan_router, "_LAST_SCAN_FILE", f)

    assert scan_router.last_scan_age_hours() is None      # 未見過
    scan_router._record_scan_time()
    age = scan_router.last_scan_age_hours()
    assert age is not None and age < 0.1

    # 40 小時前 -> 應該觸發 catch-up
    f.write_text(json.dumps({"at": (datetime.now(timezone.utc)
                                    - timedelta(hours=40)).isoformat()}), encoding="utf-8")
    assert scan_router.last_scan_age_hours() > 36


def test_catchup_skips_when_already_running(monkeypatch):
    from app.routers import scan as scan_router

    monkeypatch.setitem(scan_router._state, "running", True)
    assert scan_router.start_catchup_scan() is False


# ------------------------------------------------------- B. jd_language

def test_persist_uses_title_language_not_hardcoded_en(db, monkeypatch):
    """以前入庫硬編 "en" -> 中文工會交英文 CV。"""
    from app.models import JobApplication
    from app.services.scanner import make_dup_key
    from app.services.store import persist_drafts
    from app.services.scraper_base import JobDraft

    drafts = [
        JobDraft(platform="offertoday", job_id="zhJob", title="文員", category="general"),
        JobDraft(platform="offertoday", job_id="enJob", title="Clerk", category="general"),
    ]
    persist_drafts(db, drafts)
    db.commit()

    rows = {r.job_id_on_platform: r for r in db.query(JobApplication).all()}
    assert rows["zhJob"].jd_language == "zh"
    assert rows["enJob"].jd_language == "en"
    assert make_dup_key  # noqa: B018  (import 用)


def test_sync_jd_language_fixes_wrong_label(db):
    from app.models import JobApplication
    from app.routers.jobs import _sync_jd_language

    row = JobApplication(platform="offertoday", job_id_on_platform="zhWrong",
                         title="行政助理", category="general",
                         jd_text="職責：處理日常行政工作，需要良好中文書寫能力。",
                         jd_language="en", status="pending_review")
    db.add(row)
    db.commit()

    _sync_jd_language(db, row)
    assert row.jd_language == "zh"


def test_backfill_fixes_legacy_rows(db):
    from app.db import _backfill_jd_language
    from app.models import JobApplication

    row = JobApplication(platform="offertoday", job_id_on_platform="legacyZh",
                         title="客戶服務員", category="general",
                         jd_text="工作內容：接聽電話，處理客戶查詢。", jd_language="en",
                         status="applied")
    db.add(row)
    db.commit()

    _backfill_jd_language()
    db.refresh(row)
    assert row.jd_language == "zh"
    assert row.status == "applied"        # 唔可以改任何 status
