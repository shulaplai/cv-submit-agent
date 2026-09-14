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
    category: str = ""               # it | general (set by the scrapers)
    raw: dict = field(default_factory=dict)


class BrowserSession:
    """Browser session for one platform.

    JobsDB / OfferToday connect to the user's REAL Chrome via CDP (their
    existing login state) when available; otherwise fall back to a persistent
    Playwright profile where the user logs in once. gov.hk needs no login.
    """

    CDP_PLATFORMS = ("jobsdb", "offertoday")

    def __init__(self, platform: str, headless: bool = False, keep_open: bool = False):
        self.platform = platform
        self.headless = headless
        self.keep_open = keep_open
        self.user_data_dir = settings.PROFILES_DIR / platform
        self._pw: Playwright | None = None
        self.context = None
        self.using_cdp = False

    async def __aenter__(self):
        await self.start()
        return self.context

    async def start(self):
        if self.platform in self.CDP_PLATFORMS:
            from .cdp_browser import ensure_chrome_window, get_cdp_browser

            # Chrome with every window closed has no browser context at all —
            # open one first, otherwise the CDP attach itself fails.
            await ensure_chrome_window()
            browser = await get_cdp_browser()
            if browser is not None and browser.contexts:
                self.context = browser.contexts[0]
                self.using_cdp = True
                return self.context
        # fallback: dedicated persistent profile (user logs in once)
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
        if self.using_cdp:
            # never close the user's real Chrome — just drop our reference
            self.context = None
            return
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


def session_is_alive(session: BrowserSession | None) -> bool:
    """True when the cached session still has a usable browser context.

    A long-lived session goes stale whenever the dedicated Chrome window is
    closed / re-opened (Playwright then keeps a dead handle). Detecting that
    here is what stops the confusing "Target page, context or browser has been
    closed" error from reaching the user's batch result.
    """
    if session is None or session.context is None:
        return False
    try:
        browser = session.context.browser
        if browser is not None and not browser.is_connected():
            return False
        _ = session.context.pages      # raises once the context is closed
        return True
    except Exception:  # noqa: BLE001
        return False


async def get_browser(platform: str) -> BrowserSession:
    """Return the long-lived BrowserSession for a platform (creates on first use).

    The cached session is health-checked on every call: if Chrome was closed or
    restarted, the stale handle is dropped and a fresh session is built.
    """
    session = _browser_sessions.get(platform)
    if session is not None and not session_is_alive(session):
        log.info("browser session for %s is stale — rebuilding", platform)
        _browser_sessions.pop(platform, None)
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            pass
        session = None
    if session is None:
        session = BrowserSession(platform, keep_open=True)
        await session.start()
        _browser_sessions[platform] = session
    return session


async def repair_browsers() -> None:
    """Drop every cached session + the CDP connection, then rebuild the window.

    One-shot retry path for closed-context failures (user closed the Chrome
    window mid-session).
    """
    for s in list(_browser_sessions.values()):
        _browser_sessions.pop(s.platform, None)
        try:
            await s.close()
        except Exception:  # noqa: BLE001
            pass
    from .cdp_browser import recover_cdp_browser
    await recover_cdp_browser()


async def close_all_browsers() -> None:
    for s in list(_browser_sessions.values()):
        await s.close()
    _browser_sessions.clear()
    from .cdp_browser import close_cdp
    await close_cdp()


async def human_delay(lo: float = 1.0, hi: float = 4.0) -> None:
    """Randomized delay between actions (anti-WAF behavior simulation)."""
    await asyncio.sleep(random.uniform(lo, hi))


async def open_page(context, url: str) -> Page:
    """Open a URL in a fresh tab of the shared context; return the page."""
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    await human_delay(0.8, 2.0)
    return page


async def is_blocked(page: Page) -> bool:
    """Best-effort detection of captcha / login-wall / WAF interstitials."""
    try:
        url = page.url.lower()
        title = ""
        try:
            title = (await page.title() or "").lower()
        except Exception:  # noqa: BLE001
            pass
        body = ""
        try:
            body = (await page.locator("body").inner_text(timeout=1500))[:2000].lower()
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
