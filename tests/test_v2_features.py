"""Tests for the v2 improvements: CL validation, LLM key override, dup_key,
weekly goal, email preview, backfill selection, scan budget."""
import json

import pytest

from app.services import llm as llm_svc
from app.services.cl_generator import generate_cl_checked, validate_cl
from app.services.scanner import _backfill_candidates, make_dup_key


# ------------------------------------------------------------ CL validation

def test_validate_cl():
    assert validate_cl("This is a proper English cover letter of reasonable length. " * 15, "en") == []
    zh = "你好，我係申請人，希望加入貴公司。" * 30
    assert validate_cl(zh, "zh") == []
    # wrong language
    assert validate_cl("This is English but the JD wanted Chinese", "zh")
    assert validate_cl("這是中文但 JD 要求英文", "en")
    # too short
    assert validate_cl("short", "en")
    # too long
    assert validate_cl("word " * 700, "en")


def test_generate_cl_checked_retry_on_language(monkeypatch):
    calls = {"n": 0}

    async def fake_generate(cv_text, jd, job, language, instructions=""):
        calls["n"] += 1
        # first attempt: wrong language; retry: correct
        if calls["n"] == 1:
            return "This is an English response for a Chinese JD." * 4
        return "這是修正後嘅繁體中文求職信。" * 20

    monkeypatch.setattr("app.services.cl_generator.generate_cl", fake_generate)
    content, warning = asyncio_run(
        generate_cl_checked("cv", "jd", {"title": "t"}, "zh")
    )
    assert calls["n"] == 2
    assert "繁體中文" in content


def test_generate_cl_checked_ok_first_try(monkeypatch):
    async def fake_generate(cv_text, jd, job, language, instructions=""):
        return "Perfect English cover letter body. " * 25

    monkeypatch.setattr("app.services.cl_generator.generate_cl", fake_generate)
    content, warning = asyncio_run(
        generate_cl_checked("cv", "jd", {"title": "t"}, "en")
    )
    assert warning == ""
    assert "Perfect English" in content


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ------------------------------------------------------------ dup key

def test_make_dup_key():
    assert make_dup_key("PhoMedics  Limited", "Senior AI Engineer") == \
        make_dup_key("PhoMedics Limited", "Senior  AI Engineer")
    assert make_dup_key("A Co", "Job") != make_dup_key("B Co", "Job")


# ------------------------------------------------------------ LLM key override

def test_llm_db_key_override(db):
    from app.models import Profile

    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    profile.llm_api_key = "sk-db-override"
    db.commit()
    primary, fallback = llm_svc._resolved_keys()
    assert primary == "sk-db-override"


def test_llm_no_keys(db):
    from app.models import Profile

    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    profile.llm_api_key = ""
    profile.llm_fallback_api_key = ""
    db.commit()
    assert llm_svc._resolved_keys() == ("", "")
    assert not llm_svc.has_any_key()


# ------------------------------------------------------------ backfill selection

def test_backfill_candidates_picks_oldest_unenriched(db):
    from app.models import JobApplication

    for i, (jid, status) in enumerate([
        ("11-26-0000001", "pending_review"),  # no CL -> candidate
        ("11-26-0000002", "applied"),          # applied -> excluded
        ("11-26-0000003", "pending_review"),   # no CL -> candidate
    ]):
        db.add(JobApplication(platform="govhk", job_id_on_platform=jid,
                              title=f"Job {i}", status=status))
    db.commit()

    cands = _backfill_candidates(db, 10)
    ids = {c.job_id_on_platform for c in cands}
    assert "11-26-0000001" in ids
    assert "11-26-0000003" in ids
    assert "11-26-0000002" not in ids  # applied excluded
    assert len(cands) == 2


# ------------------------------------------------------------ API: weekly goal + email preview

def test_stats_weekly_goal(client, seed_job):
    r = client.get("/api/stats")
    body = r.json()
    assert body["weekly_goal"] >= 1
    assert "applied_this_week" in body


def test_email_preview(client, seed_job):
    r = client.get(f"/api/jobs/{seed_job.id}/email-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["to"] == "hr@example.com"
    assert "AI 工程師" in body["subject"]


def test_email_preview_requires_contact(client, db):
    from app.models import JobApplication

    row = JobApplication(platform="jobsdb", job_id_on_platform="999000",
                         title="No email job", status="pending_review",
                         contact_email="", apply_method="form")
    db.add(row)
    db.commit()
    r = client.get(f"/api/jobs/{row.id}/email-preview")
    assert r.status_code == 400


# ------------------------------------------------------------ API: LLM test + extract (mocked)

def test_test_llm_no_key(client):
    r = client.post("/api/profile/test-llm")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "key" in body["error"]


def test_extract_skills_no_cv(client):
    r = client.post("/api/profile/extract-skills")
    assert r.status_code in (400, 502)  # no CV configured or LLM call fails


# ------------------------------------------------------------ CV file picker / upload

def test_upload_cv_saves_file(client, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    fake_pdf = b"%PDF-1.4\nfake cv content"
    r = client.post(
        "/api/profile/cv",
        data={"kind": "en"},
        files={"file": ("CV_en.pdf", fake_pdf, "application/pdf")},
    )
    assert r.status_code == 200
    assert r.json()["cv_en_path"].endswith("cv_en.pdf")
    saved = tmp_path / "cvs" / "cv_en.pdf"
    assert saved.exists()
    assert saved.read_bytes() == fake_pdf


def test_upload_cv_rejects_non_pdf(client):
    r = client.post(
        "/api/profile/cv",
        data={"kind": "en"},
        files={"file": ("cv.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_cv_rejects_bad_kind(client):
    r = client.post(
        "/api/profile/cv",
        data={"kind": "fr"},
        files={"file": ("cv.pdf", b"%PDF", "application/pdf")},
    )
    assert r.status_code == 400


def test_resolve_cv_path_profile_override(db):
    from app.models import Profile
    from app.services.cv_loader import resolve_cv_path

    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    profile.cv_en_path = "/tmp/from_profile_en.pdf"
    profile.cv_zh_path = "/tmp/from_profile_zh.pdf"
    db.commit()
    assert resolve_cv_path("en") == "/tmp/from_profile_en.pdf"
    assert resolve_cv_path("zh") == "/tmp/from_profile_zh.pdf"
