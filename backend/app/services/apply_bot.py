"""Semi/auto apply bot.

- auto=False (semi-auto): open the application flow, prefill the CL AND attach
  the CV, then STOP — the human reviews in the visible browser and clicks the
  final submit/send themselves. The CV is already attached, so that final
  click actually goes through.
- auto=True  (auto): fill the form (CL + CV) and click submit / send the email
  itself. Safety guards: never auto-submit external links, never continue past
  a captcha/login wall, abort if the CL is missing or the CV cannot be
  attached, and report a form error if the confirmation page shows one (falls
  back to leaving the page open for the human).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ..models import JobApplication
from .scraper_base import get_browser, is_blocked

log = logging.getLogger(__name__)

JOBSDB_BASE = "https://hk.jobsdb.com"

SUBMIT_BUTTON_TEXTS = ("Submit application", "Submit", "提交申請", "提交", "Apply now", "Send application", "發送", "送出")
SUCCESS_MARKERS = (
    "application submitted", "application has been submitted", "已提交", "提交成功",
    "successfully submitted", "your application", "submitted successfully",
)
# If any of these appear on the page right after submit, the form did NOT go through.
ERROR_MARKERS = (
    "is required", "please fill", "please complete", "please select",
    "invalid", "error", "請填寫", "必填", "請檢查", "錯誤", "未能提交",
)

# OfferToday「發履歷」dialog: filename markers used to pick the right pre-uploaded
# resume per JD language (overridden by OFFERTODAY_CV_*_KEYWORD in config).
_ZH_CV_MARKERS = ("zh", "chinese", "中文", "繁體", "繁中", "簡體", "tc")
_EN_CV_MARKERS = ("en", "english", "fullstack", "resume", "eng")

# OfferToday post-CV self-intro defaults (~100 chars), overridable in Settings.
DEFAULT_INTRO_IT_ZH = ("你好，我係一位專注 IT 同程式開發嘅工程師，有全端開發同 AI 應用嘅經驗，"
                       "熟悉 Python、TypeScript、React 同大型語言模型等技術。對貴公司呢個職位好有興趣，"
                       "希望有機會詳談，謝謝！")
DEFAULT_INTRO_IT_EN = ("Hi, I'm a software engineer focused on IT and programming, with experience "
                       "in full-stack development and AI applications using Python, TypeScript, React "
                       "and LLM technologies. I'm very interested in this role and would love to "
                       "discuss further. Thank you!")
DEFAULT_INTRO_GENERAL_ZH = ("你好，我係一位工作認真、學習能力強嘅求職者，具備良好嘅溝通同團隊合作能力，"
                            "對貴公司嘅發展同文化好有興趣，希望有機會加入並一齊成長。請查收履歷，謝謝！")
DEFAULT_INTRO_GENERAL_EN = ("Hi, I'm a diligent and quick-learning candidate with strong communication "
                            "and teamwork skills. I'm genuinely interested in your company and this "
                            "opportunity, and would welcome the chance to contribute and grow. Thank you!")

# Built-in keywords used to classify a job as IT/programming. Overridable in
# Settings (profile.it_keywords, comma-separated).
DEFAULT_IT_KEYWORDS = [
    "ai", "developer", "engineer", "programmer", "programming", "software",
    "frontend", "backend", "full stack", "full-stack", "python", "javascript",
    "typescript", "react", "node", "devops", "cloud", "machine learning",
    "deep learning", "data", "algorithm", "sql", "database",
    "程式", "工程師", "開發", "資訊科技", "軟件", "軟體", "人工智能", "數據",
    "編程", "技術員", "演算法", "編碼", "科技",
]


def _it_keywords() -> list[str]:
    """Effective IT keywords: profile.it_keywords (comma-separated) if set,
    else the built-in defaults."""
    from ..db import SessionLocal
    from ..models import Profile

    try:
        db = SessionLocal()
        try:
            p = db.get(Profile, 1)
            if p and p.it_keywords:
                kws = [k.strip() for k in p.it_keywords.split(",") if k.strip()]
                if kws:
                    return kws
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return list(DEFAULT_IT_KEYWORDS)


def _match_it_keyword(kw: str, text: str) -> bool:
    """Keyword match: CJK -> substring; latin -> whole-word, case-insensitive."""
    kw = kw.strip()
    if not kw:
        return False
    if any(ord(c) > 127 for c in kw):
        return kw in text
    return re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE) is not None


def _is_it_job(row: JobApplication, kws: list[str] | None = None) -> bool:
    """True if the job is IT / programming related (title or JD keywords)."""
    kws = kws if kws is not None else _it_keywords()
    title = row.title or ""
    jd = (row.jd_text or "")[:2000]
    return any(_match_it_keyword(k, title) or _match_it_keyword(k, jd) for k in kws)


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


async def open_apply(row: JobApplication, cl_text: str = "", auto: bool = False,
                     template_key: str = "standard") -> dict:
    """Open + prefill (or auto-submit) the application flow for a job."""
    if row.apply_method == "external_link":
        return {"ok": True, "kind": "external_link", "url": row.external_url or row.url,
                "submitted": False,
                "message": "外部申請網站唔會自動投遞（避免亂填公司系統），請撳 link 手動完成。"}

    if row.apply_method == "email":
        from .email_bot import open_email_compose
        return await open_email_compose(row, cl_text, send=auto, template_key=template_key)

    if row.platform == "jobsdb":
        return await _jobsdb(row, cl_text, auto)
    if row.platform == "offertoday":
        return await _offertoday(row, cl_text, auto)
    return {"ok": False, "kind": "unknown", "submitted": False,
            "message": f"唔支援嘅平台: {row.platform}"}


def _abort(message: str, url: str = "") -> dict:
    return {"ok": True, "kind": "needs_manual", "submitted": False,
            "url": url, "message": message}


# ------------------------------------------------------------------ field filling

async def _fill_message_field(page, text: str) -> str:
    """Fill the application message / cover-letter field.

    Tries, in order: visible textarea → contenteditable / role=textbox →
    textarea inside an iframe. Returns a short status string ('' = not found).
    """
    text = text[:4000]
    for sel in ("textarea", "[contenteditable='true']", "[contenteditable='']", "[role='textbox']"):
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                await loc.fill(text)
                return "已填 CL/訊息" if sel == "textarea" else "已填 CL/訊息（富文本框）"
        except Exception:  # noqa: BLE001
            continue
    try:
        for frame in page.frames:
            loc = frame.locator("textarea").first
            if await loc.count() and await loc.is_visible():
                await loc.fill(text)
                return "已填 CL/訊息（iframe 內）"
    except Exception:  # noqa: BLE001
        pass
    return ""


async def _radio_label(radio) -> str:
    """Best-effort label text for a radio button (its closest <label>)."""
    try:
        label = await radio.evaluate(
            "el => { const l = el.closest('label'); return l ? l.innerText : ''; }"
        )
        return label or ""
    except Exception:  # noqa: BLE001
        return ""


async def _attach_cv(page, cv_path: str) -> tuple[bool, str]:
    """Attach/select the CV file on the application form.

    Tries: any file input (Playwright set_input_files works on hidden inputs,
    e.g. styled upload buttons), then SEEK-style radio pickers whose label
    matches the CV filename (or mentions CV/resume/履歷). Never blindly picks
    the first radio — that could be "no CV" or a different saved profile.
    Returns (ok, note).
    """
    file_inputs = page.locator("input[type='file']")
    n = await file_inputs.count()
    for i in range(n):
        try:
            await file_inputs.nth(i).set_input_files(cv_path)
            return True, "已上傳 CV 檔案"
        except Exception as e:  # noqa: BLE001
            log.warning("file input attach failed: %s", e)

    radios = page.locator("input[type='radio']")
    n = await radios.count()
    if n:
        cv_base = Path(cv_path).stem.lower()
        picked = False
        for i in range(n):
            label = (await _radio_label(radios.nth(i))).lower()
            if label and cv_base in label:
                await radios.nth(i).check()
                picked = True
                break
        if not picked:
            # no filename match — look for any radio that is clearly a CV picker
            for i in range(n):
                label = (await _radio_label(radios.nth(i))).lower()
                if any(k in label for k in ("cv", "resume", "履歷", "簡歷")):
                    await radios.nth(i).check()
                    picked = True
                    break
        if picked:
            return True, f"已揀 CV（{Path(cv_path).name}）"
        return False, "有 CV 選擇框但認唔到邊個係你嘅 CV，請手動揀"
    return False, "未揀到 CV 上傳欄位"


# ------------------------------------------------------------------ submit + confirm

async def _try_click_submit(page) -> bool:
    """One attempt at clicking the submit button (any of the known texts)."""
    # 1. buttons matching known submit texts (scroll into view first)
    for t in SUBMIT_BUTTON_TEXTS:
        loc = page.locator(f"button:has-text('{t}')").last
        try:
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000)
                return True
        except Exception:  # noqa: BLE001
            continue
    # 2. generic type=submit buttons
    submit = page.locator("button[type='submit'], input[type='submit']").last
    try:
        if await submit.count():
            await submit.scroll_into_view_if_needed(timeout=3000)
            await submit.click(timeout=5000)
            return True
    except Exception:  # noqa: BLE001
        pass
    # 3. last resort: JS click on any button/a/input whose text matches
    try:
        clicked_js = await page.evaluate(
            """(texts) => {
                const els = [...document.querySelectorAll('button, a, input[type="submit"]')];
                const el = els.filter(e => {
                    const t = (e.innerText || e.value || '').trim().toLowerCase();
                    return texts.some(x => t.includes(x));
                }).pop();
                if (el) { el.click(); return true; }
                return false;
            }""",
            [t.lower() for t in SUBMIT_BUTTON_TEXTS],
        )
        if clicked_js:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


async def _click_submit(page, max_rounds: int = 2) -> bool:
    """Click submit; re-click once if a second review step appears (SEEK-style).

    Stops as soon as a success marker is visible. Never clicks more than
    max_rounds times in total.
    """
    clicked = False
    for _ in range(max_rounds):
        if not await _try_click_submit(page):
            break
        clicked = True
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.0)
        confirmed, _ = await _confirm_submitted(page, quick=True)
        if confirmed:
            break
    return clicked


async def _confirm_submitted(page, timeout_ms: int = 15000, quick: bool = False) -> tuple[bool, str]:
    """Best-effort check that the application went through.

    Returns (confirmed, error_snippet). error_snippet is non-empty when the
    page clearly shows a form error (the submission did NOT go through).
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5 if quick else 1.5)
    url = page.url.lower()
    if any(m in url for m in ("success", "applied")):
        return True, ""
    try:
        body = (await page.locator("body").inner_text(timeout=3000))[:4000].lower()
    except Exception:  # noqa: BLE001
        return False, ""
    if any(m in body for m in SUCCESS_MARKERS):
        return True, ""
    for m in ERROR_MARKERS:
        if m in body:
            i = body.find(m)
            snippet = body[max(0, i - 60): i + 80].replace("\n", " ").strip()
            return False, snippet
    return False, ""


