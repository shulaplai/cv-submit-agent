"""Shared test fixtures. Isolates the DB to a temp dir and disables the scheduler."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

_TMP = tempfile.mkdtemp(prefix="cvsubmit_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["SCAN_INTERVAL_HOURS"] = "0"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_FALLBACK_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CoverLetter, JobApplication  # noqa: E402


@pytest.fixture(scope="session")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def clean_tables():
    """Wipe all tables before every test for full isolation."""
    from app.db import SessionLocal, init_db
    from app.models import CoverLetter, JobApplication, Profile

    init_db()
    s = SessionLocal()
    s.query(CoverLetter).delete()
    s.query(JobApplication).delete()
    s.query(Profile).delete()
    s.commit()
    s.close()
    yield


@pytest.fixture()
def seed_job(db):
    row = JobApplication(
        platform="govhk",
        job_id_on_platform="11-26-0000001",
        title="AI 工程師",
        company="測試公司",
        location="深圳",
        salary_range="$18,000（月薪）",
        jd_text="職責：負責 AI 模型開發。資歷：學士。",
        jd_language="zh",
        status="pending_review",
        match_score=70,
        apply_method="email",
        contact_email="hr@example.com",
        contact_person="陳先生",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def seed_cl(db, seed_job):
    cl = CoverLetter(application_id=seed_job.id, language="zh", content="求職信 v1", version=1)
    db.add(cl)
    db.commit()
    db.refresh(cl)
    return cl
