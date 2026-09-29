"""OfferToday 自我介紹發送前 AI 潤色（用戶要求）。"""
import asyncio

import pytest

from app.models import JobApplication, Profile
from app.services import apply_bot, polish
from app.services.polish import PolishError

INTRO_ZH = ("我擁有三年全端開發經驗，主力使用 React 與 Laravel，"
            "並曾開發整合 WhatsApp API 的 SaaS 預約平台，熟悉 Docker 容器化與 TypeScript。")


def _job(db, **kw):
    defaults = dict(platform="offertoday", job_id_on_platform="intro1",
                    title="AI Engineer", company="測試公司", category="it",
                    jd_language="zh", status="pending_review",
                    jd_text="負責 LLM 應用開發")
    defaults.update(kw)
    row = JobApplication(**defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_polish_intro_returns_polished(monkeypatch):
    async def fake_chat(messages, temperature=0.3):
        assert "自我介紹" in messages[0]["content"]
        return INTRO_ZH

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    out = asyncio.run(polish.polish_intro(INTRO_ZH, "zh", topic="it"))
    assert out == INTRO_ZH


def test_polish_intro_rejects_over_long(monkeypatch):
    async def fake_chat(messages, temperature=0.3):
        return "我擁有三年經驗。" * 60          # 遠超 280 字

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    with pytest.raises(PolishError):
        asyncio.run(polish.polish_intro(INTRO_ZH, "zh"))


def test_polish_intro_rejects_wrong_language(monkeypatch):
    async def fake_chat(messages, temperature=0.3):
        return "I am a full stack developer with three years of experience."

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    with pytest.raises(PolishError):
        asyncio.run(polish.polish_intro(INTRO_ZH, "zh"))


def test_intro_polished_is_stored_on_the_job(db, monkeypatch):
    row = _job(db)
    polished_text = "擁有三年全端開發經驗，專精 React、Laravel 與 Docker，曾開發 WhatsApp API 整合平台。"

    async def fake_intro(row_, cfg):
        return INTRO_ZH

    async def fake_polish(text, lang, topic="general", title="", instructions=""):
        assert lang == "zh" and topic == "ai" and title == "AI Engineer"
        return polished_text

    monkeypatch.setattr(apply_bot, "_offertoday_intro", fake_intro)
    monkeypatch.setattr(polish, "polish_intro", fake_polish)

    text, was_polished = asyncio.run(apply_bot._offertoday_intro_polished(row, {}))
    assert text == polished_text
    assert was_polished is True

    db.expire_all()
    fresh = db.get(JobApplication, row.id)
    assert fresh.offertoday_intro_polished == polished_text
    assert fresh.offertoday_intro_polished_at is not None


def test_intro_polish_failure_falls_back_to_original(db, monkeypatch):
    row = _job(db)

    async def fake_intro(row_, cfg):
        return INTRO_ZH

    async def fake_polish(text, lang, topic="general", title="", instructions=""):
        raise PolishError("太長")

    monkeypatch.setattr(apply_bot, "_offertoday_intro", fake_intro)
    monkeypatch.setattr(polish, "polish_intro", fake_polish)

    text, was_polished = asyncio.run(apply_bot._offertoday_intro_polished(row, {}))
    assert text == INTRO_ZH                    # 用原文，唔會冇字可發
    assert was_polished is False

    db.expire_all()
    assert db.get(JobApplication, row.id).offertoday_intro_polished == ""


def test_intro_polish_crash_never_blocks_applying(db, monkeypatch):
    row = _job(db)

    async def fake_intro(row_, cfg):
        return INTRO_ZH

    async def fake_polish(text, lang, topic="general", title="", instructions=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(apply_bot, "_offertoday_intro", fake_intro)
    monkeypatch.setattr(polish, "polish_intro", fake_polish)

    text, was_polished = asyncio.run(apply_bot._offertoday_intro_polished(row, {}))
    assert text == INTRO_ZH and was_polished is False


def test_intro_polish_disabled_via_settings(db, monkeypatch):
    db.add(Profile(id=1, intro_polish_enabled=False))
    db.commit()
    row = _job(db, job_id_on_platform="intro2")

    async def fake_intro(row_, cfg):
        return INTRO_ZH

    async def fake_polish(*a, **kw):
        raise AssertionError("唔應該 call LLM")

    monkeypatch.setattr(apply_bot, "_offertoday_intro", fake_intro)
    monkeypatch.setattr(polish, "polish_intro", fake_polish)

    text, was_polished = asyncio.run(apply_bot._offertoday_intro_polished(row, {}))
    assert text == INTRO_ZH and was_polished is False


def test_custom_polish_instructions_are_passed(db, monkeypatch):
    db.add(Profile(id=1, intro_polish_instructions="再簡潔一點，唔好超過 90 字"))
    db.commit()
    row = _job(db, job_id_on_platform="intro3")
    seen = {}

    async def fake_intro(row_, cfg):
        return INTRO_ZH

    async def fake_polish(text, lang, topic="general", title="", instructions=""):
        seen["instructions"] = instructions
        return INTRO_ZH

    monkeypatch.setattr(apply_bot, "_offertoday_intro", fake_intro)
    monkeypatch.setattr(polish, "polish_intro", fake_polish)

    asyncio.run(apply_bot._offertoday_intro_polished(row, {}))
    assert seen["instructions"] == "再簡潔一點，唔好超過 90 字"
