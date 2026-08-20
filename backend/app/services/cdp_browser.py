"""Connect to a real Chrome (dedicated profile) via CDP.

JobsDB / OfferToday automation runs inside a real Chrome window with a
DEDICATED profile (Chrome 136+ ignores --remote-debugging-port on the default
profile, so the user's main Chrome cannot be remote-controlled). The user logs
into JobsDB/OfferToday once in that window; the session persists in the
dedicated profile. Their normal Chrome is never touched.
"""
from __future__ import annotations

import logging
import os
import subprocess

from playwright.async_api import async_playwright

from ..config import settings

log = logging.getLogger(__name__)

_pw = None
_browser = None


def profile_dir() -> str:
    return os.path.expanduser(settings.CHROME_PROFILE_DIR)


async def get_cdp_browser():
    """Return the CDP-connected browser or None (never raises)."""
    global _pw, _browser
    if _browser is not None:
        return _browser
    try:
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.connect_over_cdp(settings.CHROME_CDP_URL)
        log.info("connected to Chrome via CDP: %s", settings.CHROME_CDP_URL)
        return _browser
    except Exception as e:  # noqa: BLE001
        log.warning("CDP connect failed (%s) — falling back to Playwright profile", e)
        if _pw is not None:
            try:
                await _pw.stop()
            except Exception:  # noqa: BLE001
                pass
            _pw = None
        return None


async def close_cdp() -> None:
    """Disconnect from Chrome (does NOT close the Chrome window)."""
    global _pw, _browser
    _browser = None
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:  # noqa: BLE001
            pass
        _pw = None


async def cdp_available() -> bool:
    b = await get_cdp_browser()
    return b is not None


def chrome_cdp_port() -> str:
    return settings.CHROME_CDP_URL.rsplit(":", 1)[-1].rstrip("/")


def launch_chrome_for_cdp() -> tuple[bool, str]:
    """Launch the dedicated-profile Chrome with remote debugging enabled.

    Works alongside the user's normal Chrome (different profile = separate
    instance). First launch: the user logs into JobsDB/OfferToday once.
    """
    profile = profile_dir()
    os.makedirs(profile, exist_ok=True)
    cmd = [
        "open", "-na", "Google Chrome", "--args",
        f"--remote-debugging-port={chrome_cdp_port()}",
        f"--user-data-dir={profile}",
        "https://hk.jobsdb.com",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "已開啟專用 Chrome 視窗（第一次要喺入面登入 JobsDB/OfferToday 一次）。"
        return False, r.stderr.strip() or "開啟失敗"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _profile_marker() -> str:
    return profile_dir()


def dedicated_chrome_running() -> bool:
    """True if the DEDICATED-profile Chrome instance is running (not the main one)."""
    import subprocess as sp

    try:
        r = sp.run(["pgrep", "-f", _profile_marker()], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def quit_dedicated_chrome() -> tuple[bool, str]:
    """Quit ONLY the dedicated-profile Chrome instance (user's Chrome untouched)."""
    try:
        r = subprocess.run(["pkill", "-f", _profile_marker()], capture_output=True, text=True, timeout=15)
        # pkill returns 0 even when nothing matched on some systems; verify
        if not dedicated_chrome_running():
            return True, "專用 Chrome 已退出。"
        return False, "退出失敗（可能冇權限）。"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
