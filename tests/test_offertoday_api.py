"""OfferToday 列表 JSON：唔開詳情都知日期。

用戶要求：OfferToday 嘅工要「唔開詳情都知日期」，先可以喺開詳情之前篩走過期工。
主要路徑係喺搜尋頁攔截佢自己打嘅 ``/wapi/geek/recommend/search/list`` 回應
（``_watch_list_api`` / ``_absorb_listing`` / ``_apply_listing``）；
另外仲有一條選用嘅直接 API 路徑（``_api_scrape``，``OFFERTODAY_API_ENABLED``）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from app.services import scraper_offertoday as so
from app.services.classify import TrackConfig

LIST_URL = f"{so.BASE}/wapi/geek/recommend/search/list"


# ------------------------------------------------------------------ helpers
def _item(token, title, post="剛剛", company="Acme Ltd",
          salary="$20K-30K/月", skills=("Python", "Docker")):
    return {
        "cardType": 0,
        "jobId": token,
        "jobName": title,
        "companyName": company,
        "salaryDesc": salary,
        "skills": list(skills),
        "experience": "3-5年",
        "educationDesc": "學士",
        "locationDesc": "香港",
        "jobPostTime": post,
        "jobFunctions": [{"code": "118000", "name": "資訊科技"}],
    }


def _cfg(**kw):
    base = dict(name="it", label="IT", keywords=["developer"], it_keywords=["developer"],
                govhk_max_jobs=0, offertoday_max_per_search=0, priority_extra_max=0)
    base.update(kw)
    return TrackConfig(**base)


class FakeContext:
    """假 BrowserContext：記住掛咗嘅 response listener（同真 API 一樣要移除）。"""

    def __init__(self):
        self.handlers = []

    def on(self, event, handler):
        self.handlers.append((event, handler))

    def remove_listener(self, event, handler):
        self.handlers = [h for h in self.handlers if h != (event, handler)]

    async def fire(self, url, body):
        for event, handler in list(self.handlers):
            if event == "response":
                await handler(FakeResponse(url, body))


class FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def json(self):
        return self._body


# ------------------------------------------------------- parse_post_time
def test_parse_post_time_absolute_mmdd():
    today = date(2026, 10, 6)
    assert so.parse_post_time("發布於10-05", today=today) == "2026-10-05"
    assert so.parse_post_time("更新於09-10", today=today) == "2026-09-10"
    assert so.parse_post_time("發布於01-05", today=date(2026, 12, 20)) == "2026-01-05"
    assert so.parse_post_time("發布於12-28", today=date(2026, 1, 3)) == "2025-12-28"


def test_parse_post_time_relative_and_unknown():
    today = date(2026, 10, 6)
    assert so.parse_post_time("剛剛", today=today) == "2026-10-06"
    assert so.parse_post_time("發布於今天", today=today) == "2026-10-06"
    assert so.parse_post_time("更新於昨天", today=today) == "2026-10-05"
    assert so.parse_post_time("3日前", today=today) == "2026-10-03"
    assert so.parse_post_time("更新於3個月前", today=today) == \
        (today - timedelta(days=90)).isoformat()
    assert so.parse_post_time("更新於近3個月", today=today) == \
        (today - timedelta(days=90)).isoformat()
    assert so.parse_post_time("更新於很久以前", today=today) == ""
    assert so.parse_post_time("", today=today) == ""


# --------------------------------------------------------- 攔截 helper
def test_absorb_listing_skips_banner_and_dedupes():
    store = {}
    banner = dict(_item("b1", "banner"), cardType=1)
    body = {"data": {"resultList": [banner, _item("t1", "A"), _item("t2", "B")]}}
    assert so._absorb_listing(body, store) == 2
    assert set(store) == {"t1", "t2"}
    assert so._absorb_listing(body, store) == 0
    assert len(store) == 2
    assert so._absorb_listing({"zpData": {"resultList": [_item("t9", "C")]}}, store) == 1
    assert so._absorb_listing({"data": {}}, store) == 0
    assert so._absorb_listing({}, store) == 0
    assert so._absorb_listing(None, store) == 0


def test_watch_and_unwatch_list_api():
    ctx = FakeContext()
    store = {}
    handler = so._watch_list_api(ctx, store)
    assert handler is not None and len(ctx.handlers) == 1
    assert so._watch_list_api(object(), store) is None
    so._unwatch_list_api(ctx, handler)
    assert ctx.handlers == []
    so._unwatch_list_api(None, handler)
    so._unwatch_list_api(ctx, None)


def test_apply_listing_fills_date_company_salary():
    d = so.JobDraft(platform="offertoday", job_id="t1", title="Web Developer",
                    url="u", raw={"card_text": "x"})
    today = date.today()
    assert so._apply_listing(d, _item("t1", "Web Developer", post="剛剛",
                                      company="Acme Ltd", salary="$30K-40K/月")) is True
    assert d.posted_at == today.isoformat()
    assert d.company == "Acme Ltd"
    assert d.salary_range == "$30K-40K/月"
    assert d.location == "香港"
    assert d.raw["listing"] is True
    assert d.raw["post_time_text"] == "剛剛"
    assert d.raw["skills"] == ["Python", "Docker"]
    assert d.raw["card_text"] == "x"
    d2 = so.JobDraft(platform="offertoday", job_id="t2", title="X", url="u",
                     company="原本公司", posted_at="2026-01-01")
    assert so._apply_listing(d2, None) is False
    assert so._apply_listing(d2, {"jobPostTime": "很久以前"}) is False
    assert d2.company == "原本公司"
    assert d2.posted_at == "2026-01-01"
    # 卡片文字捉到嘅「薪金」其實係經驗值（冇「$」）→ 用列表值蓋過
    d3 = so.JobDraft(platform="offertoday", job_id="t3", title="X", url="u",
                     salary_range="3-5")
    so._apply_listing(d3, _item("t3", "X", salary="$30K-40K/月"))
    assert d3.salary_range == "$30K-40K/月"
    # 卡片已經有真薪金（有「$」）→ 保留卡片值
    d4 = so.JobDraft(platform="offertoday", job_id="t4", title="X", url="u",
                     salary_range="HK$20K-30K/月")
    so._apply_listing(d4, _item("t4", "X", salary="$30K-40K/月"))
    assert d4.salary_range == "HK$20K-30K/月"


# --------------------------------------------- scrape(): 攔截 + DOM 路徑
class FakeLink:
    def __init__(self, token, title, card_text="HK$20K-30K/月"):
        self.href = f"/hk/job/{token}"
        self.title = title
        self.card_text = card_text

    async def get_attribute(self, name):
        return self.href if name == "href" else None

    async def inner_text(self):
        return self.title

    async def evaluate(self, fn):
        return self.card_text

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


class FakeSession:
    def __init__(self, context=None):
        self.context = context if context is not None else object()


def _patch_dom(monkeypatch, ctx, links):
    async def fake_open_page(_ctx, _url):
        return FakePage(links)

    async def fake_scroll(_page, _target):
        await ctx.fire(LIST_URL, {"data": {"resultList": [
            _item("tokA", "Web Developer", post="3日前")]}})

    async def fake_delay(*_a, **_k):
        return None

    monkeypatch.setattr(so, "open_page", fake_open_page)
    monkeypatch.setattr(so, "_scroll_search", fake_scroll)
    monkeypatch.setattr(so, "human_delay", fake_delay)


def test_scrape_gets_date_from_intercepted_listing(monkeypatch):
    ctx = FakeContext()
    _patch_dom(monkeypatch, ctx, [FakeLink("tokA", "Web Developer")])
    drafts = asyncio.run(so.scrape(FakeSession(ctx), track="it", cfg=_cfg()))

    assert [d.job_id for d in drafts] == ["tokA"]
    d = drafts[0]
    assert d.posted_at == (date.today() - timedelta(days=3)).isoformat()
    assert d.company == "Acme Ltd"
    # 卡片文字已經有薪金 → 保留卡片值（_apply_listing 唔會覆蓋已有值）
    assert d.salary_range == "HK$20K-30K/月"
    assert d.raw["post_time_text"] == "3日前"
    assert ctx.handlers == []


def test_scrape_without_event_api_still_works(monkeypatch):
    ctx = FakeContext()
    _patch_dom(monkeypatch, ctx, [FakeLink("tokA", "Web Developer")])
    drafts = asyncio.run(so.scrape(FakeSession(object()), track="it", cfg=_cfg()))
    assert [d.job_id for d in drafts] == ["tokA"]
    assert drafts[0].posted_at == ""


def test_scrape_soft_cap_and_priority_bypass(monkeypatch):
    ctx = FakeContext()
    links = ([FakeLink(f"n{i}", "Web Developer") for i in range(6)]
             + [FakeLink(f"p{i}", "AI Developer") for i in range(4)])
    _patch_dom(monkeypatch, ctx, links)
    monkeypatch.setattr(so, "_search_targets",
                        lambda _cfg: [(f"{so.BASE}/hk/search/x-jobs", "category", False)])
    cfg = _cfg(keywords=["developer"], priority_keywords=["ai"],
               offertoday_max_per_search=3, priority_extra_max=2, cap_bypass_enabled=True)
    drafts = asyncio.run(so.scrape(FakeSession(ctx), track="it", cfg=cfg))
    titles = [d.title for d in drafts]
    assert titles.count("Web Developer") == 3
    assert titles.count("AI Developer") == 2


# --------------------------------------------------- 選用：直接打 API
def test_api_targets_map_category_codes_and_keywords():
    cfg = _cfg(offertoday_search_terms=["devops", "data engineer"],
               ai_search_terms=["AI Agent"])
    targets = so._api_targets(cfg)
    cats = [t for t in targets if t.codes]
    kws = [t for t in targets if t.keyword]
    assert [t.codes for t in cats] == [("118000",), ("112000",), ("127000",)]
    assert all(t.query == "category" for t in cats)
    assert [t.keyword for t in kws] == ["AI Agent", "devops", "data engineer"]
    assert [t.is_ai_group for t in kws] == [True, False, False]


def test_api_payload_shape():
    tgt = so._ApiTarget(referer="https://x/", query="category", codes=("118000",))
    p = so._api_payload(tgt, 3)
    assert p["page"] == 3 and p["jobFunctionCodes"] == ["118000"] and p["keyword"] == ""
    assert p["pageSize"] == so.API_PAGE_SIZE and p["rcdType"] == 7


class FakeApiPage:
    def __init__(self, results):
        self.results = results
        self.calls = []
        self.closed = False

    async def evaluate(self, _fn, args):
        payload = args[1]
        self.calls.append(payload)
        out = self.results(payload)
        if out == "429":
            return {"status": 429, "text": ""}
        if isinstance(out, str):
            return {"status": 200, "text": out}
        return {"status": 200, "text": json.dumps({"code": 0, "msg": "Success", "data": out})}

    async def close(self):
        self.closed = True


class FakeApiSession:
    using_cdp = True

    def __init__(self):
        self.context = object()


def _patch_api(monkeypatch, page):
    async def fake_open_page(_ctx, _url):
        return page

    async def fake_delay(*_a, **_k):
        return None

    monkeypatch.setattr(so, "open_page", fake_open_page)
    monkeypatch.setattr(so, "human_delay", fake_delay)
    monkeypatch.setattr(so, "API_RETRY_WAITS", (0.0, 0.0, 0.0, 0.0))


def test_api_scrape_builds_drafts_with_date(monkeypatch):
    today = date.today()

    def results(_p):
        return {"resultList": [_item("tok1", "Web Developer", post="剛剛"),
                               _item("tok2", "AI Developer", post="3日前")],
                "total": 2, "hasMore": False}

    page = FakeApiPage(results)
    _patch_api(monkeypatch, page)
    drafts = asyncio.run(so._api_scrape(FakeApiSession(), "it", _cfg(), []))

    by_id = {d.job_id: d for d in drafts}
    assert set(by_id) == {"tok1", "tok2"}
    assert by_id["tok1"].posted_at == today.isoformat()
    assert by_id["tok2"].posted_at == (today - timedelta(days=3)).isoformat()
    assert by_id["tok1"].company == "Acme Ltd"
    assert by_id["tok1"].salary_range == "$20K-30K/月"
    assert by_id["tok1"].url == f"{so.BASE}/hk/job/tok1"
    assert by_id["tok1"].raw["post_time_text"] == "剛剛"
    assert page.closed is True


def test_api_scrape_drops_non_matching_titles(monkeypatch):
    def results(_p):
        return {"resultList": [_item("tok1", "Web Developer"),
                               _item("tok2", "Property Agent"),
                               _item("tok3", "Java Developer")],
                "total": 3, "hasMore": False}

    _patch_api(monkeypatch, FakeApiPage(results))
    drafts = asyncio.run(so._api_scrape(FakeApiSession(), "it", _cfg(), []))
    # 「Property Agent」唔命中 keyword「developer」→ 唔收
    assert [d.title for d in drafts] == ["Web Developer", "Java Developer"]


def test_api_scrape_general_track_excludes_it(monkeypatch):
    def results(_p):
        return {"resultList": [_item("tok1", "Cashier"), _item("tok2", "Web Developer")],
                "total": 2, "hasMore": False}

    _patch_api(monkeypatch, FakeApiPage(results))
    cfg = _cfg(name="general", label="一般", keywords=["cashier"], it_keywords=["developer"])
    drafts = asyncio.run(so._api_scrape(FakeApiSession(), "general", cfg, []))
    assert [d.title for d in drafts] == ["Cashier"]
    assert drafts[0].category == "general"


def test_api_scrape_paginates_until_has_more_false(monkeypatch):
    def results(payload):
        if payload["page"] == 1:
            return {"resultList": [_item("a1", "Web Developer")], "total": 2, "hasMore": True}
        return {"resultList": [_item("a2", "Backend Developer")], "total": 2, "hasMore": False}

    page = FakeApiPage(results)
    _patch_api(monkeypatch, page)
    drafts = asyncio.run(so._api_scrape(FakeApiSession(), "it", _cfg(), []))
    assert [d.job_id for d in drafts] == ["a1", "a2"]
    # 每個 target 都由 page 1 開始揭（3 個分類 target → 1,2,1,1）
    assert [c["page"] for c in page.calls[:2]] == [1, 2]


def test_api_scrape_retries_on_429(monkeypatch):
    state = {"n": 0, "out": []}

    def results(_p):
        state["n"] += 1
        state["out"].append("429" if state["n"] < 3 else "ok")
        if state["n"] < 3:
            return "429"
        return {"resultList": [_item("tok1", "Web Developer")], "total": 1, "hasMore": False}

    _patch_api(monkeypatch, FakeApiPage(results))
    drafts = asyncio.run(so._api_scrape(FakeApiSession(), "it", _cfg(), []))
    assert [d.job_id for d in drafts] == ["tok1"]
    # 頭兩次 429 → 退避重試 → 第三次成功
    assert state["out"][:3] == ["429", "429", "ok"]


def test_scrape_uses_api_when_enabled(monkeypatch):
    calls = {"dom": 0}

    async def fake_api(_session, _track, _cfg, _skills):
        return [so.JobDraft(platform="offertoday", job_id="api1", title="Web Developer",
                            posted_at="2026-10-05")]

    def boom(*_a, **_k):
        calls["dom"] += 1
        raise AssertionError("API 有貨時唔應該行 DOM 路徑")

    monkeypatch.setattr(so.settings, "OFFERTODAY_API_ENABLED", True)
    monkeypatch.setattr(so, "_api_scrape", fake_api)
    monkeypatch.setattr(so, "_search_targets", boom)
    drafts = asyncio.run(so.scrape(FakeApiSession(), "it", cfg=_cfg()))
    assert [d.job_id for d in drafts] == ["api1"]
    assert calls["dom"] == 0


def test_scrape_skips_api_by_default(monkeypatch):
    calls = {"api": 0}
    ctx = FakeContext()

    async def fake_api(*_a, **_k):
        calls["api"] += 1
        return []

    monkeypatch.setattr(so.settings, "OFFERTODAY_API_ENABLED", False)
    monkeypatch.setattr(so, "_api_scrape", fake_api)
    _patch_dom(monkeypatch, ctx, [FakeLink("tokA", "Web Developer")])
    drafts = asyncio.run(so.scrape(FakeApiSession(), "it", cfg=_cfg()))
    assert [d.job_id for d in drafts] == ["tokA"]
    assert calls["api"] == 0


def test_scrape_falls_back_to_dom_when_api_empty(monkeypatch):
    async def fake_api(*_a, **_k):
        return []

    monkeypatch.setattr(so.settings, "OFFERTODAY_API_ENABLED", True)
    monkeypatch.setattr(so, "_api_scrape", fake_api)
    ctx = FakeContext()
    _patch_dom(monkeypatch, ctx, [FakeLink("tokA", "Web Developer")])
    drafts = asyncio.run(so.scrape(FakeApiSession(), "it", cfg=_cfg()))
    assert [d.job_id for d in drafts] == ["tokA"]
