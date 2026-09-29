"""「補齊未評分 IT 工」：scope=it_unscored 只揀未評分嘅 IT 工。"""
import pytest

from app.models import JobApplication


def _seed(db, jid, **kw):
    defaults = dict(platform="govhk_it", title="Software Engineer", category="it",
                    status="pending_review", jd_language="zh", match_score=0,
                    jd_text="負責軟件開發")
    defaults.update(kw)
    row = JobApplication(job_id_on_platform=jid, **defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_unscored_it_candidates_only_picks_unscored_it(db):
    from app.services.scanner import unscored_it_candidates

    wanted = _seed(db, "a", title="Software Engineer", match_score=0)
    _seed(db, "b", title="Software Engineer", match_score=72)        # 已評分
    _seed(db, "c", title="文員", category="general", match_score=0)   # 一般工
    _seed(db, "d", title="Software Engineer", match_score=0, status="applied")  # 已投
    _seed(db, "e", title="AI Engineer", match_score=0)

    rows = unscored_it_candidates(db, 10)
    ids = {r.id for r in rows}
    assert ids == {wanted.id, [r for r in db.query(JobApplication).all()
                               if r.job_id_on_platform == "e"][0].id}


def test_unscored_it_candidates_respects_limit(db):
    from app.services.scanner import unscored_it_candidates

    for i in range(5):
        _seed(db, f"x{i}")
    assert len(unscored_it_candidates(db, 3)) == 3


def test_backfill_endpoint_it_unscored_scope(client, monkeypatch, db):
    """POST /api/scan/backfill {scope: it_unscored} -> 只處理未評分 IT 工。"""
    from app.routers import scan as scan_router

    class _SessionShim:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    row = _seed(db, "bf1", match_score=0)
    other = _seed(db, "bf2", title="文員", category="general", match_score=0)

    processed: list[int] = []

    async def fake_enrich(db_, row_, platform, fetch_detail, skills, pace=None):
        processed.append(row_.id)
        row_.match_score = 66
        row_.status = "pending_review"
        db_.flush()
        return False

    monkeypatch.setattr(scan_router, "SessionLocal", lambda: _SessionShim(db))
    monkeypatch.setattr(scan_router, "_enrich_one", fake_enrich)
    scan_router._state["running"] = False

    import time
    r = client.post("/api/scan/backfill", json={"scope": "it_unscored", "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["started"] is True and body["scope"] == "it_unscored"
    assert "未評分 IT 工" in body["message"]

    for _ in range(100):
        if not client.get("/api/scan/status").json()["running"]:
            break
        time.sleep(0.05)

    assert processed == [row.id]                     # 一般工冇被處理
    status = client.get("/api/scan/status").json()
    assert status["last_backfill"]["scope"] == "it_unscored"
    assert other.id not in processed


def test_backfill_endpoint_rejects_unknown_scope(client, monkeypatch):
    from app.routers import scan as scan_router

    scan_router._state["running"] = False
    r = client.post("/api/scan/backfill", json={"scope": "everything"})
    assert r.status_code == 400
    assert "scope" in r.json()["detail"]


def test_backfill_endpoint_default_scope_is_oldest(client, monkeypatch, db):
    from app.routers import scan as scan_router

    scan_router._state["running"] = False

    async def fake_backfill(limit=10, scope="oldest"):
        return None

    monkeypatch.setattr(scan_router, "_backfill_job", fake_backfill)
    r = client.post("/api/scan/backfill", json={})
    assert r.status_code == 200
    assert r.json()["scope"] == "oldest"
    scan_router._state["running"] = False
