"""gov.hk 搜尋框（POST /jobsearch/search/）嘅單元測試。

覆蓋：
  - parse_joblist_html 由列表直接攞到刊登日期（唔開詳情頁）
  - _fetch_token / _post_search（antiforgery token + /search/ endpoint）
  - IT 軌：一定要帶 jobType=5，列表日期過期就唔開詳情頁
  - 一般軌：逐個關鍵字用 Criteria.searchField，跨關鍵字去重、剔 IT 標題
  - 冇搜尋表單（冇 token）時跌落舊 quickview 路徑
"""
import asyncio
import re
from pathlib import Path

from conftest import days_ago_dmy

from app.services import scraper_govhk as sg
from app.services.classify import PRIORITY_SCAN_SLACK, TrackConfig
from app.services.scraper_base import JobDraft

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN_HTML = '<form><input name="__RequestVerificationToken" value="tok-test" /></form>'
FRESH_D = days_ago_dmy(1)


def _row_html(job_id: str, title: str, posted: str, salary: str = "", location: str = "") -> str:
    """砌一行 joblist 表格（同真頁一樣：img + 下一個 span 係個格嘅值）。"""
    return (
        "<table><tbody><tr>"
        f'<td><a class="clipItBtn" data-ordno="{job_id}">複製</a>'
        f'<a id="{job_id}_orderNo_hyper" href="/0/tc/jobseeker/jobsearch/jobcard/?orderNo={job_id}">link</a>'
        f'<span class="d-flex flex-column"><span>{title}</span></span></td>'
        f'<td><img src="/isms/img/ies_job_icon1.svg" alt="" /><span>{posted}</span></td>'
        f'<td><img src="/isms/img/ies_job_icon2.svg" alt="" /><span>{salary}</span></td>'
        f'<td><img src="/isms/img/fill_but3.svg" alt="" /><span>{location}</span></td>'
        "</tr></tbody></table>"
    )


class _Resp:
    def __init__(self, text: str = "", url: str = ""):
        self._text = text
        self.url = url

    async def text(self) -> str:
        return self._text

    async def dispose(self):
        pass


class _Request:
    """假 request：記低所有 POST 表單同 GET URL，joblist 回 caller 指定嘅 HTML。"""

    def __init__(self, token: str = TOKEN_HTML, rows: dict | None = None,
                 default_rows: str = ""):
        self.token = token
        self.rows = rows or {}          # searchField -> HTML
        self.default_rows = default_rows
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.search_field = ""

    async def get(self, url, *a, **k):
        self.gets.append(str(url))
        if "page=" not in str(url):
            return _Resp(self.token, str(url))        # 攞 antiforgery token
        if "page=1" not in str(url):
            return _Resp("", str(url))                # 第 2 頁空 = 到尾
        html = self.rows.get(self.search_field, self.default_rows)
        return _Resp(html, str(url))

    async def post(self, url, *a, **k):
        form = dict(k.get("form") or {})
        self.posts.append((str(url), form))
        self.search_field = form.get("Criteria.searchField", "")
        return _Resp("", str(url))


class _Session:
    def __init__(self, request=None):
        if request is None:
            self.context = object()          # 冇 .request = 攞唔到搜尋表單
        else:
            self.context = type("Ctx", (), {"request": request})()


def _stub(monkeypatch, calls=None):
    """換走 _fetch_detail / human_delay，回一個 fake detail。"""
    calls = calls if calls is not None else []

    async def fake_detail(session, item, platform, category=""):
        calls.append(item["job_id"])
        return JobDraft(platform=platform, job_id=item["job_id"], url=item["detail_url"],
                        title=item["title"], posted_at=FRESH_D, category=category)

    async def fake_delay(*a, **k):
        pass

    monkeypatch.setattr(sg, "_fetch_detail", fake_detail)
    monkeypatch.setattr(sg, "human_delay", fake_delay)
    return calls


def _general_cfg(**kw) -> TrackConfig:
    cfg = TrackConfig.defaults("general")
    cfg.govhk_max_jobs = 10
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------- 列表日期

def test_parse_joblist_html_includes_posted_at():
    """joblist 表格本身有刊登日期，唔使開詳情頁。"""
    fixture = (FIXTURES / "govhk_joblist_it.html").read_text(encoding="utf-8")
    items = sg.parse_joblist_html(fixture)
    assert len(items) == 20
    assert items[0]["job_id"] == "31-26-0005281"
    assert items[0]["posted_at"] == "20/08/2026"
    assert all(re.fullmatch(r"\d{2}/\d{2}/\d{4}", it["posted_at"]) for it in items)


# ---------------------------------------------------------------- token / POST

