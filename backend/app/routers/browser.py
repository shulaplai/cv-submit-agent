"""Real-browser (CDP) status + launch endpoints.

JobsDB / OfferToday automation runs inside a dedicated-profile Chrome window
via CDP (Chrome 136+ blocks remote debugging on the default profile). The user
logs in once there; their normal Chrome is never touched.
"""
import asyncio
import logging

from fastapi import APIRouter

from ..services.cdp_browser import (
    cdp_available,
    chrome_cdp_port,
    dedicated_chrome_running,
    launch_chrome_for_cdp,
    quit_dedicated_chrome,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/browser", tags=["browser"])


@router.get("/status")
async def browser_status():
    available = await cdp_available()
    running = dedicated_chrome_running()
    if available:
        note = "✓ 已連接到專用 Chrome 視窗 — 自動投遞會喺嗰個視窗開 tab 操作（唔影響你原本 Chrome）"
    elif running:
        note = "專用 Chrome 開緊但冇 debug port — 撳「🔁 重啟專用 Chrome」"
    else:
        note = "未開專用 Chrome — 撳「🔗 開啟專用 Chrome」；第一次要喺入面登入 JobsDB/OfferToday 一次"
    return {
        "using_real_chrome": available,
        "chrome_running": running,
        "cdp_url": chrome_cdp_port(),
        "note": note,
    }


@router.post("/launch-chrome")
async def launch_chrome():
    """Launch the dedicated-profile Chrome with remote debugging."""
    if dedicated_chrome_running():
        return {"ok": False, "restart_needed": True,
                "message": "專用 Chrome 已經開緊但冇 debug port。撳「🔁 重啟專用 Chrome」。"}
    ok, note = launch_chrome_for_cdp()
    if not ok:
        return {"ok": False, "restart_needed": False, "message": note}
    await asyncio.sleep(4)
    if await cdp_available():
        return {"ok": True, "restart_needed": False,
                "message": "✓ 專用 Chrome 已開啟並連上。（第一次使用：喺嗰個視窗登入 JobsDB/OfferToday 一次。）"}
    return {"ok": False, "restart_needed": False,
            "message": "Chrome 開咗但未連上 debug port，請稍後再撳一次。"}


@router.post("/restart-chrome")
async def restart_chrome():
    """One-click: quit ONLY the dedicated Chrome, relaunch with debug port."""
    if dedicated_chrome_running():
        ok, note = quit_dedicated_chrome()
        if not ok:
            return {"ok": False, "restart_needed": True, "message": note}
        await asyncio.sleep(2)
    ok, note = launch_chrome_for_cdp()
    if not ok:
        return {"ok": False, "restart_needed": False, "message": note}
    await asyncio.sleep(4)
    if await cdp_available():
        return {"ok": True, "restart_needed": False,
                "message": "✓ 專用 Chrome 已重開並連上。（第一次使用：喺嗰個視窗登入 JobsDB/OfferToday 一次。）"}
    return {"ok": False, "restart_needed": False,
            "message": "專用 Chrome 重開咗但未連上 debug port，請再撳多次。"}
