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


@router.get("/mail-status")
async def mail_status():
    """檢查自動化（AppleScript）權限：server 可唔可以控制 Mail 發 email。

    用戶實測痛點：`-10004 越權取用的錯誤` = macOS Automation 權限未開／被拒。
    呢個 endpoint 由 **server 自己嘅 process** 去問 Mail 版本（唔會開信、唔會寄信），
    所以測到嘅就係「自動投遞 email 會唔會成功」。
    """
    from ..services.email_bot import mail_access

    info = mail_access()
    return {
        "ok": info["ok"],
        "version": info["version"],
        "host_app": info.get("host_app", ""),
        "note": info["note"],
        "hint": ("" if info["ok"] else
                 "系統設定 → 隱私權與安全性 → 自動化 → 揾「終端機／Terminal」"
                 "（或啟動 server 嘅程式）→ 勾「郵件 / Mail」，之後重啟 server。"),
    }


@router.post("/mail-selftest")
async def mail_selftest():
    """真正開一封 Mail draft（冇收件人、即刻關閉、唔會寄出）再診斷。

    `get version` 成功唔代表「開 draft」一定成功，所以用戶報 email 發送失敗時
    用呢個一撳就知道係唔係權限／Mail 狀態問題。
    """
    from ..services.email_bot import mail_selftest as _selftest

    info = _selftest()
    return {
        "ok": info["ok"],
        "host_app": info.get("host_app", ""),
        "note": info["note"],
        "error": info["error"],
        "hint": ("" if info["ok"] else
                 "系統設定 → 隱私權與安全性 → 自動化 → 揾「終端機／Terminal」"
                 "（或啟動 server 嘅程式）→ 勾「郵件 / Mail」，之後重啟 server。"),
    }


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
