"""apply_bot tests: message-field filling, CV attach, submit clicking, confirmation.

Uses a minimal fake Playwright Page/Locator so the logic is tested without a
real browser.
"""
from __future__ import annotations

import pytest

from app.services.apply_bot import (
    _attach_cv,
    _auto_submit_platform,
    _click_submit,
    _confirm_submitted,
    _fill_message_field,
    _is_it_job,
    _jobsdb,
    _offertoday,
    _offertoday_cv_matches,
    _offertoday_intro,
    _offertoday_pick_cv,
    _prefill_platform,
)


# ------------------------------------------------------------------ fake Playwright


class FakeElem:
    def __init__(self, *, text="", visible=True, tag="button", checked=False,
                 files=None, label="", after_click=None):
        self.text = text
        self.visible = visible
        self.tag = tag
        self.checked = checked
        self.files = files or []
        self.label = label          # what _radio_label's evaluate returns
        self.value = ""
        self.clicked = False
        self.after_click = after_click


class FakeLocator:
    def __init__(self, elems):
        self._elems = elems

    @property
    def first(self):
        return FakeLocator(self._elems[:1])

    @property
    def last(self):
        return FakeLocator(self._elems[-1:] if self._elems else [])

    def nth(self, i):
        return FakeLocator([self._elems[i]] if 0 <= i < len(self._elems) else [])

    async def count(self):
        return len(self._elems)

    async def is_visible(self):
        return bool(self._elems) and self._elems[0].visible

    async def fill(self, text):
        if self._elems:
            self._elems[0].value = text

    async def set_input_files(self, path):
        if self._elems:
            self._elems[0].files = [str(path)]

    async def click(self, timeout=None):
        if self._elems:
            self._elems[0].clicked = True
            if self._elems[0].after_click:
                self._elems[0].after_click()

    async def check(self):
        if self._elems:
            self._elems[0].checked = True

    async def get_attribute(self, name):
        if not self._elems:
            return None
        return getattr(self._elems[0], name, None)

    async def evaluate(self, fn, arg=None):
        return self._elems[0].label if self._elems else ""

    async def scroll_into_view_if_needed(self, timeout=None):
        pass

    async def inner_text(self, timeout=None):
        return self._elems[0].text if self._elems else ""


class FakePage:
    def __init__(self, url="https://example.com/job/apply"):
        self.url = url
        self.frames: list = []
        self._reg: dict[str, list[FakeElem]] = {}
        self._body = ""

    def locator(self, sel):
        if sel == "body":
            # body content comes from _body and is read fresh on every call
            return FakeLocator([FakeElem(text=self._body)])
        return FakeLocator(list(self._reg.get(sel, [])))

    def add(self, sel, elem):
        self._reg.setdefault(sel, []).append(elem)

    async def goto(self, url, **kw):
        self.url = url

    async def title(self):
        return ""

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def inner_text(self, sel, timeout=None):
        return self._body

    async def evaluate(self, fn, arg=None):
        return False  # JS-click fallback never fires in fakes


class FakeRow:
    platform = "jobsdb"
    apply_method = "form"
    jd_language = "en"
    url = "https://hk.jobsdb.com/job/123"
    external_url = ""
    title = "AI Engineer"
    company = "ACME"
    location = "Hong Kong"
    salary_range = ""
    jd_text = "jd"


def _success_page():
    """Page whose body switches to a success marker once the submit is clicked."""
    page = FakePage(url="https://hk.jobsdb.com/job/123/apply")
    submit = FakeElem(text="Submit application", after_click=lambda: setattr(page, "_body", "Your application has been submitted successfully"))
    page.add("button:has-text('Submit application')", submit)
    return page


# ------------------------------------------------------------------ message field


async def test_fill_message_field_textarea():
    page = FakePage()
    ta = FakeElem(tag="textarea")
    page.add("textarea", ta)
    note = await _fill_message_field(page, "hello 世界")
    assert note == "已填 CL/訊息"
    assert ta.value == "hello 世界"


async def test_fill_message_field_contenteditable():
    page = FakePage()
    el = FakeElem(tag="div")
    page.add("[contenteditable='true']", el)
    note = await _fill_message_field(page, "hello")
    assert "富文本" in note
    assert el.value == "hello"


async def test_fill_message_field_iframe():
    page = FakePage()
    frame = FakePage(url="about:blank")
    fta = FakeElem(tag="textarea")
    frame.add("textarea", fta)
    page.frames = [frame]
    note = await _fill_message_field(page, "hello")
    assert "iframe" in note
    assert fta.value == "hello"


async def test_fill_message_field_nothing():
    page = FakePage()
    assert await _fill_message_field(page, "hello") == ""


