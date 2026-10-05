"""政府工（gov.hk）投遞唔應該再出「唔支援嘅平台: govhk_gbayes」。

根因：`store._apply_method_for()` 見到 draft 冇 contact_email 就標
`apply_method="form"`；舊資料有 330 份政府工冇 email，於是 apply flow 跌入
「唔支援嘅平台」。修法：
  1. 政府工一律行 email flow（申請方法本就係 email，email 寫喺詳情頁申請須知）
  2. 冇 email 就即場去原頁搵返，寫入 DB
  3. 真係搵唔到 -> 友善提示 + 原頁 link（唔係「唔支援嘅平台」）
  4. 「🔄 更新 JD」對政府工都要 work（以前回「政府工冇獨立詳情頁要補」）
"""
import asyncio

import pytest

from app.models import JobApplication
from app.services import apply_bot, scraper_govhk, scanner
from app.services.scraper_base import JobDraft


def _row(db, platform="govhk_gbayes", jid="g1", **kw):
    defaults = dict(title="數碼項目工程師", company="測試公司", category="it",
                    jd_language="zh", status="pending_review",
                    jd_text="職責：開發系統", apply_method="form",
                    contact_email="", url="https://www2.jobs.gov.hk/0/tc/jobCard/?order=ABC")
    defaults.update(kw)
    row = JobApplication(platform=platform, job_id_on_platform=jid, **defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------- dispatch

def test_govhk_form_row_uses_email_flow_not_unsupported(db, monkeypatch):
    """apply_method='form' 嘅政府工都要行 email flow（唔可以再話唔支援）。"""
    from app.services import email_bot

    row = _row(db, contact_email="hr@example.com")
    called = {}

    async def fake_compose(r, cl_text, send=False, template_key="standard"):
        called["email"] = r.contact_email
        return {"ok": True, "kind": "email", "submitted": False, "message": "opened"}

    monkeypatch.setattr(email_bot, "open_email_compose", fake_compose)
    res = asyncio.run(apply_bot.open_apply(row, "CL", auto=False))
    assert "唔支援" not in res["message"]
    assert called["email"] == "hr@example.com"


def test_govhk_without_email_repairs_from_detail_page(db, monkeypatch):
    """冇 contact_email -> 即場去詳情頁搵返，寫入 DB，再行 email flow。"""
    from app.services import email_bot

    row = _row(db, jid="g2")

    async def fake_get_browser(platform):
        return object()

    async def fake_fetch_detail(session, draft):
        draft.contact_email = "found@example.com"
        draft.contact_person = "陳先生"
        draft.jd_text = "JD from detail"
        return draft

    monkeypatch.setattr("app.services.scraper_base.get_browser", fake_get_browser)
    monkeypatch.setattr(scraper_govhk, "fetch_detail", fake_fetch_detail)

    called = {}

    async def fake_compose(r, cl_text, send=False, template_key="standard"):
        called["email"] = r.contact_email
        return {"ok": True, "kind": "email", "submitted": False, "message": "opened"}

    monkeypatch.setattr(email_bot, "open_email_compose", fake_compose)

    res = asyncio.run(apply_bot.open_apply(row, "CL", auto=False))
    assert called["email"] == "found@example.com"
    assert "唔支援" not in res["message"]

    db.expire_all()
    fresh = db.get(JobApplication, row.id)
    assert fresh.contact_email == "found@example.com"
    assert fresh.apply_method == "email"          # 由 form 修返做 email
    assert fresh.contact_person == "陳先生"


def test_govhk_still_without_email_gives_helpful_message(db, monkeypatch):
    """真係搵唔到 email -> 友善提示 + 原頁 link，而唔係「唔支援嘅平台」。"""
    row = _row(db, jid="g3")

    async def fake_get_browser(platform):
        return object()

    async def fake_fetch_detail(session, draft):
        return draft          # 詳情頁都冇 email

    monkeypatch.setattr("app.services.scraper_base.get_browser", fake_get_browser)
    monkeypatch.setattr(scraper_govhk, "fetch_detail", fake_fetch_detail)

    res = asyncio.run(apply_bot.open_apply(row, "CL", auto=False))
    assert res["kind"] == "needs_manual"
    assert "唔支援" not in res["message"]
    assert "申請須知" in res["message"]
    assert res["url"].startswith("https://www2.jobs.gov.hk")


def test_unknown_platform_still_reports_but_gives_url(db):
    row = _row(db, platform="weirdboard", jid="w1", apply_method="form")
    res = asyncio.run(apply_bot.open_apply(row, "CL", auto=False))
    assert "唔支援嘅平台" in res["message"]
    assert res["url"]                      # 至少俾返條 link


# --------------------------------------------------------------- detail fetch

def test_scanner_maps_govhk_subplatforms_to_govhk_fetcher():
    assert scanner._fetch_detail_for("govhk_it") is scraper_govhk.fetch_detail
    assert scanner._fetch_detail_for("govhk_gbayes") is scraper_govhk.fetch_detail
    assert scanner._fetch_detail_for("govhk_general") is scraper_govhk.fetch_detail
    assert scanner.can_fetch_detail("govhk_it") is True
    assert scanner.can_fetch_detail("nonsense") is False


def test_govhk_fetch_detail_wrapper_copies_fields(monkeypatch):
    """wrapper 要將詳情頁搵到嘅 email／聯絡人／JD 抄返落 draft。"""
    draft = JobDraft(platform="govhk_it", job_id="11-26-0000001",
                     title="資訊科技技術員", url="https://x/jobCard",
                     category="it", apply_method="form")

    async def fake_inner(session, item, platform, category=""):
        return JobDraft(platform=platform, job_id=item["job_id"], title=item["title"],
                        contact_email="hr@x.com", contact_person="李小姐",
                        jd_text="完整 JD", apply_method="email")

    monkeypatch.setattr(scraper_govhk, "_fetch_detail", fake_inner)
    out = asyncio.run(scraper_govhk.fetch_detail(object(), draft))
    assert out.contact_email == "hr@x.com"
    assert out.contact_person == "李小姐"
    assert out.jd_text == "完整 JD"
    assert out.apply_method == "email"


def test_fetch_detail_endpoint_uses_govhk_scraper(client, db, monkeypatch):
    """詳情頁「🔄 更新 JD」對政府工要真係去揭頁（以前回「政府工冇詳情頁要補」）。"""
    from app.routers import jobs as jobs_router

    row = _row(db, platform="govhk_it", jid="gd1", contact_email="")
    calls = {}

    async def fake_browser(platform):
        return object()

    async def fake_fetch_detail(session, draft):
        calls["called"] = True
        draft.contact_email = "repaired@example.com"
        draft.jd_text = "JD refetched"
        return draft

    monkeypatch.setattr(jobs_router, "_detail_fetcher", lambda p: fake_fetch_detail)
    monkeypatch.setattr(jobs_router, "get_browser", fake_browser, raising=False)

    r = client.post(f"/api/jobs/{row.id}/fetch-detail")
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls.get("called") is True
    assert body["ok"] is True
    assert "email" in body["message"] or "聯絡" in body["message"] or body["updated"]
    db.expire_all()
    assert db.get(JobApplication, row.id).contact_email == "repaired@example.com"
