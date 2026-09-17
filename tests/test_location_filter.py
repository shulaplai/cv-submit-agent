"""一般工「想去嘅地點」白名單：只收寫得明確又喺名單內嘅工。

用戶要求：
- 唔係排除地點，而係「想去嘅地點」；機場／赤鱲角自然唔會出現（唔填佢落名單）。
- 嚴格：地點空白或者只寫「港九新界」等泛指 -> 唔收。
- 只影響一般工；IT 工同大灣區計劃豁免。
- 空名單 = 唔篩（避免一填錯就清空塊板）。
"""
import asyncio

import pytest

from app.services import scanner
from app.services.classify import resolve_wanted_locations, wanted_location_match
from app.services.scraper_base import JobDraft

SEED = "觀塘, 旺角, 尖沙咀, 中環"


def _fake_scraper(drafts):
    async def _scrape(session, track="it", cfg=None, **kwargs):
        return list(drafts)
    return _scrape


def _run(db, monkeypatch, drafts, track="general", offertoday_fill=None,
         platform="offertoday"):
    """run_scan with stub scraper (no network) + stubbed browser."""
    async def fake_get_browser(platform_):
        return object()

    monkeypatch.setattr(scanner, "get_browser", fake_get_browser)
    monkeypatch.setattr(scanner.settings, "MAX_ENRICH_PER_SCAN", 0)
    monkeypatch.setattr(scanner.settings, "ENRICH_ALL_IT", False)
    monkeypatch.setattr(scanner, "PLATFORM_SCRAPERS",
                        ((platform, _fake_scraper(drafts), offertoday_fill),))
    return asyncio.run(scanner.run_scan(db, {}, track=track))


def _draft(job_id, title, location="", platform="offertoday", category="general"):
    return JobDraft(platform=platform, job_id=job_id, title=title, location=location,
                    category=category, url=f"https://example.com/{job_id}")


def _whitelist(db, terms=SEED):
    from app.models import Profile

    db.add(Profile(general_wanted_locations=terms))
    db.commit()


# ------------------------------------------------ 字眼設定