# ------------------------------------------------------------------ CV attach


async def test_attach_cv_file_input():
    page = FakePage()
    fi = FakeElem(tag="input")
    page.add("input[type='file']", fi)
    ok, note = await _attach_cv(page, "/tmp/my_cv.pdf")
    assert ok
    assert "已上傳 CV" in note
    assert fi.files == ["/tmp/my_cv.pdf"]


async def test_attach_cv_radio_by_filename():
    page = FakePage()
    r1 = FakeElem(tag="input", label="My CV.pdf")
    r2 = FakeElem(tag="input", label="Cover letter only")
    page.add("input[type='radio']", r1)
    page.add("input[type='radio']", r2)
    ok, note = await _attach_cv(page, "/tmp/My CV.pdf")
    assert ok
    assert r1.checked and not r2.checked
    assert "已揀 CV" in note


async def test_attach_cv_radio_by_keyword():
    page = FakePage()
    r1 = FakeElem(tag="input", label="選擇履歷表")
    page.add("input[type='radio']", r1)
    ok, _ = await _attach_cv(page, "/tmp/resume_2025.pdf")
    assert ok
    assert r1.checked


async def test_attach_cv_radio_no_match_does_not_pick_blindly():
    page = FakePage()
    r1 = FakeElem(tag="input", label="不用附上文件")
    page.add("input[type='radio']", r1)
    ok, note = await _attach_cv(page, "/tmp/my_cv.pdf")
    assert not ok
    assert not r1.checked
    assert "認唔到" in note


async def test_attach_cv_no_field():
    page = FakePage()
    ok, note = await _attach_cv(page, "/tmp/my_cv.pdf")
    assert not ok
    assert "未揀到 CV" in note


# ------------------------------------------------------------------ submit + confirm


async def test_click_submit_finds_button():
    page = _success_page()
    clicked = await _click_submit(page, max_rounds=1)
    assert clicked
    assert page._reg["button:has-text('Submit application')"][0].clicked


async def test_click_submit_type_submit_fallback():
    page = FakePage()
    page.add("button[type='submit'], input[type='submit']", FakeElem(tag="button"))
    assert await _click_submit(page, max_rounds=1)
    assert page._reg["button[type='submit'], input[type='submit']"][0].clicked


async def test_click_submit_none():
    page = FakePage()
    assert not await _click_submit(page, max_rounds=1)


async def test_click_submit_stops_after_success():
    page = _success_page()
    clicked = await _click_submit(page, max_rounds=2)
    assert clicked
    # success marker visible after the first click -> must NOT click a 2nd time
    assert page._reg["button:has-text('Submit application')"][0].clicked


async def test_confirm_success_markers():
    page = FakePage()
    page._body = "Thank you! Your application has been submitted successfully."
    confirmed, err = await _confirm_submitted(page, quick=True)
    assert confirmed
    assert err == ""


async def test_confirm_error_markers():
    page = FakePage()
    page._body = "Please fill in the required fields before submitting."
    confirmed, err = await _confirm_submitted(page, quick=True)
    assert not confirmed
    assert "required" in err


async def test_confirm_url_marker():
    page = FakePage(url="https://hk.jobsdb.com/apply-success")
    confirmed, _ = await _confirm_submitted(page, quick=True)
    assert confirmed


# ------------------------------------------------------------------ platform flows


async def test_auto_submit_aborts_without_cl():
    page = FakePage()
    res = await _auto_submit_platform(page, FakeRow(), "")
    assert res["ok"] and not res["submitted"]
    assert "Cover Letter" in res["message"]


async def test_auto_submit_aborts_without_cv(monkeypatch):
    from app.services import apply_bot

    monkeypatch.setattr(apply_bot, "_cv_path_for", lambda lang: "/tmp/cv_en.pdf")
    monkeypatch.setattr(apply_bot, "_cv_exists", lambda p: False)
    page = FakePage()
    res = await _auto_submit_platform(page, FakeRow(), "CL text")
    assert res["ok"] and not res["submitted"]
    assert "CV" in res["message"]


async def test_auto_submit_happy_path(monkeypatch):
    from app.services import apply_bot

    monkeypatch.setattr(apply_bot, "_cv_path_for", lambda lang: "/tmp/cv_en.pdf")
    monkeypatch.setattr(apply_bot, "_cv_exists", lambda p: True)

    page = FakePage(url="https://hk.jobsdb.com/job/123/apply")
    ta = FakeElem(tag="textarea")
    page.add("textarea", ta)
    fi = FakeElem(tag="input")
    page.add("input[type='file']", fi)
    page.add("button:has-text('Submit application')", FakeElem(text="Submit application"))

    res = await _auto_submit_platform(page, FakeRow(), "CL text")
    assert res["ok"]
    assert res["submitted"] is True
    assert "已填 CL" in res["message"]
    assert "已上傳 CV" in res["message"]


