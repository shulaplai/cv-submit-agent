"""職位台「搵更適合自己嘅工」篩選：AI／合約／外派／資歷級別 + focus 排序。"""
import pytest


def _seed(db, jid, **kw):
    from app.models import JobApplication

    defaults = dict(platform="govhk_it", title="Software Engineer", category="it",
                    status="pending_review", jd_language="zh", match_score=60,
                    match_level="", ai_match=False, is_contract=False, is_agency=False)
    defaults.update(kw)
    row = JobApplication(job_id_on_platform=jid, **defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def board(db):
    """四份工：AI 高分／普通高分／合約／外派（資歷超出）。"""
    return {
        "ai": _seed(db, jid="j1", title="AI Engineer", match_score=80,
                    ai_match=True, match_level="fit", posted_at="2026-09-01"),
        "plain": _seed(db, jid="j2", title="System Engineer", match_score=90,
                       match_level="fit", posted_at="2026-08-01"),
        "contract": _seed(db, jid="j3", title="IT Technician (Contract)", match_score=70,
                          is_contract=True, match_level="fit", posted_at="2026-08-15"),
        "agency": _seed(db, jid="j4", title="Programmer (EA)", match_score=60,
                        is_agency=True, match_level="over", posted_at="2026-08-20"),
    }


def _get(client, **params):
    r = client.get("/api/jobs", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_ai_only_filter(client, board):
    data = _get(client, ai_only=True)
    assert [i["id"] for i in data["items"]] == [board["ai"].id]
    assert all(i["ai_match"] for i in data["items"])


def test_exclude_contract_and_agency(client, board):
    ids = [i["id"] for i in _get(client, exclude_contract=True)["items"]]
    assert board["contract"].id not in ids
    assert board["ai"].id in ids

    ids = [i["id"] for i in _get(client, exclude_agency=True)["items"]]
    assert board["agency"].id not in ids


def test_levels_filter(client, board):
    data = _get(client, levels="fit")
    ids = [i["id"] for i in data["items"]]
    assert board["ai"].id in ids and board["plain"].id in ids
    assert board["agency"].id not in ids      # level=over

    data = _get(client, levels="over")
    assert [i["id"] for i in data["items"]] == [board["agency"].id]


def test_focus_sort_puts_ai_and_high_scores_first(client, board):
    data = _get(client, sort="focus")
    ids = [i["id"] for i in data["items"]]
    assert ids[0] == board["ai"].id                    # AI 優先
    assert ids[1] == board["plain"].id                 # 之後按分數（90 > 70 > 60）
    assert ids == [board["ai"].id, board["plain"].id, board["contract"].id,
                   board["agency"].id]


def test_sort_validation(client):
    r = client.get("/api/jobs", params={"sort": "nonsense"})
    assert r.status_code == 400
    assert "focus" in r.json()["detail"]


def test_facets_report_new_dimensions(client, board):
    facets = _get(client)["facets"]
    assert facets["ai"] == 1
    assert facets["contract"] == 1
    assert facets["agency"] == 1
    assert facets["levels"]["fit"] == 3
    assert facets["levels"]["over"] == 1


def test_ai_facet_not_zeroed_by_active_ai_filter(client, board):
    """撳咗 AI chip 之後，其他 chip 嘅數字唔應該冚零。"""
    facets = _get(client, ai_only=True)["facets"]
    assert facets["ai"] == 1
    assert facets["contract"] == 1
    assert facets["agency"] == 1


def test_job_payload_carries_flags_and_cv_variant(client, board):
    item = _get(client, ai_only=True)["items"][0]
    assert item["ai_match"] is True
    assert item["match_level"] == "fit"
    assert item["cv_variant"] == "通用版"       # 測試環境冇設定任何 CV