# ------------------------------------------------------------------ JobsDB

async def _jobsdb(row: JobApplication, cl_text: str, auto: bool) -> dict:
    session = await get_browser("jobsdb")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    if await is_blocked(page):
        return _abort("JobsDB 出現驗證/登入牆，請喺開咗嘅視窗完成驗證或登入，再手動申請。", page.url)

    apply_btn = page.locator("a[data-automation='job-detail-apply'], a[data-automation*='apply']").first
    if await apply_btn.count():
        href = await apply_btn.get_attribute("href") or ""
        if href.startswith("/"):
            await page.goto(JOBSDB_BASE + href, wait_until="domcontentloaded", timeout=45_000)

    # login wall on the apply page -> must log in first
    if "login" in page.url.lower() or await is_blocked(page):
        return _abort("JobsDB 申請要登入 SEEK：請喺開咗嘅視窗登入一次，之後再撳自動投遞。", page.url)

    if not auto:
        return await _prefill_platform(page, row, cl_text, "JobsDB")
    return await _auto_submit_platform(page, row, cl_text)


# ------------------------------------------------------------------ OfferToday

def _offertoday_settings() -> dict:
    """Profile fields for OfferToday, with config/env fallbacks. Returns dict."""
    from ..config import settings
    from ..db import SessionLocal
    from ..models import Profile

    p = None
    try:
        db = SessionLocal()
        try:
            p = db.get(Profile, 1)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        p = None

    def gv(attr: str, default: str = "") -> str:
        v = getattr(p, attr, "") if p else ""
        return v or default

    return {
        "cv_en_kw": gv("offertoday_cv_en_keyword", settings.OFFERTODAY_CV_EN_KEYWORD),
        "cv_zh_kw": gv("offertoday_cv_zh_keyword", settings.OFFERTODAY_CV_ZH_KEYWORD),
        "intro_it_zh": gv("after_cv_intro_it_zh"),
        "intro_it_en": gv("after_cv_intro_it_en"),
        "intro_general_zh": gv("after_cv_intro_general_zh"),
        "intro_general_en": gv("after_cv_intro_general_en"),
    }


