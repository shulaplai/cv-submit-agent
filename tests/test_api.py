"""API-level tests against the FastAPI app with an isolated DB."""


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_profile_default_and_update(client):
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.json()["name"] == ""

    r = client.put("/api/profile", json={
        "name": "陳大文",
        "email": "chan@example.com",
        "cv_en_path": "/tmp/cv_en.pdf",
        "cv_zh_path": "/tmp/cv_zh.pdf",
        "skills_json": '["AI","Python"]',
        "gba_age_under_29": True,
        "gba_edu_associate_degree": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "陳大文"
    assert body["skills_json"] == '["AI","Python"]'


def test_jobs_list_and_detail(client, seed_job):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(j["id"] == seed_job.id for j in items)

    r = client.get(f"/api/jobs/{seed_job.id}")
    assert r.status_code == 200
    assert r.json()["contact_email"] == "hr@example.com"


def test_cover_letter_versioning(client, seed_job):
    r = client.post(f"/api/jobs/{seed_job.id}/cover-letters", json={"content": "第一版 CL"})
    assert r.status_code == 200
    assert len(r.json()["cover_letters"]) == 1

    r = client.post(f"/api/jobs/{seed_job.id}/cover-letters", json={"content": "第二版 CL（改短啲）"})
    assert r.status_code == 200
    cls = r.json()["cover_letters"]
    assert len(cls) == 2
    assert cls[0]["version"] == 1 and cls[1]["version"] == 2


def test_mark_applied_and_status(client, seed_job):
    r = client.post(f"/api/jobs/{seed_job.id}/mark-applied")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert r.json()["applied_at"] is not None

    r = client.patch(f"/api/jobs/{seed_job.id}", json={"status": "interviewing", "notes": "下輪 video call", "interview_stage": "first_interview"})
    assert r.status_code == 200
    assert r.json()["status"] == "interviewing"
    assert r.json()["notes"] == "下輪 video call"

    r = client.patch(f"/api/jobs/{seed_job.id}", json={"status": "not_a_status"})
    assert r.status_code == 400


def test_stats(client, seed_job):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert "govhk" in body["by_platform"]
    assert len(body["weekly_applied"]) == 8


def test_low_match_hidden_by_default(client, db):
    from app.models import JobApplication

    row = JobApplication(platform="offertoday", job_id_on_platform="999999",
                         title="低分工", status="low_match", match_score=20)
    db.add(row)
    db.commit()

    r = client.get("/api/jobs")
    assert not any(j["title"] == "低分工" for j in r.json()["items"])
    assert r.json()["hidden_low_match"] >= 1

    r = client.get("/api/jobs?show_all=true")
    assert any(j["title"] == "低分工" for j in r.json()["items"])


def test_jobsdb_hidden_by_default(client, db):
    from app.models import JobApplication

    row = JobApplication(platform="jobsdb", job_id_on_platform="998877",
                         title="JobsDB 職位", status="pending_review", match_score=80)
    db.add(row)
    db.commit()

    r = client.get("/api/jobs")
    assert not any(j["title"] == "JobsDB 職位" for j in r.json()["items"])

    r = client.get("/api/jobs?show_all=true")
    assert not any(j["title"] == "JobsDB 職位" for j in r.json()["items"])


def test_scan_status(client):
    r = client.get("/api/scan/status")
    assert r.status_code == 200
    assert r.json()["running"] in (True, False)


def test_scan_stop_endpoint_when_idle(client):
    """暫停掣：冇 scan 行緊時撳暫停 -> 有禮貌嘅錯誤回應，唔 crash。"""
    r = client.post("/api/scan/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["stopped"] is False
    assert "冇 scan" in body["message"]


def test_scan_stop_endpoint_while_running(client, monkeypatch):
    """行緊時撳暫停 -> 設定 stop flag，狀態有 stop_requested。"""
    from app.services import scan_control

    async def fake_run_scan(db, progress):
        scan_control.request_stop()
        from app.services.scanner import ScanSummary
        s = ScanSummary(stopped=True, scanned=1)
        return s

    from app.routers import scan as scan_router

    monkeypatch.setattr(scan_router, "run_scan", fake_run_scan)
    scan_control.clear_stop()

    # start the scan (background task)
    r = client.post("/api/scan")
    assert r.json()["started"] is True

    # wait a moment for the background task to finish, then check status
    import time
    for _ in range(50):
        status = client.get("/api/scan/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)

    status = client.get("/api/scan/status").json()
    assert status["last"] is not None
    assert status["last"]["stopped"] is True
    assert status["stop_requested"] is False  # cleared after scan ends
