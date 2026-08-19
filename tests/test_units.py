"""Language detection, matcher, LLM JSON parsing, email bot unit tests."""
import re

from app.services import matcher
from app.services.email_bot import _apple_escape, build_email
from app.services.language import detect_language
from app.services.llm import _parse_json


def test_detect_language():
    assert detect_language("職責：負責多模態大模型Token資源管理") == "zh"
    assert detect_language("We are looking for a Senior AI Engineer.") == "en"
    assert detect_language("") == "en"
    # mixed with English tech words still Chinese-dominant
    assert detect_language("負責Linux、基礎系統服務及雲資源；優化告警規則，提升運維效率及故障發現精准度。") == "zh"


def test_keyword_score():
    skills = ["Python", "LangGraph", "Engineer", "TypeScript"]
    assert matcher.keyword_score("AI Engineer (Python)", "We need LangGraph experience", skills) > 50
    assert matcher.keyword_score("行政秘書", "文書處理", skills) < 30
    assert matcher.keyword_score("", "", []) == 50  # unknown skills -> neutral


def test_parse_json_robust():
    assert _parse_json('{"score": 88, "reason": "ok"}') == {"score": 88, "reason": "ok"}
    assert _parse_json('```json\n{"score": 70, "reason": "好"}\n```')["score"] == 70
    assert _parse_json('前文 {"score": 60, "reason": "x"} 後文')["score"] == 60
    import pytest
    with pytest.raises(Exception):
        _parse_json("完全唔係 JSON")


def test_build_email():
    class FakeRow:
        title = "AI 工程師"
        company = "測試公司"
        platform = "govhk"
        contact_email = "hr@example.com"
        contact_person = "陳先生"

    email = build_email(FakeRow(), "你好，我係求職者。", "/tmp/cv.pdf")
    assert email["to"] == "hr@example.com"
    assert "AI 工程師" in email["subject"]
    assert "測試公司" in email["subject"]
    assert email["body"].startswith("你好，我係求職者。")
    assert email["attachment"].endswith("cv.pdf")  # /tmp resolves to /private/tmp on macOS


def test_apple_escape():
    s = _apple_escape('he said "hi"\nnew line')
    assert '\\"' in s
    assert "\\n" in s


def test_store_dedup(db):
    from app.models import JobApplication
    from app.services.scraper_base import JobDraft
    from app.services.store import persist_drafts

    d1 = JobDraft(platform="govhk", job_id="11-26-0000001", title="AI 工程師")
    new, dup, rows = persist_drafts(db, [d1])
    assert new == 1 and dup == 0
    # same platform+job id again -> duplicate
    new2, dup2, _ = persist_drafts(db, [d1])
    assert new2 == 0 and dup2 == 1
    # different job id -> new
    d2 = JobDraft(platform="govhk", job_id="11-26-0000002", title="軟件工程師")
    new3, _, _ = persist_drafts(db, [d2])
    assert new3 == 1
