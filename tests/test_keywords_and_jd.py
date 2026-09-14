"""Check 1（關鍵字）：自定 + 精準度；Check 2：冇 JD 嘅卡可以即刻去網站攞詳情。"""
import asyncio

import pytest

from app.services.classify import (
    DEFAULT_NON_IT_KEYWORDS,
    classify,
    parse_keywords,
    resolve_non_it_keywords,
    title_matches,
)


# --------------------------------------------------- 排除字眼（唔當 IT）

@pytest.mark.parametrize("title", [
    "MECHANICAL ENGINEER",
    "Civil Engineer (Graduate)",
    "Project & Production Engineer - Electronic",
    "桌面排版操作員",
    "土木工程師",
    "結構工程師",
    "外勤技術員**",
    "Quantity Surveyor",
])
def test_broad_titles_are_not_it(title):
    assert classify(title) == "general"


@pytest.mark.parametrize("title", [
    "AI Engineer",
    "Software Engineer",
    "電腦技術員**",
    "數據中心技術員",
    "資訊保安工程師(EA)",
    "IT Support Specialist",
    "Network Engineer",
    "全端網頁開發員(EA)",
])
def test_real_it_titles_stay_it(title):
    assert classify(title) == "it"


def test_strong_it_wins_over_exclusion():
    """同時有排除字眼（保安）同強 IT 字眼（資訊）-> 照當 IT。"""
    assert classify("資訊保安工程師") == "it"
    assert classify("保安員") == "general"


def test_non_it_keywords_customisable(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "NON_IT_KEYWORDS", "太陽能")
    assert resolve_non_it_keywords("") == ["太陽能"]
    assert resolve_non_it_keywords("風力, 水力") == ["風力", "水力"]
    monkeypatch.setattr(settings, "NON_IT_KEYWORDS", "")
    assert resolve_non_it_keywords("") == DEFAULT_NON_IT_KEYWORDS


def test_exclusion_only_applies_when_it_rule_matches_first():
    """唔關 IT 事嘅字眼（例如 銷售）唔會令非 IT 標題出現奇怪行為。"""
    assert classify("銷售工程師") == "general"
    assert classify("市場助理") == "general"


def test_parse_keywords_trims():
    assert parse_keywords(" a , b ,, c ") == ["a", "b", "c"]


# ------------------------------------------- 冇 JD -> 即刻攞詳情（API）

def test_fetch_detail_endpoint_offertoday(client, db, monkeypatch):
    """撳入冇 JD 嘅 OfferToday 卡 -> 即刻去網站攞 JD 並寫入 DB。"""
    from app.models import JobApplication
    from app.routers import jobs as jobs_router
    from app.services.scraper_base import JobDraft

    row = JobApplication(platform="offertoday", job_id_on_platform="tokFetch",
                         title="AI Developer", url="https://example.test/job/1",
                         category="it", status="pending_review")
    db.add(row)
    db.commit()

    async def fake_fetch_detail(session, draft):
        draft.jd_text = "職責：開發 AI 系統\n要求：Python"
        draft.posted_at = "2026-01-02"
        draft.company = "Example Ltd"
        draft.salary_range = "HK$30K"
        return draft

    async def fake_get_browser(platform):
        return object()

    monkeypatch.setattr(jobs_router.scraper_offertoday, "fetch_detail", fake_fetch_detail)
    monkeypatch.setattr("app.services.scraper_base.get_browser", fake_get_browser)

    r = client.post(f"/api/jobs/{row.id}/fetch-detail")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["updated"] is True
    assert body["job"]["jd_text"].startswith("職責")
    assert body["job"]["company"] == "Example Ltd"
    assert body["job"]["posted_at"] == "2026-01-02"
    assert "JD" in body["message"]


def test_fetch_detail_endpoint_govhk_is_noop(client, db):
    from app.models import JobApplication

    row = JobApplication(platform="govhk_it", job_id_on_platform="31-26-0009999",
                         title="資訊科技技術員", category="it", status="pending_review")
    db.add(row)
    db.commit()

    r = client.post(f"/api/jobs/{row.id}/fetch-detail")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["updated"] is False
    assert "掃描時已經入庫" in body["message"]


