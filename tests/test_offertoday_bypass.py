"""OfferToday：高分／AI 職位無視每頁上限（用戶要求）。

驗證：
  - 軟上限只計非優先工；優先工額外最多 priority_extra_max 份
  - 軟上限滿之後唔會再為普通工開卡片文字（慳 CDP round-trip）
  - 連續好多個都唔係優先工就收手（唔會白揭成頁）
  - scroll 目標 = 軟上限 x2 + 豁免額（否則捲唔到豁免工）
"""
import asyncio

from app.config import settings
from app.services import scraper_offertoday
from app.services.classify import PRIORITY_SCAN_SLACK, TrackConfig

CALLS: dict = {}


class FakeLink:
    def __init__(self, token, title):
        self.token, self.title = token, title

    async def get_attribute(self, name):
        CALLS["get_attribute"] = CALLS.get("get_attribute", 0) + 1
        return f"/hk/job/{self.token}"

    async def inner_text(self):
        return self.title

    async def evaluate(self, fn):
        CALLS["evaluate"] = CALLS.get("evaluate", 0) + 1
        return self.title

    async def count(self):
        return 1


class FakeLocator:
    def __init__(self, links):
        self.links = links

    async def count(self):
        return len(self.links)

    def nth(self, i):
        return self.links[i]


class FakePage:
    def __init__(self, links):
        self._links = links

    def locator(self, sel):
        return FakeLocator(self._links)

    async def close(self):
        pass


def _harness(monkeypatch, titles, cap, extra, bypass=True):
    CALLS.clear()
    cfg = TrackConfig(
        name="it", label="IT", keywords=["developer"], it_keywords=["developer"],
        govhk_max_jobs=0, offertoday_max_per_search=cap,
        cap_bypass_enabled=bypass, cap_bypass_min_score=70,
        priority_keywords=["ai", "人工智能"], priority_extra_max=extra,
    )
    links = [FakeLink(f"tok{i}", t) for i, t in enumerate(titles)]

    async def fake_open_page(ctx, url):
        return FakePage(links)

    async def fake_scroll(page, target):
        CALLS["scroll_target"] = target

    async def fake_human_delay(*a, **k):
        return None

    monkeypatch.setattr(scraper_offertoday, "_search_urls_for", lambda cfg_: ["https://x/it"])
    monkeypatch.setattr(scraper_offertoday, "open_page", fake_open_page)
    monkeypatch.setattr(scraper_offertoday, "_scroll_search", fake_scroll)
    monkeypatch.setattr(scraper_offertoday, "human_delay", fake_human_delay)

    class FakeSession:
        context = object()

    drafts = asyncio.run(scraper_offertoday.scrape(FakeSession(), track="it", cfg=cfg))
    return drafts


def test_offertoday_priority_jobs_bypass_per_search_cap(monkeypatch):
    drafts = _harness(monkeypatch,
                      ["Web Developer"] * 3 + ["AI Developer"] * 4,
                      cap=3, extra=2)
    titles = [d.title for d in drafts]
    assert titles.count("Web Developer") == 3      # 軟上限
    assert titles.count("AI Developer") == 2       # 豁免額
    assert len(drafts) == 5


def test_offertoday_skips_card_text_for_capped_non_priority(monkeypatch):
    """軟上限滿咗之後，普通工唔應該再開卡片文字（evaluate）。"""
    drafts = _harness(monkeypatch,
                      ["Web Developer"] * 3 + ["Web Developer"] * 5,
                      cap=3, extra=5)
    assert len(drafts) == 3
    assert CALLS["evaluate"] == 3                  # 只有收咗嘅 3 份先抽卡片文字


def test_offertoday_stops_after_slack_of_non_priority(monkeypatch):
    """連續 PRIORITY_SCAN_SLACK 個都唔係優先工 -> 唔再逐個 link 檢查。"""
    titles = ["Web Developer"] * 3 + ["Web Developer"] * 400
    drafts = _harness(monkeypatch, titles, cap=3, extra=5)
    assert len(drafts) == 3
    # 檢查過嘅 link 數量應該遠少於 400（軟上限 3 + slack）
    assert CALLS["get_attribute"] <= 3 + PRIORITY_SCAN_SLACK + 2


def test_offertoday_scroll_target_covers_the_priority_extra(monkeypatch):
    _harness(monkeypatch, ["Web Developer"] * 2, cap=10, extra=15)
    assert CALLS["scroll_target"] == 10 * 2 + 15


def test_offertoday_bypass_off_respects_cap(monkeypatch):
    drafts = _harness(monkeypatch, ["AI Developer"] * 6, cap=2, extra=5, bypass=False)
    assert len(drafts) == 2


def test_offertoday_no_cap_means_no_extra_needed(monkeypatch):
    """cap = 0（唔設限）-> 全部收，豁免額唔關事。"""
    drafts = _harness(monkeypatch, ["Web Developer"] * 4 + ["AI Developer"] * 3,
                      cap=0, extra=0)
    assert len(drafts) == 7