async def generate_after_cv_intro(lang: str, is_it: bool) -> str:
    """AI-write the ~100-char post-CV self-intro (IT vs general, en vs zh).

    Used by the settings endpoint and by the OfferToday apply flow. Raises
    LLMError when generation fails (callers fall back to a default template).
    """
    from ..services import llm as llm_svc
    from ..services.cv_loader import get_cv_text, load_skills

    try:
        cv_text = get_cv_text(lang)
    except Exception:  # noqa: BLE001 — no CV configured, still generate generic
        cv_text = ""
    skills = load_skills()
    topic = "IT / 程式開發" if is_it else "一般專業"

    if lang == "zh":
        system = (
            "你係求職者嘅助手。根據求職者履歷寫一段 80–120 字繁體中文自我介紹，"
            "用喺求職平台發完 CV 之後跟住送出。語氣專業自信、唔吹噓，只可以用履歷事實。"
            "直接輸出自我介紹文字，唔加稱呼/標題/問候。"
        )
        user = f"自我介紹方向：{topic}\n技能：{', '.join(skills) if skills else '（未設定）'}\n\n履歷：\n{cv_text[:4000]}"
    else:
        system = (
            "You are the applicant's assistant. Write an 80–120 word English "
            "self-introduction to send right after the CV on a job platform. "
            "Professional and confident, based ONLY on CV facts, no exaggeration. "
            "Output only the intro text — no greeting, title, or sign-off."
        )
        user = f"Intro angle: {topic}\nSkills: {', '.join(skills) if skills else '(not set)'}\n\nCV:\n{cv_text[:4000]}"

    text = await llm_svc.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.7,
    )
    return text.strip()


