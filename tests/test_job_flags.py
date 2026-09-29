"""AI／合約／外派 flag 推斷 + 開機 backfill（職位台新篩選 chip 嘅基礎）。"""
from app.models import JobApplication
from app.services.jobflags import ai_match, compute_flags, is_agency, is_contract


def test_ai_match_from_title():
    assert ai_match("AI Engineer") is True
    assert ai_match("人工智能工程師") is True
    assert ai_match("大模型應用開發") is True
    assert ai_match("Web Developer") is False


def test_ai_match_from_jd_only():
    """OfferToday 好多時 AI 字眼只喺 JD 出現（標題得「Software Developer」）。"""
    assert ai_match("Software Developer", "我們用 LLM + LangGraph 做 AI Agent") is True
    assert ai_match("Software Developer", "負責 React 前端開發") is False


def test_ai_match_word_boundary_does_not_hit_email_or_detail():
    """拉丁字用詞邊界：'ai' 唔應該命中 email／detail（否則滿板都係 AI）。"""
    assert ai_match("Email Marketing Executive") is False
    assert ai_match("Detail Coordinator") is False
    assert ai_match("AI基礎架構主任") is True      # 中英夾雜照中


def test_jd_scan_window_is_limited():
    """JD 只睇頭 800 字，避免一句「AI」喺好後嘅福利段落就當成 AI 職位。"""
    long_jd = "負責一般文書處理。" * 200 + "本公司使用 AI 工具"
    assert ai_match("文員", long_jd) is False


def test_contract_flag():
    assert is_contract("合約資訊科技技術員") is True
    assert is_contract("IT Technician (Contract)") is True
    assert is_contract("系統工程師", "6-month renewable contract") is True
    assert is_contract("資訊科技主任") is False


def test_agency_flag():
    assert is_agency("IT Support (EA)") is True
    assert is_agency("程式員", "Manpower Services (Hong Kong) Limited") is True
    assert is_agency("外派技術員") is True
    assert is_agency("系統工程師", "香港科技有限公司") is False


def test_compute_flags_reads_row_attributes():
    class Row:
        title = "AI Engineer (Contract)"
        company = "Recruit Express (Hong Kong) Limited"
        jd_text = ""

    flags = compute_flags(Row())
    assert flags == {"ai_match": True, "is_contract": True, "is_agency": True}


def test_backfill_job_flags_fills_legacy_rows(db):
    """開機 backfill 幫舊行（flag 預設 False）計返 AI／合約／外派。"""
    from app.db import _backfill_job_flags

    row = JobApplication(platform="govhk", job_id_on_platform="legacy1",
                         title="AI 工程師 (合約)", company="Manpower Services (Hong Kong) Ltd",
                         category="it", status="pending_review")
    db.add(row)
    db.commit()
    assert row.ai_match is False and row.is_contract is False and row.is_agency is False

    _backfill_job_flags()

    db.expire_all()
    fresh = db.query(JobApplication).filter_by(job_id_on_platform="legacy1").one()
    assert fresh.ai_match is True
    assert fresh.is_contract is True
    assert fresh.is_agency is True

    # 冪等：再跑一次唔會改任何嘢（亦唔會爆）
    _backfill_job_flags()
    db.expire_all()
    again = db.query(JobApplication).filter_by(job_id_on_platform="legacy1").one()
    assert (again.ai_match, again.is_contract, again.is_agency) == (True, True, True)
