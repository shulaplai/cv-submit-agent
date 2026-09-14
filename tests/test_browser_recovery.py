"""Tests for browser self-healing.

Real-world failure this guards: the dedicated Chrome window gets closed while
the server keeps running. The cached Playwright handle dies and every apply
failed with "BrowserContext.new_page: Target page, context or browser has been
closed". These tests cover the detection + rebuild logic (no real Chrome).
"""
import asyncio

import pytest

from app.services import apply_bot, cdp_browser, scraper_base


# ---------------------------------------------------------------- cdp layer

class _FakeBrowser:
    def __init__(self, connected=True, contexts=None):
        self._connected = connected
        self.contexts = contexts if contexts is not None else [object()]

    def is_connected(self):
        return self._connected


def test_get_cdp_browser_reconnects_when_handle_is_stale(monkeypatch):
    """A disconnected cached browser must be dropped and reconnected."""
    stale = _FakeBrowser(connected=False)
    fresh = _FakeBrowser(connected=True)
    monkeypatch.setattr(cdp_browser, "_browser", stale)

    class FakeChromium:
        async def connect_over_cdp(self, url):
            return fresh

    class FakePW:
        chromium = FakeChromium()

        async def stop(self):
            pass

    async def fake_start():
        return FakePW()

    monkeypatch.setattr(cdp_browser, "async_playwright", lambda: _Starter(fake_start))

    got = asyncio.run(cdp_browser.get_cdp_browser())
    assert got is fresh
    assert cdp_browser._browser is fresh


def test_get_cdp_browser_reuses_live_handle(monkeypatch):
    live = _FakeBrowser(connected=True)
    monkeypatch.setattr(cdp_browser, "_browser", live)

    async def boom():
        raise AssertionError("must not reconnect while the handle is alive")

    monkeypatch.setattr(cdp_browser, "async_playwright", lambda: _Starter(boom))
    got = asyncio.run(cdp_browser.get_cdp_browser())
    assert got is live


class _Starter:
    """Mimics ``async_playwright()`` returning an object with ``.start()``."""

    def __init__(self, start):
        self._start = start

    def __call__(self):
        return self

    async def start(self):
        return await self._start()


def test_ensure_chrome_window_opens_tab_when_none_open(monkeypatch):
    """Chrome alive but with zero windows -> open one via CDP."""
    calls = {"opened": 0, "tabs": 0}

    def open_window(url="about:blank"):
        calls["opened"] += 1
        calls["tabs"] = 1        # the new tab becomes visible to CDP
        return True

    monkeypatch.setattr(cdp_browser, "_cdp_page_count", lambda: calls["tabs"])
    monkeypatch.setattr(cdp_browser, "open_chrome_window", open_window)
    assert asyncio.run(cdp_browser.ensure_chrome_window()) is True
    assert calls["opened"] == 1


def test_ensure_chrome_window_noop_when_window_exists(monkeypatch):
    monkeypatch.setattr(cdp_browser, "_cdp_page_count", lambda: 3)
    monkeypatch.setattr(cdp_browser, "open_chrome_window",
                        lambda url="about:blank": pytest.fail("must not open a tab"))
    assert asyncio.run(cdp_browser.ensure_chrome_window()) is True


# ------------------------------------------------------------- session layer

class _FakeContext:
    def __init__(self, alive=True, browser=None):
        self.alive = alive
        self.browser = browser
        self._pages = []

    @property
    def pages(self):
        if not self.alive:
            raise RuntimeError("Target page, context or browser has been closed")
        return self._pages


class _FakeSession:
    def __init__(self, context):
        self.platform = "offertoday"
        self.context = context
        self.using_cdp = True
        self.closed = False

    async def close(self):
        self.closed = True


def test_session_is_alive_variants():
    assert scraper_base.session_is_alive(None) is False
    assert scraper_base.session_is_alive(_FakeSession(None)) is False
    assert scraper_base.session_is_alive(_FakeSession(_FakeContext(alive=False))) is False
    assert scraper_base.session_is_alive(
        _FakeSession(_FakeContext(browser=_FakeBrowser(connected=False)))) is False
    assert scraper_base.session_is_alive(_FakeSession(_FakeContext())) is True