async def _offertoday_intro(row: JobApplication, cfg: dict) -> str:
    """Pick the post-CV self-intro: saved value > AI-generated > default template."""
    is_it = _is_it_job(row)
    if row.jd_language == "en":
        saved = cfg["intro_it_en"] if is_it else cfg["intro_general_en"]
        default = DEFAULT_INTRO_IT_EN if is_it else DEFAULT_INTRO_GENERAL_EN
    else:
        saved = cfg["intro_it_zh"] if is_it else cfg["intro_general_zh"]
        default = DEFAULT_INTRO_IT_ZH if is_it else DEFAULT_INTRO_GENERAL_ZH
    if saved:
        return saved
    try:
        return await generate_after_cv_intro(row.jd_language, is_it)
    except Exception:  # noqa: BLE001 — LLM missing/failed -> use template
        return default


async def _offertoday_send_message(page, text: str) -> bool:
    """Type text into the chat message box and click the 「發送」 button."""
    ce = page.locator("[contenteditable='true']").first
    if not await ce.count():
        return False
    try:
        await ce.fill(text[:1000])
    except Exception:  # noqa: BLE001
        try:
            await ce.click()
            await ce.fill(text[:1000])
        except Exception:  # noqa: BLE001
            return False
    send = page.locator("button.MuiButton-contained:has-text('發送')").last
    if not await send.count():
        send = page.locator("button:has-text('發送')").last
    try:
        await send.click(timeout=5000)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _offertoday(row: JobApplication, cl_text: str, auto: bool) -> dict:
    session = await get_browser("offertoday")
    page = await session.context.new_page()
    await page.goto(row.url, wait_until="domcontentloaded", timeout=45_000)
    # The message entry button is JS-rendered (SPA); wait for it to mount.
    try:
        await page.wait_for_selector("#J_apply, button:has-text('繼續溝通')", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5)

    # open the message/compose view: 「傳送訊息」(#J_apply, new thread) or
    # 「繼續溝通」 (existing thread with this employer).
    opened = False
    for sel in ("#J_apply", "button:has-text('繼續溝通')"):
        btn = page.locator(sel).first
        if await btn.count():
            try:
                await btn.click()
                opened = True
                break
            except Exception:  # noqa: BLE001
                continue
    if not opened:
        return _abort("未揾到 OfferToday「傳送訊息／繼續溝通」掣，請喺視窗手動開啟。", page.url)
    await asyncio.sleep(1.5)
    if await is_blocked(page):
        return _abort("OfferToday 出現登入/驗證要求，請喺開咗嘅視窗完成，再撳自動投遞。", page.url)

    cfg = _offertoday_settings()

    # 1. 發履歷 -> 「選擇履歷」dialog -> pick CV by JD language
    picked = await _offertoday_pick_cv(page, row.jd_language, cfg["cv_zh_kw"], cfg["cv_en_kw"])
    if not picked:
        return {"ok": True, "kind": "form", "submitted": False, "url": page.url,
                "message": "⚠ 未揾到/揀到已上傳嘅 CV，請喺視窗手動撳「發履歷」揀。"}

    if not auto:
        return {"ok": True, "kind": "form", "submitted": False, "url": page.url,
                "message": f"已揀履歷：{picked}。請喺視窗撳「發送」發 CV，再自己打自我介紹。"}

    # 2. send the CV (dialog 發送)
    send_cv = page.locator("[role='dialog'] button:has-text('發送')").last
    if not await send_cv.count():
        return {"ok": True, "kind": "maybe_submitted", "submitted": False, "url": page.url,
                "message": f"已揀履歷：{picked}，但未撳到對話框「發送」掣，請手動發送。"}
    try:
        await send_cv.click(timeout=5000)
    except Exception as e:  # noqa: BLE001
        log.warning("offertoday cv send failed: %s", e)
        return {"ok": True, "kind": "maybe_submitted", "submitted": False, "url": page.url,
                "message": f"已揀履歷：{picked}，但發送 CV 出錯，請喺視窗手動補發。"}
    await asyncio.sleep(1.0)

    # 3. type + send the self-intro message
    intro = await _offertoday_intro(row, cfg)
    if await _offertoday_send_message(page, intro):
        return {"ok": True, "kind": "submitted", "submitted": True, "url": page.url,
                "message": f"✔ OfferToday 已發送履歷（{picked}）同自我介紹。"}
    return {"ok": True, "kind": "submitted", "submitted": True, "url": page.url,
            "message": f"✔ 已發送履歷（{picked}），但自我介紹未能自動發送，請喺視窗手動補發。"}


