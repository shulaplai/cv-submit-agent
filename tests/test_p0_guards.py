"""P0 安全守門：唔可以重複投遞、唔可以寄垃圾畀僱主、AI intro 要讀到 CV。"""
import asyncio
from datetime import datetime, timezone

import pytest

from app.services import apply_bot


class Row:
    id = 501
    platform = "offertoday"
    apply_method = "form"
    jd_language = "zh"
    jd_text = "JD"
    url = "https://www.offertoday.com/hk/job/x"
    external_url = ""
    title = "AI Engineer"
    company = "例子公司"
    dup_key = ""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------- 1. 已投遞唔可以再投

async def test_open_apply_refuses_already_applied():
    row = Row(status="applied", applied_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc))
    res = await apply_bot.open_apply(row, "CL", auto=True)
    assert res["submitted"] is False
    assert res["kind"] == "already_applied"
    assert "已經投過" in res["message"]


async def test_open_apply_refuses_while_in_progress():
    row = Row(status="pending_review")
    apply_bot._applying_ids.add(row.id)
    try:
        res = await apply_bot.open_apply(row, "CL", auto=True)
    finally:
        apply_bot._applying_ids.discard(row.id)
    assert res["submitted"] is False
    assert res["kind"] == "in_progress"
    assert "正在投遞中" in res["message"]


async def test_open_apply_releases_id_after_run(monkeypatch):
    """跑完（成功或拋錯）都要放返個 id，唔可以卡死。"""
    row = Row(status="pending_review")

    async def boom(*a, **kw):
        raise RuntimeError("browser died")

    monkeypatch.setattr(apply_bot, "_open_apply_inner", boom)
    with pytest.raises(RuntimeError):
        await apply_bot.open_apply(row, "CL", auto=True)
    assert row.id not in apply_bot._applying_ids


# ---------------------------------------------- 2. AI intro 要讀到 CV

def test_generate_after_cv_intro_uses_cv_text(monkeypatch):
    """以前漏傳 title -> NameError 被吞 -> 生成時冇履歷內容。"""
    import app.services.apply_bot as ab
    import app.services.cv_loader as cvl
    import app.services.llm as llm

    seen = {}

    def fake_get_cv_text(lang, title=""):
        seen["lang"], seen["title"] = lang, title
        return "履歷內容：做過 RAG、AI agent"

    async def fake_chat(messages, **kw):
        seen["user"] = messages[-1]["content"]
        return "我係 AI Agent 工程師"

    monkeypatch.setattr(cvl, "get_cv_text", fake_get_cv_text)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(cvl, "load_skills", lambda: ["Python"])

    out = asyncio.run(ab.generate_after_cv_intro("zh", "ai", title="AI Engineer"))
    assert out == "我係 AI Agent 工程師"
    assert seen["title"] == "AI Engineer"          # title 真係傳到落去
    assert "RAG" in seen["user"]                   # 履歷內容真係入到 prompt


def test_settings_generation_picks_cv_by_topic(monkeypatch):
    """設定頁生成（冇 job title）都要用對應版本嘅 CV。"""
    import app.services.apply_bot as ab
    import app.services.cv_loader as cvl
    import app.services.llm as llm

    seen = {}
    monkeypatch.setattr(cvl, "get_cv_text",
                        lambda lang, title="": seen.setdefault("title", title) or "CV")
    monkeypatch.setattr(cvl, "load_skills", lambda: [])

    async def fake_chat(messages, **kw):
        return "X"
    monkeypatch.setattr(llm, "chat", fake_chat)

    asyncio.run(ab.generate_after_cv_intro("zh", "ai"))
    assert seen["title"] == "AI Engineer"
    seen.clear()
    asyncio.run(ab.generate_after_cv_intro("zh", "it"))
    assert seen["title"] == "Software Developer"


# ---------------------------------------------- 3. 揀履歷降級要報警

async def test_offertoday_pick_cv_reports_fallback(monkeypatch):
    """指定版本搵唔到而要降級 -> info["fallback"] = True（上層會出警告）。"""
    import app.services.apply_bot as ab

    class Elem:
        def __init__(self, text):
            self.text = text
            self.clicked = False

        async def count(self):
            return 1

        async def inner_text(self):
            return self.text

        async def click(self, timeout=None):
            self.clicked = True

    class Loc:
        def __init__(self, items):
            self.items = items

        @property
        def first(self):
            return self.items[0]

        @property
        def last(self):
            return self.items[-1]

        def nth(self, i):
            return self.items[i]

        async def count(self):
            return len(self.items)

    class Page:
        url = "x"

        def __init__(self):
            self.btn = Elem("發履歷")
            self.items = [Elem("Lai_Resume.pdf")]

        def locator(self, sel):
            return Loc([self.btn]) if "發履歷" in sel else Loc(self.items)

    info = {}
    picked = await ab._offertoday_pick_cv(Page(), "zh", plan=[("AI 版", "AI")], info=info)
    assert picked == "lai_resume.pdf"
    assert info["fallback"] is True                # 有降級 -> 上層會出警告
    assert info["variant_label"] == ""


async def test_offertoday_pick_cv_no_warning_on_exact_hit():
    """搵到指定版本 -> 唔應該出警告。"""
    import app.services.apply_bot as ab

    class Elem:
        def __init__(self, text):
            self.text = text
            self.clicked = False

        async def count(self):
            return 1

        async def inner_text(self):
            return self.text

        async def click(self, timeout=None):
            self.clicked = True

    class Loc:
        def __init__(self, items):
            self.items = items

        @property
        def first(self):
            return self.items[0]

        @property
        def last(self):
            return self.items[-1]

        def nth(self, i):
            return self.items[i]

        async def count(self):
            return len(self.items)

    class Page:
        url = "x"

        def __init__(self):
            self.btn = Elem("發履歷")
            self.ai = Elem("Lai_AI.pdf")
            self.general = Elem("Lai_中文.pdf")
            self.items = [self.general, self.ai]

        def locator(self, sel):
            return Loc([self.btn]) if "發履歷" in sel else Loc(self.items)

    page = Page()
    info = {}
    picked = await ab._offertoday_pick_cv(
        page, "zh", plan=[("AI 版", "AI"), ("一般中文版", "中文")], info=info)
    assert picked == "lai_ai.pdf"
    assert page.ai.clicked is True and page.general.clicked is False
    assert info["fallback"] is False
    assert info["variant_label"] == "AI 版"