def test_get_browser_rebuilds_stale_session(monkeypatch):
    stale = _FakeSession(_FakeContext(alive=False))
    built = []

    class FakeBrowserSession:
        def __init__(self, platform, keep_open=False):
            self.platform = platform
            self.context = _FakeContext()
            self.using_cdp = True
            built.append(self)

        async def start(self):
            return self.context

        async def close(self):
            pass

    monkeypatch.setattr(scraper_base, "BrowserSession", FakeBrowserSession)
    monkeypatch.setitem(scraper_base._browser_sessions, "offertoday", stale)

    got = asyncio.run(scraper_base.get_browser("offertoday"))
    assert got is built[0]                       # rebuilt
    assert scraper_base._browser_sessions["offertoday"] is got
    assert stale.closed is True                  # old handle dropped


# --------------------------------------------------------------- apply layer

def test_is_closed_error_matches_playwright_messages():
    assert apply_bot._is_closed_error(
        RuntimeError("BrowserContext.new_page: Target page, context or browser has been closed"))
    assert apply_bot._is_closed_error(
        RuntimeError("BrowserType.connect_over_cdp: Protocol error: "
                     "Browser context management is not supported"))
    assert not apply_bot._is_closed_error(RuntimeError("timeout 45000ms exceeded"))


def test_open_job_page_repairs_and_retries_once(monkeypatch):
    """First new_page() fails with a closed context -> repair, retry, succeed."""
    repaired = {"n": 0}

    class FakePage:
        def __init__(self):
            self.urls = []

        async def goto(self, url, **kwargs):
            self.urls.append(url)

    page = FakePage()

    class FakeCtx:
        def __init__(self, fail):
            self._fail = fail

        async def new_page(self):
            if self._fail:
                raise RuntimeError("Target page, context or browser has been closed")
            return page

    class FakeSession:
        def __init__(self, fail):
            self.context = FakeCtx(fail)

    state = {"call": 0}

    async def fake_get_browser(platform):
        state["call"] += 1
        return FakeSession(fail=state["call"] == 1)

    async def fake_repair():
        repaired["n"] += 1

    monkeypatch.setattr(apply_bot, "get_browser", fake_get_browser)
    monkeypatch.setattr(scraper_base, "repair_browsers", fake_repair)

    result = asyncio.run(apply_bot._open_job_page("offertoday", "https://example.test/job"))
    assert result is page
    assert repaired["n"] == 1
    assert state["call"] == 2


def test_open_job_page_does_not_swallow_other_errors(monkeypatch):
    class FakeCtx:
        async def new_page(self):
            raise RuntimeError("Timeout 45000ms exceeded")

    class FakeSession:
        context = FakeCtx()

    async def fake_get_browser(platform):
        return FakeSession()

    async def fake_repair():
        pytest.fail("must not repair for unrelated errors")

    monkeypatch.setattr(apply_bot, "get_browser", fake_get_browser)
    monkeypatch.setattr(scraper_base, "repair_browsers", fake_repair)

    with pytest.raises(RuntimeError):
        asyncio.run(apply_bot._open_job_page("offertoday", "https://example.test/job"))


def test_offertoday_job_closed_detection():
    class FakePage:
        def __init__(self, body, badge=0):
            self._body = body
            self._badge = badge

        async def inner_text(self, sel):
            return self._body

        def locator(self, sel):
            badge = self._badge

            class L:
                @property
                def first(self):
                    return self

                async def count(self):
                    return badge

                async def is_visible(self):
                    return badge > 0

            return L()

    closed = FakePage("Data Center Operator 已關閉 今日回覆過候選人")
    assert asyncio.run(apply_bot._offertoday_job_closed(closed)) is True

    open_job = FakePage("Data Center Operator 招聘中 申請")
    assert asyncio.run(apply_bot._offertoday_job_closed(open_job)) is False

    badge_only = FakePage("普通職位", badge=1)
    assert asyncio.run(apply_bot._offertoday_job_closed(badge_only)) is True


def test_friendly_browser_error_messages():
    """Raw Playwright strings never reach the batch result verbatim."""
    hint = apply_bot.friendly_browser_error(
        RuntimeError("BrowserContext.new_page: Target page, context or browser has been closed"))
    assert hint and "Chrome" in hint and "再撳一次" in hint

    cdp = apply_bot.friendly_browser_error(
        RuntimeError("BrowserType.connect_over_cdp: ECONNREFUSED 127.0.0.1:9222"))
    assert cdp and "9222" in cdp

    assert apply_bot.friendly_browser_error(RuntimeError("Timeout 45000ms exceeded")) is None