def _offertoday_cv_matches(filename: str, language: str, zh_kw: str = "", en_kw: str = "") -> bool:
    """Whether an OfferToday resume filename matches the JD language."""
    fn = filename.lower()
    if language == "zh":
        if zh_kw and zh_kw.lower() in fn:
            return True
        return any(m in fn for m in _ZH_CV_MARKERS)
    # English: configured keyword, else "not Chinese"
    if en_kw and en_kw.lower() in fn:
        return True
    if any(m in fn for m in _ZH_CV_MARKERS):
        return False
    return True


async def _offertoday_pick_cv(page, language: str, zh_kw: str = "", en_kw: str = "") -> str:
    """Click 「發履歷」 to open the 「選擇履歷」 dialog and pick the CV whose
    filename matches the JD language. Returns the picked filename ('' = failed)."""
    fb = page.locator("button:has-text('發履歷')").first
    for _ in range(8):
        if await fb.count():
            break
        await asyncio.sleep(0.5)
    if not await fb.count():
        return ""
    try:
        await fb.click(timeout=5000)
    except Exception:  # noqa: BLE001
        return ""
    await asyncio.sleep(1.0)

    items = page.locator("[role='dialog'] p.MuiTypography-noWrap")
    n = await items.count()
    candidates: list[tuple[str, int]] = []
    for i in range(n):
        try:
            txt = (await items.nth(i).inner_text()).strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if txt.endswith((".pdf", ".doc", ".docx")):
            candidates.append((txt, i))
    if not candidates:
        return ""

    # pick the first candidate matching the language, else fall back to the first
    chosen = next((c for c in candidates if _offertoday_cv_matches(c[0], language, zh_kw, en_kw)), candidates[0])
    try:
        await items.nth(chosen[1]).click(timeout=5000)
        return chosen[0]
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ shared