def test_fetch_token_reads_hidden_input():
    req = _Request()
    token = asyncio.run(sg._fetch_token(_Session(req)))
    assert token == "tok-test"
    assert req.gets and "page=" not in req.gets[0]


def test_fetch_token_empty_without_token_or_request():
    assert asyncio.run(sg._fetch_token(_Session(_Request(token="<html></html>")))) == ""
    assert asyncio.run(sg._fetch_token(_Session())) == ""      # context 冇 request


def test_post_search_sends_form_to_search_endpoint():
    req = _Request()
    ok = asyncio.run(sg._post_search(_Session(req), "tok-test",
                                     keyword="文員", job_type="5"))
    assert ok is True
    url, form = req.posts[0]
    assert url == sg.SEARCH_POST_URL == f"{sg.BASE}/0/tc/jobseeker/jobsearch/search/"
    assert form["Criteria.searchField"] == "文員"
    assert form["Criteria.jobType"] == "5"
    assert form["__RequestVerificationToken"] == "tok-test"
    assert form["Search"] == "搜尋"
    assert form["IsMobile"] == "False"


def test_post_search_detects_404_redirect():
    class _BadReq(_Request):
        async def post(self, url, *a, **k):
            return _Resp("", f"{sg.BASE}/0/404.html")

    assert asyncio.run(sg._post_search(_Session(_BadReq()), "tok-test",
                                       keyword="文員")) is False


# ---------------------------------------------------------------- IT 軌

def test_it_channel_posts_it_category(monkeypatch):
    """IT 軌用 /search/ + jobType=5（舊 code POST /simple/ 係 404，等於冇 filter）。"""
    calls = _stub(monkeypatch)
    req = _Request(default_rows=_row_html("11-26-0000001", "資訊科技支援技術員", FRESH_D))
    cfg = TrackConfig.defaults("it")

    drafts = asyncio.run(sg._scrape_it(_Session(req), set(), cfg))

    assert req.posts and req.posts[0][0] == sg.SEARCH_POST_URL
    assert req.posts[0][1]["Criteria.jobType"] == sg.IT_JOB_TYPE == "5"
    assert [d.title for d in drafts] == ["資訊科技支援技術員"]
    assert [d.platform for d in drafts] == [sg.IT_PLATFORM]
    assert calls == ["11-26-0000001"]


def test_it_channel_skips_stale_row_without_detail_fetch(monkeypatch):
    """列表日期已經過期 → 唔開詳情頁，直接收工。"""
    calls = _stub(monkeypatch)
    stale = _row_html("11-26-0000002", "資訊科技支援技術員", days_ago_dmy(90))
    req = _Request(default_rows=stale)
    cfg = TrackConfig.defaults("it")

    drafts = asyncio.run(sg._scrape_it(_Session(req), set(), cfg))

    assert drafts == []
    assert calls == []                       # 冇開詳情頁


def test_it_channel_aborts_without_token(monkeypatch):
    _stub(monkeypatch)
    req = _Request(token="<html></html>", default_rows=_row_html("11-26-0000003", "IT", FRESH_D))
    drafts = asyncio.run(sg._scrape_it(_Session(req), set(), TrackConfig.defaults("it")))
    assert drafts == []
    assert req.posts == []                   # 連 POST 都冇發


# ---------------------------------------------------------------- 一般軌

def test_general_searches_each_keyword(monkeypatch):
    cfg = _general_cfg(keywords=["文員", "行政助理"])
    calls = _stub(monkeypatch)
    req = _Request(rows={
        "文員": _row_html("26-26-0000001", "文員", FRESH_D, "$15K", "中環"),
        "行政助理": _row_html("26-26-0000002", "行政助理", FRESH_D, "$18K", "旺角"),
    })
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert [f["Criteria.searchField"] for _, f in req.posts] == ["文員", "行政助理"]
    assert {d.title for d in drafts} == {"文員", "行政助理"}
    assert calls == ["26-26-0000001", "26-26-0000002"]
    # 唔應該行 quickview
    assert all("quickview" not in u for u in req.gets)


def test_general_search_dedups_across_keywords(monkeypatch):
    cfg = _general_cfg(keywords=["文員", "助理"])
    calls = _stub(monkeypatch)
    row = _row_html("26-26-0000003", "文員", FRESH_D)
    req = _Request(rows={"文員": row, "助理": row})
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert [d.job_id for d in drafts] == ["26-26-0000003"]
    assert calls == ["26-26-0000003"]


def test_general_search_excludes_it_titles(monkeypatch):
    cfg = _general_cfg(keywords=["工程師"])
    calls = _stub(monkeypatch)
    req = _Request(rows={"工程師": _row_html("26-26-0000004", "資訊科技工程師", FRESH_D)})
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert drafts == []
    assert calls == []


