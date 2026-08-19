"""Shared Playwright infrastructure: persistent sessions, human delays, block detection."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from playwright.async_api import Page, Playwright, async_playwright

from ..config import settings

log = logging.getLogger(__name__)

BLOCK_MARKERS = (
    "captcha", "recaptcha", "security check", "verify you are human",
    "verify you're human", "access denied", "cf-error", "blocked",
    "驗證碼", "安全驗證", "身份驗證", "請登入", "登入以繼續",
)


@dataclass
class JobDraft:
    """Normalized job record produced by a scraper before persistence."""
    platform: str
    job_id: str
    title: str
    url: str = ""
    company: str = ""
    location: str = ""
    salary_range: str = ""
    jd_text: str = ""
    posted_at: str = ""
    apply_method: str = "form"       # form | external_link | email
    contact_email: str = ""
    contact_person: str = ""
    external_url: str = ""
    raw: dict = field(default_factory=dict)


class BrowserSession:
    """Persistent (logged-in) Chromium context for one platform.

    The browser window stays visible so the human can log in once and
    review every pre-filled application before submitting.
    """

    def __init__(self, platform: str, headless: bool = False, keep_open: bool = False):
        self.platform = platform
        self.headless = headless
        self.keep_open = keep_open
        self.user_data_dir = settings.PROFILES_DIR / platform
        self._pw: Playwright | None = None
        self.context = None

    async def __aenter__(self):
        await self.start()
        return self.context

    async def start(self):
        self._pw = await async_playwright().start()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1366, "height": 900},
            locale="zh-HK",
        )
        return self.context

    async def __aexit__(self, *exc):
        if not self.keep_open:
            await self.close()

    async def close(self):
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:  # noqa: BLE001
                pass
            self.context = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None


# ------------------------------------------------------------------ global
# The app keeps one long-lived visible browser per platform: the user logs in
# once and reviews every pre-filled application in that same window.
_browser_sessions: dict[str, BrowserSession] = {}


async def get_browser(platform: str) -> BrowserSession:
    """Return the long-lived BrowserSession for a platform (creates on first use)."""
    if platform not in _browser_sessions:
        _browser_sessions[platform] = BrowserSession(platform, keep_open=True)
        await _browser_sessions[platform].start()
    return _browser_sessions[platform]


async def close_all_browsers() -> None:
    for s in list(_browser_sessions.values()):
        await s.close()
    _browser_sessions.clear()


async def human_delay(lo: float = 1.0, hi: float = 4.0) -> None:
    """Randomized delay between actions (anti-WAF behavior simulation)."""
    await asyncio.sleep(random.uniform(lo, hi))


async def open_page(context, url: str) -> Page:
    """Open a URL in a fresh tab of the shared context; return the page."""
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    await human_delay(0.8, 2.0)
    return page


def is_blocked(page: Page) -> bool:
    """Best-effort detection of captcha / login-wall / WAF interstitials."""
    try:
        url = page.url.lower()
        title = (page.title() or "").lower()
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=1500)[:2000].lower()
        except Exception:  # noqa: BLE001
            pass
        haystack = f"{url} {title} {body}"
        return any(m in haystack for m in BLOCK_MARKERS)
    except Exception:  # noqa: BLE001
        return False


async def grab_html(page: Page) -> str:
    """Return the rendered page HTML (awaiting network idle-ish quiet)."""
    await human_delay(0.3, 0.9)
    return await page.content()