async def _prefill_platform(page, row: JobApplication, cl_text: str, name: str) -> dict:
    """Semi-auto: fill CL + attach CV, but do NOT click submit.

    The human reviews in the visible window and clicks the final submit
    themselves — with the CV already attached, that last click goes through.
    """
    notes = [f"已開定 {name} 申請頁"]
    if cl_text:
        filled = await _fill_message_field(page, build_application_text(row, cl_text))
        notes.append(filled if filled else "未揾到 CL/訊息輸入框（請手動貼上）")
    else:
        notes.append("未有 CL（請先喺詳情頁生成）")
    cv_path = _cv_path_for(row.jd_language)
    if _cv_exists(cv_path):
        cv_ok, cv_note = await _attach_cv(page, cv_path)
        notes.append(cv_note if cv_ok else f"⚠ {cv_note}（可喺視窗手動補）")
    else:
        notes.append("未揾到 CV 檔案（請喺設定頁填 CV 路徑或喺視窗手動上傳）")
    return {"ok": True, "kind": "form", "submitted": False, "url": page.url,
            "message": "；".join(notes) + "。請喺視窗 review 後撳提交。"}


async def _auto_submit_platform(page, row: JobApplication, cl_text: str) -> dict:
    """Fill CL + attach CV and click submit. Returns {ok, kind, submitted, url, message}."""
    if not cl_text:
        return _abort("未有 Cover Letter（可能 LLM key 未設定）——唔會亂投。請先喺職位詳情生成/編輯 CL。", page.url)
    cv_path = _cv_path_for(row.jd_language)
    if not _cv_exists(cv_path):
        return _abort(f"揾唔到 CV 檔案：{cv_path}——請喺設定頁填返 CV 路徑先。", page.url)

    notes: list[str] = []
    filled = await _fill_message_field(page, build_application_text(row, cl_text))
    notes.append(filled if filled else "未揾到 CL/訊息輸入框（用平台 profile 內已有嘅 CL）")

    cv_ok, cv_note = await _attach_cv(page, cv_path)
    if not cv_ok:
        return _abort(f"未成功上傳/揀選 CV——{cv_note}。為咗安全唔會自動提交，請轉手動模式。", page.url)
    notes.append(cv_note)

    if not await _click_submit(page, max_rounds=2):
        return _abort("已填好 CL 同 CV，但未揾到提交按鈕——請喺開咗嘅視窗手動撳提交。", page.url)

    confirmed, err_snippet = await _confirm_submitted(page)
    if err_snippet:
        return {"ok": True, "kind": "failed", "submitted": False, "url": page.url,
                "message": f"提交後頁面顯示錯誤（{err_snippet[:120]}）——請喺視窗檢查並手動補交。"}
    if confirmed:
        return {"ok": True, "kind": "submitted", "submitted": True, "url": page.url,
                "message": "✔ 申請已自動提交（" + "；".join(notes) + "）。"}
    return {"ok": True, "kind": "maybe_submitted", "submitted": True, "url": page.url,
            "message": "已撳咗提交（" + "；".join(notes) + "），但未能確認結果——請睇下開咗嘅頁面係咪成功（唔肯定就唔好再撳一次）。"}
