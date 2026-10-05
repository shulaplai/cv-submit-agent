"""Shared test fixtures. Isolates the DB to a temp dir and disables the scheduler."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

_TMP = tempfile.mkdtemp(prefix="cvsubmit_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["SCAN_DAY_INTERVAL"] = "0"  # no scheduler in tests
os.environ["SCAN_HOUR"] = "23"
os.environ["SCAN_JOB_DELAY_MIN_SECONDS"] = "0"  # no politeness pacing in tests
os.environ["SCAN_JOB_DELAY_MAX_SECONDS"] = "0"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_FALLBACK_API_KEY"] = ""
# 唔好污染真資料：log 同 last_scan 記錄都寫入臨時目錄
os.environ["CVSUBMIT_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["LAST_SCAN_PATH"] = os.path.join(_TMP, "last_scan.json")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CoverLetter, JobApplication  # noqa: E402


# ---------------------------------------------------------------------------
# 測試用日期：**唔好用硬編碼日期**（例如 "2026-08-01"）—— 掃描器嘅「新鮮度」窗口
# 係相對今日計，硬編碼日期會隨時間變舊，令一批測試無聲無息咁全部掛
# （2026-10-05 就係咁樣中招）。一律用 days_ago() / days_ago_dmy() 計返。
# ---------------------------------------------------------------------------
def days_ago(n: int) -> str:
    """n 日前嘅 ISO 日期（YYYY-MM-DD）。"""
    from datetime import date, timedelta

    return (date.today() - timedelta(days=n)).isoformat()


def days_ago_dmy(n: int) -> str:
    """n 日前嘅 DD/MM/YYYY（gov.hk 列表格式）。"""
    from datetime import date, timedelta

    return (date.today() - timedelta(days=n)).strftime("%d/%m/%Y")


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
