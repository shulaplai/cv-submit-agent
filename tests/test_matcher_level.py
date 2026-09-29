"""Match 評分加入「資歷／穩定」維度：
  - prompt 帶求職者年資（Settings 頁 years_experience）
  - LLM 回嘅 level（under／fit／over）會寫入 DB + 職位台篩選
  - LLM 回 garbage level -> 留空（唔會亂填）
  - LLM 失敗 -> 關鍵字 fallback，AI 職位唔會因為冇技能清單而變 0 分
"""
import asyncio

import pytest

from app.models import JobApplication, Profile
from app.services import matcher


def test_keyword_score_ai_bonus_without_skills():
    """冇技能清單時 AI 職位要有分（否則 LLM 一失敗 AI 工就沉底）。"""
    assert matcher.keyword_score("AI Engineer", "LangGraph agent", []) > 50
    assert matcher.keyword_score("行政秘書", "文書處理", []) == 50


def test_match_prompt_includes_years_of_experience(db):
    db.add(Profile(id=1, years_experience=3, prefer_ai=True))
    db.commit()

    messages = matcher._build_match_messages(
        {"title": "AI Engineer", "company": "X", "jd_text": "開發 LLM 應用"}, [])
    blob = messages[0]["content"] + messages[1]["content"]
    assert "3 年" in blob
    assert "over" in blob and "fit" in blob          # 要求輸出 level
    assert "合約" in blob                             # 合約／外派要扣分


def test_match_prompt_without_experience_setting(db):
    db.add(Profile(id=1, years_experience=0))
    db.commit()
    messages = matcher._build_match_messages({"title": "X", "jd_text": "y"}, [])
    assert "中級程度" in messages[0]["content"]


def test_llm_match_returns_level(monkeypatch, db):
    async def fake_json(messages, temperature=0.0):
        return {"score": 40, "reason": "要求 5 年經驗，超出你年資", "level": "Over"}

    monkeypatch.setattr(matcher.llm_svc, "chat_json", fake_json)
    score, reason, level = asyncio.run(
        matcher.llm_match_score({"title": "Senior Engineer", "jd_text": "5+ years"}, []))
    assert score == 40
    assert level == "over"                            # 大小寫都收


def test_llm_match_garbage_level_is_blank(monkeypatch, db):
    async def fake_json(messages, temperature=0.0):
        return {"score": 70, "reason": "ok", "level": "excellent"}

    monkeypatch.setattr(matcher.llm_svc, "chat_json", fake_json)
    _s, _r, level = asyncio.run(matcher.llm_match_score({"title": "X", "jd_text": "y"}, []))
    assert level == ""


def test_score_job_without_jd_has_no_level():
    score, reason, level = asyncio.run(
        matcher.score_job({"title": "AI Engineer", "short_desc": "python"}, []))
    assert score >= 0 and reason == "" and level == ""


def test_enrich_stores_level_and_flags(db, monkeypatch):
    """掃描 enrich 之後：match_level + AI／合約／外派 flag 都要入 DB。"""
    from app.services import scanner

    row = JobApplication(platform="govhk", job_id_on_platform="lvl1",
                         title="AI Engineer (Contract)", company="測試公司",
                         category="it", status="pending_review",
                         jd_text="負責 LLM 應用開發", jd_language="zh")
    db.add(row)
    db.commit()

    async def fake_score_job(job_dict, skills):
        return (55, "資歷超出你年資", "over")

    monkeypatch.setattr(scanner, "score_job", fake_score_job)
    monkeypatch.setattr(scanner.settings, "MATCH_THRESHOLD", 90)

    asyncio.run(scanner._enrich_one(db, row, "govhk", None, []))
    db.expire_all()
    fresh = db.query(JobApplication).filter_by(job_id_on_platform="lvl1").one()
    assert fresh.match_level == "over"
    assert fresh.ai_match is True          # 標題有 AI
    assert fresh.is_contract is True        # 標題有 Contract
    assert fresh.status == "low_match"      # 55 < 90


def test_refresh_endpoint_stores_level(client, db, monkeypatch):
    """詳情頁「重新整理」一樣會更新 level 同 flags。"""
    from app.services import matcher as _matcher

    row = JobApplication(platform="govhk", job_id_on_platform="lvl2",
                         title="IT Support Engineer", category="it",
                         status="pending_review", jd_text="Desktop support",
                         jd_language="en")
    db.add(row)
    db.commit()

    async def fake_json(messages, temperature=0.0):
        return {"score": 72, "reason": "啱你級數", "level": "fit"}

    monkeypatch.setattr(_matcher.llm_svc, "chat_json", fake_json)
    r = client.post(f"/api/jobs/{row.id}/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["match_level"] == "fit"
    assert r.json()["is_agency"] is False
