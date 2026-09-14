"""CV 版本選擇：AI 職位交 AI 版；冇 AI 版 -> Full-stack 版；
連 Full-stack 都冇 -> Developer 版；全部都冇 -> 通用版（cv_en / cv_zh）。

用戶選擇：只睇「職位標題」判斷 AI；中英各一份；OfferToday 用「揀履歷」檔名認版本。
"""
import asyncio

import pytest

from app.services import cv_loader
from app.services.cv_loader import (
    filename_matches_variant,
    resolve_cv_for_job,
    title_is_ai,
    variant_preference,
    variant_for_filename,
)


# ------------------------------------------------------------ AI 標題判斷

@pytest.mark.parametrize("title", [
    "AI Engineer",
    "AI Agent Developer",
    "大模型算法工程師",
    "機器學習工程師",
    "Senior LLM Engineer",
    "Computer Vision Engineer",
    "AI Specialist",
    "生成式 AI 研究員",
])
def test_ai_titles(title):
    assert title_is_ai(title) is True


@pytest.mark.parametrize("title", [
    "Web Developer",
    "Email Marketing Officer",     # 含 "ai" 但唔係 AI（email）
    "Data Center Operator",
    "文員",
    "IT Support Technician",
    "Detail Checker",              # 含 "ai"（detail）
    "Sales Executive",
])
def test_non_ai_titles(title):
    assert title_is_ai(title) is False


def test_ai_detection_is_title_only():
    """JD 內文有 AI 字眼都唔會影響（只睇標題）。"""
    assert title_is_ai("Clerk") is False


def test_variant_preference_order():
    assert variant_preference("AI Engineer") == ["ai", "fullstack", "developer"]
    assert variant_preference("Web Developer") == ["fullstack", "developer"]


# --------------------------------------------------------- 版本檔案選擇

def _fake_profile(monkeypatch, **paths):
    class P:
        pass

    prof = P()
    for key, val in paths.items():
        setattr(prof, key, val)
    monkeypatch.setattr(cv_loader, "_profile", lambda: prof)
    # 清走 .env 設定，避免干擾
    for name in ("CV_EN_PATH", "CV_ZH_PATH", "CV_AI_EN_PATH", "CV_AI_ZH_PATH",
                 "CV_FULLSTACK_EN_PATH", "CV_FULLSTACK_ZH_PATH",
                 "CV_DEVELOPER_EN_PATH", "CV_DEVELOPER_ZH_PATH"):
        monkeypatch.setattr(cv_loader.settings, name, "")


def test_ai_job_prefers_ai_cv(monkeypatch):
    _fake_profile(monkeypatch, cv_en_path="generic_en.pdf", cv_ai_en_path="ai_en.pdf",
                  cv_fullstack_en_path="fs_en.pdf")
    assert resolve_cv_for_job("AI Engineer", "en") == ("ai_en.pdf", "ai")


def test_ai_job_falls_back_to_fullstack_when_no_ai_cv(monkeypatch):
    _fake_profile(monkeypatch, cv_en_path="generic_en.pdf",
                  cv_fullstack_en_path="fs_en.pdf", cv_developer_en_path="dev_en.pdf")
    assert resolve_cv_for_job("AI Engineer", "en") == ("fs_en.pdf", "fullstack")


def test_ai_job_falls_back_to_developer(monkeypatch):
    _fake_profile(monkeypatch, cv_en_path="generic_en.pdf", cv_developer_en_path="dev_en.pdf")
    assert resolve_cv_for_job("大模型算法工程師", "en") == ("dev_en.pdf", "developer")


def test_ai_job_falls_back_to_generic(monkeypatch):
    _fake_profile(monkeypatch, cv_en_path="generic_en.pdf")
    assert resolve_cv_for_job("AI Engineer", "en") == ("generic_en.pdf", "default")


def test_non_ai_job_prefers_fullstack(monkeypatch):
    _fake_profile(monkeypatch, cv_en_path="generic_en.pdf", cv_ai_en_path="ai_en.pdf",
                  cv_fullstack_en_path="fs_en.pdf")
    assert resolve_cv_for_job("Web Developer", "en") == ("fs_en.pdf", "fullstack")


def test_non_ai_job_uses_developer_when_no_fullstack(monkeypatch):
    _fake_profile(monkeypatch, cv_zh_path="generic_zh.pdf", cv_ai_zh_path="ai_zh.pdf",
                  cv_developer_zh_path="dev_zh.pdf")
    assert resolve_cv_for_job("行政助理", "zh") == ("dev_zh.pdf", "developer")


def test_language_is_respected(monkeypatch):
    _fake_profile(monkeypatch, cv_ai_en_path="ai_en.pdf", cv_ai_zh_path="ai_zh.pdf")
    assert resolve_cv_for_job("AI Engineer", "zh") == ("ai_zh.pdf", "ai")
    assert resolve_cv_for_job("AI Engineer", "en") == ("ai_en.pdf", "ai")