def test_empty_list_means_no_filter(monkeypatch):
    """留空 = 唔篩地點（唔會一填錯就清空塊板）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "GENERAL_WANTED_LOCATIONS", "")
    assert resolve_wanted_locations("") == []
    assert wanted_location_match(("赤鱲角",), []) == ""


def test_profile_beats_env(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "GENERAL_WANTED_LOCATIONS", "屯門, 元朗")
    assert resolve_wanted_locations("") == ["屯門", "元朗"]
    assert resolve_wanted_locations(SEED) == ["觀塘", "旺角", "尖沙咀", "中環"]


def test_match_across_location_title_and_jd():
    wanted = ["觀塘", "旺角"]
    assert wanted_location_match(("觀塘", "", ""), wanted) == "觀塘"
    assert wanted_location_match(("觀塘區", "", ""), wanted) == "觀塘"
    assert wanted_location_match(("", "旺角分店文員", ""), wanted) == "旺角"
    assert wanted_location_match(("", "", "工作地點：觀塘工業區"), wanted) == "觀塘"
    assert wanted_location_match(("屯門", "文員", "一般辦公室"), wanted) == ""


def test_latin_location_is_whole_word():
    assert wanted_location_match(("", "", "Kwun Tong office"), ["kwun tong"]) == "kwun tong"
    assert wanted_location_match(("", "", "Kwun Tongx"), ["kwun tong"]) == ""


# ------------------------------------------------ 列表階段

def test_on_list_location_kept(db, monkeypatch):
    from app.models import JobApplication

    _whitelist(db)
    drafts = [_draft("tokKt", "文員", location="觀塘", platform="govhk_general"),
              _draft("tokTm", "文員", location="屯門區", platform="govhk_general")]
    summary = _run(db, monkeypatch, drafts)

    rows = db.query(JobApplication).all()
    assert [r.title for r in rows] == ["文員"]
    assert rows[0].location == "觀塘"
    assert summary.skipped_location == 1
    assert summary.tracks["general"]["skipped_location"] == 1


@pytest.mark.parametrize("location", ["", "港九新界", "香港島,九龍半島", "深圳"])
def test_vague_or_blank_location_is_dropped(db, monkeypatch, location):
    """嚴格模式：寫唔明或者冇寫地點 -> 唔收。"""
    from app.models import JobApplication

    _whitelist(db)
    _run(db, monkeypatch, [_draft("tokVague", "文員", location=location,
                                  platform="govhk_general")])
    assert db.query(JobApplication).count() == 0


def test_empty_whitelist_keeps_everything(db, monkeypatch):
    from app.models import JobApplication

    drafts = [_draft("tokOff", "文員", location="赤鱲角", platform="govhk_general")]
    summary = _run(db, monkeypatch, drafts)
    assert db.query(JobApplication).count() == 1
    assert summary.skipped_location == 0


# ------------------------------------------------ 豁免範圍

def test_gbayes_is_exempt(db, monkeypatch):
    """大灣區計劃（深圳／廣州…）唔跟香港地區名單。"""
    from app.models import JobApplication

    _whitelist(db)
    drafts = [_draft("tokSz", "行政助理", location="深圳", platform="govhk_gbayes")]
    summary = _run(db, monkeypatch, drafts)

    rows = db.query(JobApplication).all()
    assert [r.title for r in rows] == ["行政助理"]
    assert summary.skipped_location == 0


def test_it_track_is_exempt(db, monkeypatch):
    from app.models import JobApplication

    _whitelist(db)
    drafts = [_draft("tokItClk", "IT Support", location="赤鱲角",
                     platform="govhk_it", category="it")]
    summary = _run(db, monkeypatch, drafts, track="it")

    assert [r.title for r in db.query(JobApplication).all()] == ["IT Support"]
    assert summary.skipped_location == 0


# ------------------------------------------- 詳情階段（OfferToday 靠 JD 內文）

def test_jd_that_names_wanted_district_is_kept(db, monkeypatch):
    from app.models import JobApplication

    _whitelist(db)

    async def fake_fetch_detail(session, draft):
        draft.jd_text = "職責：文件處理\n工作地點：觀塘"
        return draft

    _run(db, monkeypatch, [_draft("tokJdOk", "行政助理")], offertoday_fill=fake_fetch_detail)
    rows = db.query(JobApplication).all()
    assert [r.title for r in rows] == ["行政助理"]
    assert rows[0].jd_text.startswith("職責")


def test_jd_with_off_list_district_is_dropped(db, monkeypatch):
    from app.models import JobApplication

    _whitelist(db)

    async def fake_fetch_detail(session, draft):
        draft.jd_text = "職責：倉務\n工作地點：赤鱲角機場貨運站"
        return draft

    summary = _run(db, monkeypatch, [_draft("tokJdAir", "倉務助理")],
                   offertoday_fill=fake_fetch_detail)
    assert db.query(JobApplication).count() == 0
    assert summary.skipped_location == 1


def test_applied_row_is_never_dropped(db, monkeypatch):
    """已申請嘅記錄永遠留住（申請歷史）。"""
    from app.models import JobApplication

    _whitelist(db)
    row = JobApplication(platform="offertoday", job_id_on_platform="tokAppliedOff",
                         title="倉務助理", category="general", status="applied",
                         location="赤鱲角", jd_text="")
    db.add(row)
    db.commit()

    async def fake_fetch_detail(session, draft):
        draft.jd_text = "工作地點：赤鱲角"
        return draft

    _run(db, monkeypatch, [], offertoday_fill=fake_fetch_detail)
    kept = db.query(JobApplication).filter(JobApplication.id == row.id).first()
    assert kept is not None and kept.status == "applied"


def test_old_rows_are_untouched(db, monkeypatch):
    """舊 data 唔理：已入庫嘅一般工唔會因為唔喺名單而被刪。"""
    from app.models import JobApplication

    db.add(JobApplication(platform="offertoday", job_id_on_platform="tokOldVague",
                          title="文員", category="general", status="pending_review",
                          location="港九新界", jd_text=""))
    db.commit()
    _whitelist(db)

    _run(db, monkeypatch, [])
    assert db.query(JobApplication).count() == 1


def test_blank_list_location_defers_to_detail_stage(db, monkeypatch):
    """列表冇工地點（OfferToday 常見）唔可以即刻篩走，要開完 JD 先判斷。"""
    from app.models import JobApplication

    _whitelist(db)

    async def fake_fetch_detail(session, draft):
        draft.jd_text = "工作地點：旺角"
        return draft

    _run(db, monkeypatch, [_draft("tokDefer", "行政助理")], offertoday_fill=fake_fetch_detail)
    assert [r.title for r in db.query(JobApplication).all()] == ["行政助理"]


def test_known_off_list_location_is_dropped_before_detail(db, monkeypatch):
    """列表已經寫明唔喺名單 -> 唔使開詳情，即刻篩走（慳時間）。"""
    from app.models import JobApplication

    _whitelist(db)
    calls = []

    async def fake_fetch_detail(session, draft):
        calls.append(draft.job_id)
        return draft

    _run(db, monkeypatch, [_draft("tokKnownOff", "文員", location="赤鱲角")],
         offertoday_fill=fake_fetch_detail)
    assert db.query(JobApplication).count() == 0
    assert calls == []           # 冇開過詳情頁