def test_fetch_detail_endpoint_404(client):
    assert client.post("/api/jobs/999999/fetch-detail").status_code == 404


def test_backfill_jd_endpoint_starts(client, monkeypatch, db):
    """📄 補 JD 掣：背景幫冇 JD 嘅工補詳情（唔用 LLM）。"""
    from app.models import JobApplication
    from app.routers import scan as scan_router

    row = JobApplication(platform="offertoday", job_id_on_platform="tokNoJd",
                         title="Web Developer", category="it", status="pending_review")
    db.add(row)
    db.commit()

    calls = []

    async def fake_fill(db_, row_, fetch_detail, pace=None, prune=True):
        calls.append(row_.id)
        row_.jd_text = "補咗嘅 JD"
        db_.flush()
        return False

    monkeypatch.setattr(scan_router, "SessionLocal", lambda: _SessionShim(db))
    monkeypatch.setattr("app.services.scanner._fill_detail", fake_fill)
    scan_router._state["running"] = False

    r = client.post("/api/scan/backfill-jd", json={"limit": 5})
    assert r.status_code == 200
    assert r.json()["started"] is True

    import time
    for _ in range(100):
        if not client.get("/api/scan/status").json()["running"]:
            break
        time.sleep(0.05)
    assert calls  # 至少處理咗一份
    assert client.get("/api/scan/status").json()["last_jd_backfill"]["processed"] >= 1


class _SessionShim:
    """Wrap the test session so the router's db.close() doesn't kill it."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


# ------------------------------------------- 補 JD 唔可以刪走申請歷史

def _stale_draft(job_id: str, posted: str):
    from app.services.scraper_base import JobDraft

    return JobDraft(platform="offertoday", job_id=job_id, title="Web Developer",
                    jd_text="遠古嘅 JD", posted_at=posted)


async def _run_fill(db, row, monkeypatch, posted, **kwargs):
    from app.services import scanner

    async def fake_get_browser(platform):
        return object()

    async def fake_fetch_detail(session, draft):
        return _stale_draft(row.job_id_on_platform, posted)

    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    return await scanner._fill_detail(db, row, fake_fetch_detail, **kwargs)


def test_fill_detail_deletes_stale_pending_row(db, monkeypatch):
    """掃描模式（prune=True）：過期嘅新記錄照樣刪走。"""
    from app.models import JobApplication
    from app.services.scanner import _fill_detail

    row = JobApplication(platform="offertoday", job_id_on_platform="tokStaleA",
                         title="Web Developer", category="it", status="pending_review")
    db.add(row)
    db.commit()
    rid = row.id

    deleted = asyncio.run(_run_fill(db, row, monkeypatch, "2020-01-01"))
    db.commit()
    assert deleted is True, _fill_detail
    assert db.query(JobApplication).filter(JobApplication.id == rid).first() is None


def test_fill_detail_never_deletes_applied_row(db, monkeypatch):
    """已申請嘅工就算過期都要留住（申請歷史）。"""
    from app.models import JobApplication

    row = JobApplication(platform="offertoday", job_id_on_platform="tokStaleB",
                         title="Web Developer", category="it", status="applied")
    db.add(row)
    db.commit()
    rid = row.id

    deleted = asyncio.run(_run_fill(db, row, monkeypatch, "2020-01-01"))
    db.commit()
    kept = db.query(JobApplication).filter(JobApplication.id == rid).first()
    assert deleted is False
    assert kept is not None and kept.status == "applied"


def test_backfill_keeps_stale_row_but_marks_expired(db, monkeypatch):
    """補 JD 模式（prune=False）：唔刪，只標記過期。"""
    from app.models import JobApplication

    row = JobApplication(platform="offertoday", job_id_on_platform="tokStaleC",
                         title="Web Developer", category="it", status="pending_review")
    db.add(row)
    db.commit()
    rid = row.id

    deleted = asyncio.run(_run_fill(db, row, monkeypatch, "2020-01-01", prune=False))
    db.commit()
    kept = db.query(JobApplication).filter(JobApplication.id == rid).first()
    assert deleted is False
    assert kept is not None
    assert kept.status == "low_match"
    assert "過期" in (kept.match_reason or "")
    assert kept.posted_date is not None
