"""用戶要求：「唔係份份工都要評分，只有 IT 工需要」。

預設（ENRICH_GENERAL_JOBS=false）：
  - IT 工：照舊 LLM 完整評分 + 生成 CL + 摘要
  - 一般工：**零 LLM** —— 照樣入庫、有 JD、有關鍵字分數同 AI／合約／外派標籤，
    但唔會叫 LLM 評分、唔生成 CL、唔寫摘要，狀態係 pending_review（照樣顯示）
開啟 ENRICH_GENERAL_JOBS 之後：一般工照舊入 LLM top-N 預算。
"""
from conftest import days_ago
import asyncio

import pytest

from app.config import settings
from app.models import CoverLetter, JobApplication, Profile
from app.services import scanner
from app.services.scraper_base import JobDraft


def _harness(monkeypatch, drafts, score_calls: list):
    async def fake_scrape(session, track="it", cfg=None, channels=None):
        # 真 scraper 會按 track 過濾，呢度照做（唔係同一批 draft 會入兩次）
        if track in ("it", "general"):
            return [d for d in drafts if d.category == track]
        return drafts

    async def fake_fetch_detail(session, d):
        d.jd_text = f"JD for {d.title}"
        d.posted_at = days_ago(1)
        return d

    async def fake_get_browser(platform):
        return object()

    async def fake_score_job(job_dict, skills):
        score_calls.append(job_dict.get("title"))
        return (80, "啱你", "fit")

    async def fake_gen_cl(cv_text, jd, job, lang, instructions=""):
        return ("CL 內容", "")

    async def fake_summary(job_dict):
        return "摘要"

    monkeypatch.setattr(scanner, "get_cv_text", lambda lang, title="": "CV 內容")
    monkeypatch.setattr(scanner, "generate_cl_checked", fake_gen_cl)
    monkeypatch.setattr("app.services.matcher.summarize_job", fake_summary)
    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        (("offertoday", fake_scrape, fake_fetch_detail),))
    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(settings, "MAX_JOB_AGE_DAYS", 60)
    monkeypatch.setattr(settings, "MAX_ENRICH_PER_SCAN", 30)
    monkeypatch.setattr(settings, "ENRICH_ALL_IT", True)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MIN_SECONDS", 0)
    monkeypatch.setattr(settings, "SCAN_JOB_DELAY_MAX_SECONDS", 0)


def _drafts():
    return [
        JobDraft(platform="offertoday", job_id="it1", title="AI Developer",
                 posted_at="", category="it"),
        JobDraft(platform="offertoday", job_id="g1", title="文員",
                 posted_at="", category="general"),
        JobDraft(platform="offertoday", job_id="g2", title="客戶服務助理",
                 posted_at="", category="general"),
    ]


def test_general_jobs_skip_llm_by_default(db, monkeypatch):
    calls: list = []
    _harness(monkeypatch, _drafts(), calls)

    summary = asyncio.run(scanner.run_scan(db, {}, track=None))

    # LLM 只為 IT 工 call 過一次，冇為一般工 call
    assert calls == ["AI Developer"]

    rows = {r.job_id_on_platform: r for r in db.query(JobApplication).all()}
    assert set(rows) == {"it1", "g1", "g2"}

    it = rows["it1"]
    assert it.match_score == 80 and it.match_reason == "啱你" and it.match_level == "fit"
    assert it.status == "pending_review"

    for jid in ("g1", "g2"):
        row = rows[jid]
        assert row.match_reason == scanner.UNSCORED_REASON     # 標示「未評分」
        assert row.match_level == ""
        assert row.status == "pending_review"                  # 照樣顯示喺一般頁
        assert row.jd_text                                     # JD 照有（唔用 LLM）

    # 一般工冇 CL（冇洗 LLM 生成）
    cl_apps = {c.application_id for c in db.query(CoverLetter).all()}
    assert rows["it1"].id in cl_apps
    assert rows["g1"].id not in cl_apps
    assert rows["g2"].id not in cl_apps

    # 統計：一般工未評分唔應該被當成 low_match
    assert summary.low_match == 0


def test_general_jobs_skip_llm_when_profile_default_is_false(db, monkeypatch):
    db.add(Profile(id=1, enrich_general_jobs=False))
    db.commit()
    calls: list = []
    _harness(monkeypatch, _drafts(), calls)
    asyncio.run(scanner.run_scan(db, {}, track=None))
    assert calls == ["AI Developer"]


def test_general_jobs_can_still_be_scored_when_enabled(db, monkeypatch):
    db.add(Profile(id=1, enrich_general_jobs=True))
    db.commit()
    calls: list = []
    _harness(monkeypatch, _drafts(), calls)

    asyncio.run(scanner.run_scan(db, {}, track=None))

    assert sorted(calls) == sorted(["AI Developer", "文員", "客戶服務助理"])
    row = db.query(JobApplication).filter_by(job_id_on_platform="g1").one()
    assert row.match_reason == "啱你"
    assert row.match_level == "fit"


def test_general_jobs_still_scored_on_demand_via_backfill(db, monkeypatch):
    """一般工未評分，但詳情頁「重新整理」／「補齊」照可以即刻評分。"""
    row = JobApplication(platform="offertoday", job_id_on_platform="gs1",
                         title="文員", category="general", status="pending_review",
                         jd_text="負責一般文書", jd_language="zh")
    db.add(row)
    db.commit()

    calls: list = []

    async def fake_score_job(job_dict, skills):
        calls.append(job_dict.get("title"))
        return (66, "啱你", "fit")

    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(settings, "MATCH_THRESHOLD", 90)   # 唔會生成 CL
    asyncio.run(scanner._enrich_one(db, row, "offertoday", None, []))
    assert calls == ["文員"]
    assert row.match_score == 66


def test_score_unscored_helper_includes_unevaluated_general_jobs(db):
    """「補齊未評分 IT 工」只揀 IT；一般工想補就逐份撳。"""
    from app.services.scanner import unscored_it_candidates

    db.add(JobApplication(platform="offertoday", job_id_on_platform="i1",
                          title="Software Engineer", category="it",
                          status="pending_review", match_score=0))
    db.add(JobApplication(platform="offertoday", job_id_on_platform="g9",
                          title="文員", category="general",
                          status="pending_review", match_score=0))
    db.commit()
    picked = {r.job_id_on_platform for r in unscored_it_candidates(db, 10)}
    assert picked == {"i1"}
