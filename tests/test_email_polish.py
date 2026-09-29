"""Email 內文潤色：輸出檢查（稱呼／簽名保留、唔加事實、語言、長度、字眼）。"""
import asyncio

import pytest

from app.services import polish
from app.services.polish import PolishError, polish_email_body, validate_email_polish

ORIGINAL_ZH = """陳先生 您好，

我係一名有三年經驗嘅全端開發員，主力用 React 同 Laravel。我亦都做過 WhatsApp API 整合嘅 SaaS 平台。
我係一名有三年經驗嘅全端開發員，主力用 React 同 Laravel，做過整合平台。

希望可以加入貴公司，貢獻我嘅前後端經驗。

謝謝。

Lai Shu Lap
lai@example.com"""


def test_validate_accepts_a_good_polish():
    good = """陳先生 您好，

我是一名擁有三年經驗的全端開發員，主力使用 React 與 Laravel，並曾開發整合 WhatsApp API 的 SaaS 平台。

希望能夠加入貴公司，貢獻前後端開發經驗。

謝謝。

Lai Shu Lap
lai@example.com"""
    assert validate_email_polish(ORIGINAL_ZH, good, "zh") == []


def test_validate_flags_changed_greeting():
    bad = ORIGINAL_ZH.replace("陳先生 您好，", "敬啟者：", 1)
    problems = validate_email_polish(ORIGINAL_ZH, bad, "zh")
    assert any("稱呼" in p for p in problems)


def test_validate_flags_missing_signature_email():
    bad = ORIGINAL_ZH.replace("lai@example.com", "")
    problems = validate_email_polish(ORIGINAL_ZH, bad, "zh")
    assert any("署名" in p or "簽名" in p for p in problems)


def test_validate_flags_too_short_and_too_long():
    short = "陳先生 您好，\n\n見附件。\n\nLai Shu Lap\nlai@example.com"
    assert any("刪得太多" in p for p in validate_email_polish(ORIGINAL_ZH, short, "zh"))
    long = ORIGINAL_ZH + "補充" * 200
    assert any("太長" in p for p in validate_email_polish(ORIGINAL_ZH, long, "zh"))


def test_validate_flags_language_mismatch():
    english_only = ("Dear Mr Chan,\n\n" + "I am a full stack developer. " * 12
                    + "\n\nlai@example.com")
    problems = validate_email_polish(ORIGINAL_ZH, english_only, "zh")
    assert any("語言" in p for p in problems)


def test_validate_flags_ai_self_reference():
    bad = ORIGINAL_ZH.replace("謝謝。", "本信由 AI 助手潤色。")
    assert any("AI" in p for p in validate_email_polish(ORIGINAL_ZH, bad, "zh"))


def test_validate_skips_tiny_bodies():
    assert validate_email_polish("hi", "hello", "en") == []


def test_polish_email_body_returns_model_output(monkeypatch):
    good = ORIGINAL_ZH.replace("我係一名", "我是一名")

    async def fake_chat(messages, temperature=0.3):
        assert "稱呼" in messages[0]["content"]          # prompt 有鐵律
        assert ORIGINAL_ZH[:20] in messages[1]["content"]
        return good

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    out = asyncio.run(polish_email_body(ORIGINAL_ZH, "zh", {"title": "全端開發員"}))
    assert out == good.strip()


def test_polish_email_body_rejects_bad_model_output(monkeypatch):
    async def fake_chat(messages, temperature=0.3):
        return "敬啟者：\n\n短。\n\n（AI 助手潤色）"

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    with pytest.raises(PolishError):
        asyncio.run(polish_email_body(ORIGINAL_ZH, "zh"))


def test_polish_email_body_raises_on_empty(monkeypatch):
    with pytest.raises(PolishError):
        asyncio.run(polish_email_body("   ", "zh"))


def test_polish_email_body_wraps_llm_errors(monkeypatch):
    from app.services.llm import LLMError

    async def fake_chat(messages, temperature=0.3):
        raise LLMError("no key")

    monkeypatch.setattr(polish.llm_svc, "chat", fake_chat)
    with pytest.raises(PolishError):
        asyncio.run(polish_email_body(ORIGINAL_ZH, "zh"))


def test_polish_cache_key_changes_with_content_and_template():
    from app.services.email_bot import polish_cache_key

    a = polish_cache_key("CL A", "standard", "zh")
    b = polish_cache_key("CL B", "standard", "zh")
    c = polish_cache_key("CL A", "formal", "zh")
    assert a != b and a != c and a == polish_cache_key("CL A", "standard", "zh")
