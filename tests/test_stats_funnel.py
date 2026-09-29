"""統計漏斗：用數據答「邊類工值得繼續投」（IT vs 一般、AI vs 非 AI、分數段）。"""
from datetime import datetime, timezone

from app.models import JobApplication


def _applied(db, jid, status, **kw):
    defaults = dict(platform="govhk_it", title="Software Engineer", category="it",
                    jd_language="zh", match_score=70, ai_match=False,
                    applied_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    outcome_at=datetime(2026, 9, 5, tzinfo=timezone.utc)
                    if status not in ("applied",) else None)
    defaults.update(kw)
    row = JobApplication(job_id_on_platform=jid, status=status, **defaults)
    db.add(row)
    db.commit()
    return row


def test_funnel_groups_by_category(db, client):
    _applied(db, "a1", "applied", category="it")
    _applied(db, "a2", "interviewing", category="it", match_score=80, ai_match=True)
    _applied(db, "a3", "rejected", category="it", match_score=80, ai_match=True)
    _applied(db, "a4", "no_response", category="general", match_score=40)
    _applied(db, "a5", "offer", category="general", match_score=30)
    _applied(db, "a6", "applied", category="general", match_score=45)

    data = client.get("/api/stats").json()
    by_cat = {row["key"]: row for row in data["funnel_by_category"]}

    it = by_cat["it"]
    assert it["applied"] == 3
    assert it["responded"] == 2          # interviewing + rejected
    assert it["interviewing"] == 1
    assert it["rejected"] == 1
    assert it["pending"] == 1            # 仲未記錄結果
    assert it["response_rate"] == round(2 / 3 * 100, 1)

    general = by_cat["general"]
    assert general["applied"] == 3
    assert general["no_response"] == 1
    assert general["offer"] == 1
    assert general["responded"] == 1     # 只有 offer（冇回音唔算有回覆）
    assert general["pending"] == 1


def test_funnel_groups_by_ai_flag(db, client):
    _applied(db, "b1", "interviewing", ai_match=True, match_score=80)
    _applied(db, "b2", "rejected", ai_match=True, match_score=75)
    _applied(db, "b3", "applied", ai_match=False, match_score=60)
    _applied(db, "b4", "no_response", ai_match=False, match_score=40)

    data = client.get("/api/stats").json()
    by_ai = {row["key"]: row for row in data["funnel_by_ai"]}
    assert by_ai["ai"]["applied"] == 2
    assert by_ai["ai"]["responded"] == 2
    assert by_ai["non_ai"]["applied"] == 2
    assert by_ai["non_ai"]["responded"] == 0
    assert by_ai["non_ai"]["no_response"] == 1


def test_funnel_groups_by_score_band(db, client):
    _applied(db, "c1", "interviewing", match_score=88)
    _applied(db, "c2", "rejected", match_score=70)
    _applied(db, "c3", "no_response", match_score=55)
    _applied(db, "c4", "applied", match_score=30)
    _applied(db, "c5", "applied", match_score=0)

    data = client.get("/api/stats").json()
    bands = {row["key"]: row for row in data["funnel_by_score"]}
    assert bands["high"]["applied"] == 2          # >= 65
    assert bands["high"]["responded"] == 2
    assert bands["mid"]["applied"] == 1           # 50–64
    assert bands["mid"]["responded"] == 0
    assert bands["low"]["applied"] == 2           # < 50
    assert bands["low"]["responded"] == 0


def test_funnel_only_counts_applied_rows(db, client):
    _applied(db, "d1", "pending_review", match_score=90)     # 未投 -> 唔入漏斗
    _applied(db, "d2", "low_match", match_score=20)
    _applied(db, "d3", "interviewing", match_score=80)

    data = client.get("/api/stats").json()
    total_applied = sum(row["applied"] for row in data["funnel_by_category"])
    assert total_applied == 1


def test_stats_still_reports_weekly_goal(client, db):
    _applied(db, "e1", "applied")
    data = client.get("/api/stats").json()
    assert data["weekly_goal"] == 15
    assert data["funnel"]                       # 合併清單照有