def test_env_paths_used_when_profile_empty(monkeypatch):
    _fake_profile(monkeypatch)
    monkeypatch.setattr(cv_loader.settings, "CV_AI_EN_PATH", "/tmp/env_ai_en.pdf")
    assert resolve_cv_for_job("AI Engineer", "en") == ("/tmp/env_ai_en.pdf", "ai")


# ------------------------------------------------- OfferToday 履歷檔名

@pytest.mark.parametrize("filename,variant", [
    ("cv_en_ai.pdf", "ai"),
    ("Lai_Shu_Lap_CV_AI.pdf", "ai"),
    ("cv_en_fullstack.pdf", "fullstack"),
    ("Lai CV Full Stack EN.pdf", "fullstack"),
    ("CV_Full_Stack_ZH.pdf", "fullstack"),
    ("Lai_Developer_CV.pdf", "developer"),
    ("cv_en_dev.pdf", "developer"),
])
def test_resume_filename_variant(filename, variant):
    assert filename_matches_variant(filename, variant) is True


@pytest.mark.parametrize("filename", ["Lai_CV.pdf", "random_resume.pdf", "Lai_Shu_Lap.pdf"])
def test_resume_filename_unknown_variant(filename):
    """'Lai' 唔可以誤中 'ai'。"""
    assert variant_for_filename(filename) == ""


def test_lai_name_not_treated_as_ai():
    assert filename_matches_variant("Lai_Shu_Lap_CV.pdf", "ai") is False
    assert filename_matches_variant("Lai_Shu_Lap_CV_AI.pdf", "ai") is True


def test_offertoday_pick_prefers_ai_resume_for_ai_job():
    """OfferToday「揀履歷」：AI 職位要揀 AI 版履歷（檔名比對）。"""
    from app.services.apply_bot import _offertoday_pick_cv

    picked: list[str] = []

    class FakeLocator:
        def __init__(self, names):
            self.names = names

        @property
        def first(self):
            return self

        async def count(self):
            return len(self.names)

        def nth(self, i):
            return FakeItem(self.names[i], picked)

    class FakeItem:
        def __init__(self, name, sink):
            self.name = name
            self.sink = sink

        async def inner_text(self):
            return self.name

        async def click(self, timeout=None):
            self.sink.append(self.name)

    class FakePage:
        def __init__(self):
            self.items = FakeLocator(["Lai_CV_EN.pdf", "Lai_CV_AI.pdf", "Lai_CV_FullStack.pdf"])

        def locator(self, sel):
            if sel.startswith("[role='dialog']"):
                return self.items

            class Btn:
                @property
                def first(self):
                    return self

                async def count(self):
                    return 1

                async def click(self, timeout=None):
                    return None

            class FB:
                @property
                def first(self):
                    return self

                async def count(self):
                    return 1

                async def click(self, timeout=None):
                    return None

            return FB()

    result = asyncio.run(_offertoday_pick_cv(FakePage(), "en", title="AI Engineer"))
    assert result == "lai_cv_ai.pdf"          # 回傳係細楷檔名
    assert picked == ["Lai_CV_AI.pdf"]        # 但撳嘅係原本檔名


def test_offertoday_pick_prefers_fullstack_for_plain_job():
    from app.services.apply_bot import _offertoday_pick_cv

    picked: list[str] = []

    class FakeLocator:
        def __init__(self, names):
            self.names = names

        @property
        def first(self):
            return self

        async def count(self):
            return len(self.names)

        def nth(self, i):
            return FakeItem(self.names[i], picked)

    class FakeItem:
        def __init__(self, name, sink):
            self.name = name
            self.sink = sink

        async def inner_text(self):
            return self.name

        async def click(self, timeout=None):
            self.sink.append(self.name)

    class FakePage:
        def __init__(self):
            self.items = FakeLocator(["Lai_CV_AI.pdf", "Lai_CV_FullStack.pdf"])

        def locator(self, sel):
            if sel.startswith("[role='dialog']"):
                return self.items

            class Btn:
                @property
                def first(self):
                    return self

                async def count(self):
                    return 1

                async def click(self, timeout=None):
                    return None

            return Btn()

    result = asyncio.run(_offertoday_pick_cv(FakePage(), "en", title="Web Developer"))
    assert result == "lai_cv_fullstack.pdf"


# ------------------------------------------------- CV 版本上載（設定頁）

def test_upload_variant_cv_endpoint(client, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    r = client.post(
        "/api/profile/cv",
        data={"kind": "ai_en"},
        files={"file": ("my_ai_cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200
    assert r.json()["cv_ai_en_path"].endswith("cv_ai_en.pdf")
    assert (tmp_path / "cvs" / "cv_ai_en.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_upload_cv_rejects_unknown_kind(client):
    r = client.post(
        "/api/profile/cv",
        data={"kind": "nope_en"},
        files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400


def test_profile_roundtrip_keeps_variant_paths(client):
    r = client.put("/api/profile", json={"cv_developer_zh_path": "/tmp/dev_zh.pdf"})
    assert r.status_code == 200
    assert r.json()["cv_developer_zh_path"] == "/tmp/dev_zh.pdf"
    assert client.get("/api/profile").json()["cv_developer_zh_path"] == "/tmp/dev_zh.pdf"
