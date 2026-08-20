"""Semi/auto apply bot.

- auto=False (semi-auto): open the application flow, prefill what we can and
  STOP — the human reviews in the visible browser and clicks submit/send.
- auto=True  (auto): fill the form (CL + CV) and click submit / send the email
  itself. Safety guards: never auto-submit external links, never continue past
  a captcha/login wall, and abort if the CL is missing or the confirmation is
  unclear (falls back to leaving the page open for the human).
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from ..models import JobApplication
from .scraper_base import get_browser, is_blocked

log = logging.getLogger(__name__)

JOBSDB_BASE = "https://hk.jobsdb.com"

SUBMIT_BUTTON_TEXTS = ("Submit application", "Submit", "提交申請", "提交", "Apply now", "Send application", "發送", "送出")
SUCCESS_MARKERS = (
    "application submitted", "application has been submitted", "已提交", "提交成功",
    "successfully submitted", "your application", "submitted successfully",
)


def _cv_path_for(language: str) -> str:
    from .cv_loader import resolve_cv_path

    path = resolve_cv_path(language)
    if not path:
        path = resolve_cv_path("zh" if language == "en" else "en")
    return path


def _cv_exists(path: str) -> bool:
    return bool(path) and Path(path).exists()


def build_application_text(row: JobApplication, cl_text: str) -> str:
    """Saved self-intro (profile, in the JD language) + cover letter.

    This is the text embedded into application messages / emails.
    """
    from ..db import SessionLocal
    from ..models import Profile

    intro = ""
    try:
        db = SessionLocal()
        try:
            profile = db.get(Profile, 1)
            if profile:
                intro = profile.intro_zh if row.jd_language == "zh" else profile.intro_en
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    parts = [p for p in (intro.strip(), cl_text.strip()) if p]
    return "\n\n".join(parts)


async def open_apply(row: JobApplication, cl_text: str = "", auto: bool = False) -> dict:
    """Open + prefill (or auto-submit) the application flow for a job."""
    if row.apply_method == "external_link":
        return {"ok": True, "kind": "external_link", "url": row.external_url or row.url,
                "submitted": False,
                "message": "外部申請網站唔會自動投遞（避免亂填公司系統），請撳 link 手動完成。"}

    if row.apply_method == "email":
        from .email_bot import open_email_compose
        return await open_email_compose(row, cl_text, send=auto)

    if row.platform == "jobsdb":
        return await _jobsdb(row, cl_text, auto)
    if row.platform == "offertoday":
        return await _offertoday(row, cl_text, auto)
    return {"ok": False, "kind": "unknown", "submitted": False,
            "message": f"唔支援嘅平台: {row.platform}"}


def _abort(message: str, url: str = "") -> dict:
    return {"ok": True, "kind": "needs_manual", "submitted": False,
            "url": url, "message": message}


# ------------------------------------------------------------------ JobsDB

async def _jobsdb(row: JobApplication, cl_text: str, auto: bool) -> dict:
    session = await get_browser("jobsdb")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    if is_blocked(page):
        return _abort("JobsDB 出現驗證/登入牆，請喺開咗嘅視窗完成驗證或登入，再手動申請。", page.url)

    apply_btn = page.locator("a[data-automation='job-detail-apply'], a[data-automation*='apply']").first
    if await apply_btn.count():
        href = await apply_btn.get_attribute("href") or ""
        if href.startswith("/"):
            await page.goto(JOBSDB_BASE + href, wait_until="domcontentloaded", timeout=45_000)

    # login wall on the apply page -> must log in first
    if "login" in page.url.lower() or is_blocked(page):
        return _abort("JobsDB 申請要登入 SEEK：請喺開咗嘅視窗登入一次，之後再撳自動投遞。", page.url)

    if not auto:
        note = "已開定 JobsDB 申請頁。"
        if cl_text:
            filled = await _fill_first_textarea(page, build_application_text(row, cl_text))
            note += "已預填 Cover Letter。" if filled else "（未揾到 CL 輸入框，請手動貼上。）"
        return {"ok": True, "kind": "form", "submitted": False, "url": page.url, "message": note}

    return await _auto_submit_platform(page, row, cl_text)


# ------------------------------------------------------------------ OfferToday

async def _offertoday(row: JobApplication, cl_text: str, auto: bool) -> dict:
    session = await get_browser("offertoday")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    btn = page.locator("#J_apply").first
    if await btn.count():
        await btn.click()
    if is_blocked(page):
        return _abort("OfferToday 出現登入/驗證要求，請喺開咗嘅視窗完成，再撳自動投遞。", page.url)

    if not auto:
        note = "已開定 OfferToday 職位頁並撳咗「傳送投遞消息」。"
        if cl_text:
            filled = await _fill_first_textarea(page, build_application_text(row, cl_text))
            note += "已預填投遞訊息。" if filled else "（未揾到訊息輸入框，請手動填。）"
        return {"ok": True, "kind": "form", "submitted": False, "url": page.url, "message": note}

    return await _auto_submit_platform(page, row, cl_text)


# ------------------------------------------------------------------ auto submit

async def _auto_submit_platform(page, row: JobApplication, cl_text: str) -> dict:
    """Fill CL + CV and click submit. Returns {ok, kind, submitted, url, message}."""
    if not cl_text:
        return _abort("未有 Cover Letter（可能 LLM key 未設定）——唔會亂投。請先喺職位詳情生成/編輯 CL。", page.url)
    cv_path = _cv_path_for(row.jd_language)
    if not _cv_exists(cv_path):
        return _abort(f"揾唔到 CV 檔案：{cv_path}——請喺設定頁填返 CV 路徑先。", page.url)

    # 1. fill cover letter / message textarea
    try:
        ta = page.locator("textarea").first
        if await ta.count() and await ta.is_visible():
            await ta.fill(build_application_text(row, cl_text)[:4000])
    except Exception:  # noqa: BLE001
        pass

    # 2. choose / attach CV
    cv_ok = False
    file_input = page.locator("input[type='file']").first
    if await file_input.count():
        try:
            await file_input.set_input_files(cv_path)
            cv_ok = True
        except Exception as e:  # noqa: BLE001
            log.warning("file input attach failed: %s", e)
    else:
        # SEEK-style CV picker: radios labelled with CV names
        radios = page.locator("input[type='radio']")
        n = await radios.count()
        if n:
            cv_base = Path(cv_path).stem.lower()
            picked = False
            for i in range(n):
                label = await _radio_label(radios.nth(i), cv_base)
                if label:
                    await radios.nth(i).check()
                    picked = True
                    break
            if not picked:
                await radios.first.check()
            cv_ok = True
    if not cv_ok:
        return _abort("未揾到 CV 上傳/選擇欄位——為咗安全，唔會自動提交。請用手動模式。", page.url)

    # 3. click submit
    submitted_click = False
    for t in SUBMIT_BUTTON_TEXTS:
        loc = page.locator(f"button:has-text('{t}')").last
        if await loc.count():
            try:
                await loc.click(timeout=5000)
                submitted_click = True
                break
            except Exception:  # noqa: BLE001
                continue
    if not submitted_click:
        submit = page.locator("button[type='submit']").last
        if await submit.count():
            try:
                await submit.click(timeout=5000)
                submitted_click = True
            except Exception:  # noqa: BLE001
                pass
    if not submitted_click:
        return _abort("已填好 CL 同 CV，但未揾到提交按鈕——請喺開咗嘅視窗手動撳提交。", page.url)

    # 4. confirm
    confirmed = await _confirm_submitted(page)
    if confirmed:
        return {"ok": True, "kind": "submitted", "submitted": True, "url": page.url,
                "message": "✔ 申請已自動提交。"}
    return {"ok": True, "kind": "maybe_submitted", "submitted": True, "url": page.url,
            "message": "已撳咗提交但未能確認結果——請睇下開咗嘅頁面係咪成功（唔肯定就唔好再撳一次）。"}


async def _radio_label(radio, cv_base: str) -> bool:
    """Try to match a radio with its label text to the CV filename."""
    try:
        label = radio.evaluate(
            "el => { const l = el.closest('label'); return l ? l.innerText : ''; }"
        )
        return cv_base in (label or "").lower()
    except Exception:  # noqa: BLE001
        return False


async def _confirm_submitted(page, timeout_ms: int = 15000) -> bool:
    """Best-effort confirmation that the application went through."""
    import asyncio

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(1.5)
    url = page.url.lower()
    if any(m in url for m in ("success", "applied")):
        return True
    try:
        body = (await page.locator("body").inner_text(timeout=3000))[:4000].lower()
    except Exception:  # noqa: BLE001
        return False
    return any(m in body for m in SUCCESS_MARKERS)


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