async def test_auto_submit_detects_form_error(monkeypatch):
    from app.services import apply_bot

    monkeypatch.setattr(apply_bot, "_cv_path_for", lambda lang: "/tmp/cv_en.pdf")
    monkeypatch.setattr(apply_bot, "_cv_exists", lambda p: True)

    page = FakePage(url="https://hk.jobsdb.com/job/123/apply")
    page.add("textarea", FakeElem(tag="textarea"))
    page.add("input[type='file']", FakeElem(tag="input"))
    page.add("button:has-text('Submit application')",
             FakeElem(text="Submit application",
                      after_click=lambda: setattr(page, "_body", "Error: please fill in required fields")))
    res = await _auto_submit_platform(page, FakeRow(), "CL text")
    assert res["submitted"] is False
    assert "錯誤" in res["message"] or "failed" == res["kind"]


async def test_prefill_attaches_cv(monkeypatch):
    from app.services import apply_bot

    monkeypatch.setattr(apply_bot, "_cv_path_for", lambda lang: "/tmp/cv_en.pdf")
    monkeypatch.setattr(apply_bot, "_cv_exists", lambda p: True)

    page = FakePage(url="https://hk.jobsdb.com/job/123/apply")
    ta = FakeElem(tag="textarea")
    page.add("textarea", ta)
    page.add("input[type='file']", FakeElem(tag="input"))

    res = await _prefill_platform(page, FakeRow(), "CL text", "JobsDB")
    assert res["ok"]
    assert res["submitted"] is False
    assert res["kind"] == "form"
    assert "已填 CL" in res["message"]
    assert "已上傳 CV" in res["message"]
    assert "review" in res["message"]


async def test_jobsdb_semauto_attaches_cv(monkeypatch):
    from app.services import apply_bot

    page = FakePage(url="https://hk.jobsdb.com/job/123/apply")
    page.add("a[data-automation='job-detail-apply'], a[data-automation*='apply']",
             FakeElem(tag="a", label="Apply"))
    page.add("textarea", FakeElem(tag="textarea"))
    page.add("input[type='file']", FakeElem(tag="input"))

    class FakeContext:
        async def new_page(self):
            return page

    class FakeSession:
        context = FakeContext()

    async def fake_get_browser(platform):
        return FakeSession()

    monkeypatch.setattr(apply_bot, "get_browser", fake_get_browser)
    monkeypatch.setattr(apply_bot, "_cv_path_for", lambda lang: "/tmp/cv_en.pdf")
    monkeypatch.setattr(apply_bot, "_cv_exists", lambda p: True)

    res = await _jobsdb(FakeRow(), "CL text", auto=False)
    assert res["ok"]
    assert res["submitted"] is False
    assert res["kind"] == "form"
    assert "已填 CL" in res["message"]
    assert "已上傳 CV" in res["message"]


# ------------------------------------------------------------------ OfferToday


def test_offertoday_cv_matches_language():
    assert _offertoday_cv_matches("lai_shu_lap_fullstack.pdf", "en") is True
    assert _offertoday_cv_matches("lai_shulap_cv_zh.pdf", "zh") is True
    assert _offertoday_cv_matches("cv_chinese.pdf", "zh") is True
    assert _offertoday_cv_matches("lai_shu_lap_fullstack.pdf", "zh") is False
    assert _offertoday_cv_matches("cv_chinese.pdf", "en") is False
    # no zh marker -> treated as English by default
    assert _offertoday_cv_matches("my_cv_2025.pdf", "en") is True


async def test_offertoday_pick_cv_by_language():
    page = FakePage()
    page.add("button:has-text('發履歷')", FakeElem(text="發履歷"))
    en = FakeElem(text="lai_shu_lap_fullstack.pdf")
    zh = FakeElem(text="lai_shulap_cv_zh.pdf")
    page.add("[role='dialog'] p.MuiTypography-noWrap", en)
    page.add("[role='dialog'] p.MuiTypography-noWrap", zh)

    picked = await _offertoday_pick_cv(page, "zh")
    assert picked == "lai_shulap_cv_zh.pdf"
    assert zh.clicked and not en.clicked


async def test_offertoday_pick_cv_no_button():
    page = FakePage()
    assert await _offertoday_pick_cv(page, "zh") == ""


