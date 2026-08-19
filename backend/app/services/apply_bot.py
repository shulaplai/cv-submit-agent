"""Semi-auto apply bot: open the application flow, prefill what we can, STOP.

The human always reviews in the visible browser window and clicks submit/send
themselves — the bot never submits. If a login wall / captcha is detected the
job is flagged needs_manual_intervention and the window is left open for login.
"""
from __future__ import annotations

import logging

from ..models import JobApplication
from .scraper_base import get_browser, is_blocked

log = logging.getLogger(__name__)

JOBSDB_BASE = "https://hk.jobsdb.com"


async def open_apply(row: JobApplication, cl_text: str = "") -> dict:
    """Open + prefill the application flow for a job. Returns a status dict."""
    if row.apply_method == "external_link":
        return {"ok": True, "kind": "external_link", "url": row.external_url or row.url,
                "message": "外部申請網站：已經開咗職位頁，請撳「Apply」去公司網站完成申請。"}

    if row.apply_method == "email":
        from .email_bot import open_email_compose
        return await open_email_compose(row, cl_text)

    if row.platform == "jobsdb":
        return await _open_jobsdb(row, cl_text)
    if row.platform == "offertoday":
        return await _open_offertoday(row, cl_text)
    return {"ok": False, "kind": "unknown", "message": f"唔支援嘅平台: {row.platform}"}


async def _open_jobsdb(row: JobApplication, cl_text: str) -> dict:
    session = await get_browser("jobsdb")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    if is_blocked(page):
        return {"ok": True, "kind": "blocked", "url": page.url,
                "message": "JobsDB 出現驗證/登入牆，請喺開咗嘅視窗完成驗證或登入，再手動申請。"}

    # navigate to the apply page
    apply_btn = page.locator("a[data-automation='job-detail-apply'], a[data-automation*='apply']").first
    if await apply_btn.count():
        href = await apply_btn.get_attribute("href") or ""
        if href.startswith("/"):
            await page.goto(JOBSDB_BASE + href, wait_until="domcontentloaded", timeout=45_000)

    note = "已開定 JobsDB 申請頁。"
    if cl_text:
        filled = await _fill_first_textarea(page, cl_text)
        note += "已預填 Cover Letter。" if filled else "（未揾到 Cover Letter 輸入框，請手動貼上。）"
    return {"ok": True, "kind": "form", "url": page.url, "message": note}


async def _open_offertoday(row: JobApplication, cl_text: str) -> dict:
    session = await get_browser("offertoday")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    btn = page.locator("#J_apply").first
    if await btn.count():
        await btn.click()
    note = "已開定 OfferToday 職位頁並撳咗「傳送投遞消息」。"
    if cl_text:
        filled = await _fill_first_textarea(page, cl_text)
        note += "已預填投遞訊息。" if filled else "（未揾到訊息輸入框，請喺視窗入面手動填寫。）"
    return {"ok": True, "kind": "form", "url": page.url, "message": note}


async def _fill_first_textarea(page, text: str) -> bool:
    """Fill the first visible textarea (message/cover-letter field) if any."""
    ta = page.locator("textarea").first
    try:
        if await ta.count() and await ta.is_visible():
            await ta.fill(text[:3000])
            return True
    except Exception:  # noqa: BLE001
        pass
    return False