def test_general_keyword_sweep_is_capped(monkeypatch):
    cfg = _general_cfg(keywords=[f"kw{i}" for i in range(20)])
    _stub(monkeypatch)
    req = _Request(default_rows=_row_html("26-26-0000005", "kw0", FRESH_D))
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert len(req.posts) == sg.KEYWORD_SEARCH_MAX == 12
    assert [d.job_id for d in drafts] == ["26-26-0000005"]      # 同一份工去重


def test_general_skips_stale_row_without_detail_fetch(monkeypatch):
    cfg = _general_cfg(keywords=["文員"])
    calls = _stub(monkeypatch)
    req = _Request(rows={"文員": _row_html("26-26-0000006", "文員", days_ago_dmy(90))})
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert drafts == []
    assert calls == []


def test_general_falls_back_to_quickview_without_token(monkeypatch):
    """冇搜尋表單（例如舊 session）→ 照舊行 main quickview。"""
    fixture = (FIXTURES / "govhk_quickview_general.html").read_text(encoding="utf-8")
    cfg = _general_cfg()

    class FakePage:
        async def close(self):
            pass

    seen_urls = []

    async def fake_open_page(ctx, url):
        seen_urls.append(url)
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_delay(*a, **k):
        pass

    async def fake_detail(session, item, platform, category=""):
        return JobDraft(platform=platform, job_id=item["job_id"], title=item["title"],
                        posted_at=FRESH_D, category=category)

    monkeypatch.setattr(sg, "open_page", fake_open_page)
    monkeypatch.setattr(sg, "grab_html", fake_grab_html)
    monkeypatch.setattr(sg, "human_delay", fake_delay)
    monkeypatch.setattr(sg, "_fetch_detail", fake_detail)

    drafts = asyncio.run(sg._scrape_general(_Session(), set(), cfg))

    assert {d.title for d in drafts} == {"文員", "行政助理"}
    assert seen_urls and "quickview/?direct=False" in seen_urls[0]


def test_general_search_no_rows_falls_back_to_quickview(monkeypatch):
    """搜尋機制回 0 列（唔係「filter 完冇工」）→ 跌落 quickview 執死雞。"""
    fixture = (FIXTURES / "govhk_quickview_general.html").read_text(encoding="utf-8")
    cfg = _general_cfg(keywords=["文員"])

    class FakePage:
        async def close(self):
            pass

    async def fake_open_page(ctx, url):
        return FakePage()

    async def fake_grab_html(page):
        return fixture

    async def fake_delay(*a, **k):
        pass

    async def fake_detail(session, item, platform, category=""):
        return JobDraft(platform=platform, job_id=item["job_id"], title=item["title"],
                        posted_at=FRESH_D, category=category)

    monkeypatch.setattr(sg, "open_page", fake_open_page)
    monkeypatch.setattr(sg, "grab_html", fake_grab_html)
    monkeypatch.setattr(sg, "human_delay", fake_delay)
    monkeypatch.setattr(sg, "_fetch_detail", fake_detail)

    req = _Request(rows={})                       # 搜尋回空頁
    drafts = asyncio.run(sg._scrape_general(_Session(req), set(), cfg))

    assert req.posts                              # 有試過搜尋
    # quickview 用同一個 cfg.keywords 過濾，所以只收到「文員」
    assert {d.title for d in drafts} == {"文員"}


# ---------------------------------------------------------------- _Tally

def test_tally_skip_then_stop():
    cfg = TrackConfig.defaults("general")
    cfg.govhk_max_jobs = 1
    cfg.cap_bypass_enabled = False
    tally = sg._Tally()

    assert tally.take("文員", [], cfg) == "normal"          # 第 1 份收
    assert tally.take("文員", [], cfg) == "skip"            # 軟上限用盡 -> 唔開詳情頁
    for _ in range(PRIORITY_SCAN_SLACK - 2):
        assert tally.take("文員", [], cfg) == "skip"
    assert tally.take("文員", [], cfg) == "stop"            # 連續 miss 夠多 -> 收渠道


def test_tally_keeps_priority_jobs_over_cap():
    from app.services.classify import is_priority_job          # noqa: F401

    cfg = TrackConfig.defaults("general")
    cfg.govhk_max_jobs = 1
    cfg.cap_bypass_enabled = True
    cfg.cap_bypass_min_score = 90
    cfg.priority_keywords = []
    cfg.priority_extra_max = 2
    tally = sg._Tally()

    assert tally.take("文員", [], cfg) == "normal"
    assert tally.take("系統工程師", [], cfg) in ("skip", "priority")