def _offertoday_fake_page():
    page = FakePage(url="https://www.offertoday.com/hk/job/abc")
    page.add("#J_apply", FakeElem(tag="button", text="傳送訊息"))
    page.add("[contenteditable='true']", FakeElem(tag="div"))
    page.add("button:has-text('發履歷')", FakeElem(text="發履歷"))
    page.add("[role='dialog'] p.MuiTypography-noWrap", FakeElem(text="lai_shu_lap_fullstack.pdf"))
    page.add("[role='dialog'] p.MuiTypography-noWrap", FakeElem(text="lai_shulap_cv_zh.pdf"))
    page.add("[role='dialog'] button:has-text('發送')", FakeElem(text="發送"))
    page.add("button.MuiButton-contained:has-text('發送')", FakeElem(text="發送"))
    return page


async def test_offertoday_semauto_picks_cv(monkeypatch):
    from app.services import apply_bot

    page = _offertoday_fake_page()

    class FakeContext:
        async def new_page(self):
            return page

    class FakeSession:
        context = FakeContext()

    async def fake_get_browser(platform):
        return FakeSession()

    monkeypatch.setattr(apply_bot, "get_browser", fake_get_browser)

    row = FakeRow()
    row.platform = "offertoday"
    row.jd_language = "zh"

    res = await _offertoday(row, "CL text", auto=False)
    assert res["ok"]
    assert res["submitted"] is False
    assert res["kind"] == "form"
    assert "lai_shulap_cv_zh" in res["message"]


async def test_offertoday_auto_sends(monkeypatch):
    from app.services import apply_bot

    page = _offertoday_fake_page()

    class FakeContext:
        async def new_page(self):
            return page

    class FakeSession:
        context = FakeContext()

    async def fake_get_browser(platform):
        return FakeSession()

    monkeypatch.setattr(apply_bot, "get_browser", fake_get_browser)
    async def fake_gen_intro(lang, is_it):
        return "AI_GENERATED_INTRO_IT_EN"
    monkeypatch.setattr(apply_bot, "generate_after_cv_intro", fake_gen_intro)

    row = FakeRow()
    row.platform = "offertoday"
    row.jd_language = "en"
    row.title = "AI Engineer"

    res = await _offertoday(row, "CL text", auto=True)
    assert res["submitted"] is True
    assert res["kind"] == "submitted"
    assert "lai_shu_lap_fullstack" in res["message"]
    # CV sent via dialog 發送
    assert page._reg["[role='dialog'] button:has-text('發送')"][0].clicked
    # self-intro typed into contenteditable and sent via chat 發送
    ce = page._reg["[contenteditable='true']"][0]
    assert ce.value == "AI_GENERATED_INTRO_IT_EN"
    assert page._reg["button.MuiButton-contained:has-text('發送')"][0].clicked


def test_is_it_job():
    r0 = FakeRow()
    r0.title = "全職銷售助理"
    r0.jd_text = "負責門店銷售、客戶服務。"
    assert _is_it_job(r0) is False

    r1 = FakeRow()
    r1.title = "AI Engineer (Python)"
    assert _is_it_job(r1) is True

    r3 = FakeRow()
    r3.title = "資訊科技工程師"
    assert _is_it_job(r3) is True


async def test_offertoday_intro_it_vs_general(monkeypatch):
    from app.services import apply_bot

    cfg = {"intro_it_zh": "", "intro_it_en": "", "intro_general_zh": "", "intro_general_en": ""}

    # saved value wins (no AI call)
    cfg_saved = dict(cfg, intro_general_zh="我嘅自訂一般簡介")
    gen_row = FakeRow()
    gen_row.title = "Customer Service Officer"
    gen_row.jd_language = "zh"
    assert await _offertoday_intro(gen_row, cfg_saved) == "我嘅自訂一般簡介"

    # empty -> AI-generated (mock the LLM)
    async def fake_gen(lang, is_it):
        return "AI_IT_ZH" if is_it else "AI_GENERAL_ZH"
    monkeypatch.setattr(apply_bot, "generate_after_cv_intro", fake_gen)

    it_row = FakeRow()
    it_row.title = "Software Engineer"
    it_row.jd_language = "zh"
    assert await _offertoday_intro(it_row, cfg) == "AI_IT_ZH"

    gen_row2 = FakeRow()
    gen_row2.title = "Customer Service Officer"
    gen_row2.jd_language = "zh"
    assert await _offertoday_intro(gen_row2, cfg) == "AI_GENERAL_ZH"

    # AI fails -> fall back to default template
    async def fail_gen(lang, is_it):
        raise RuntimeError("llm down")
    monkeypatch.setattr(apply_bot, "generate_after_cv_intro", fail_gen)
    fallback = await _offertoday_intro(gen_row2, cfg)
    assert len(fallback) > 20 and "程式" not in fallback
