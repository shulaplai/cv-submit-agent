"""Email application flow for jobs.gov.hk vacancies (and any job with contact_email).

Composes the application email from the generated cover letter + CV, then opens
macOS Mail with everything pre-filled (AppleScript). The human reviews and
presses send themselves — no SMTP credentials, nothing is sent automatically.

Fallback when Mail/AppleScript is unavailable: mailto: link + copy body to
clipboard so the user pastes it anywhere.

發送前 AI 潤色（用戶要求）：整封 email 內文（稱呼 + 自我介紹 + 求職信 + 結尾 +
簽名）會經一次 LLM 潤色，令封信讀落自然、唔似 AI 拼砌。潤色成品會存落 DB
（`email_body_polished` + `email_polish_key` cache key），所以 **預覽同實寄係
同一份文字**，而且同一封（同一 CL 版本／模板）再撳預覽唔會再洗 LLM。潤色失敗
一律用原文，唔會漏寄。
"""
from __future__ import annotations

import hashlib
import logging
import shlex
import subprocess
from pathlib import Path

from ..config import settings
from ..models import JobApplication
from .polish import PolishError, polish_email_body
from .tuning import load_tuning

log = logging.getLogger(__name__)


def _email_context(row: JobApplication, cl_text: str) -> dict:
    """Build the context passed to an email template (intro/name/email from profile)."""
    from ..db import SessionLocal
    from ..models import Profile

    lang = getattr(row, "jd_language", "zh") or "zh"
    intro = ""
    name = settings.APPLICANT_NAME
    email_addr = settings.APPLICANT_EMAIL
    try:
        db = SessionLocal()
        try:
            profile = db.get(Profile, 1)
            if profile:
                intro = (profile.intro_zh if lang == "zh" else profile.intro_en) or ""
                name = profile.name or name
                email_addr = profile.email or email_addr
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass

    return {
        "lang": lang,
        "contact_person": row.contact_person or "",
        "company": row.company or "",
        "title": row.title or "",
        "intro": (intro or "").strip(),
        "cl": (cl_text or "").strip(),
        "applicant_name": name or "",
        "applicant_email": email_addr or "",
    }


def build_email(row: JobApplication, cl_text: str, cv_path: str, template_key: str = "standard") -> dict:
    """Assemble subject/body/attachment for an email application.

    The subject and body follow the JD language; the body is composed from the
    chosen template (self-intro + cover letter + signature). 呢個係**未潤色**嘅
    版本；要寄／要預覽就用 ``build_email_polished()``（會做 AI 潤色）。
    """
    from .email_templates import compose_body

    lang = getattr(row, "jd_language", "zh") or "zh"
    subject = (
        f"Application for {row.title} ({row.company or row.platform})"
        if lang == "en"
        else f"應徵：{row.title}（{row.company or row.platform}）"
    )
    ctx = _email_context(row, cl_text)
    body = compose_body(template_key, ctx)
    if not body.strip():
        body = cl_text.strip() or "（請喺 UI 先生成/編輯 Cover Letter）"
    return {
        "to": row.contact_email,
        "contact_person": row.contact_person,
        "subject": subject,
        "body": body,
        "attachment": str(Path(cv_path).resolve()) if cv_path else "",
    }


def polish_cache_key(cl_text: str, template_key: str, lang: str) -> str:
    """潤色 cache key：同一封（同一 CL 內容／模板／語言）唔會重複洗 LLM。"""
    digest = hashlib.sha256((cl_text or "").encode("utf-8")).hexdigest()[:16]
    return f"{lang}:{template_key}:{digest}"


def _cached_polish(row: JobApplication, key: str) -> str:
    if (getattr(row, "email_polish_key", "") or "") == key:
        return (getattr(row, "email_body_polished", "") or "").strip()
    return ""


def _store_polish(row: JobApplication, key: str, body: str) -> None:
    """存潤色成品（best-effort：失敗唔會阻礙發送）。"""
    from ..models import utcnow as _utcnow

    try:
        from ..db import SessionLocal

        db = SessionLocal()
        try:
            db_row = db.get(JobApplication, row.id)
            if db_row is None:
                return
            db_row.email_body_polished = body
            db_row.email_polish_key = key
            db_row.email_polished_at = _utcnow()
            db.commit()
            if db_row is not row:
                # 唔係同一個 session 嘅 object -> 手動同步，等 preview 即刻見到
                row.email_body_polished = body
                row.email_polish_key = key
                row.email_polished_at = db_row.email_polished_at
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        log.warning("saving polished email body failed for job %s", getattr(row, "id", "?"))


async def build_email_polished(row: JobApplication, cl_text: str, cv_path: str,
                               template_key: str = "standard") -> tuple[dict, bool, str]:
    """(email, polished?, 原文 body)。

    一齊做：組信 -> 檢查 cache -> 需要就 AI 潤色整封內文 -> 存返 DB。
    潤色失敗／關掉開關就用未潤色版本（`polished=False`），永遠有信可寄。
    """
    email = build_email(row, cl_text, cv_path, template_key)
    original = email["body"]
    tuning = load_tuning()
    if not tuning.email_polish_enabled:
        return email, False, original
    lang = getattr(row, "jd_language", "zh") or "zh"
    key = polish_cache_key(cl_text, template_key, lang)
    cached = _cached_polish(row, key)
    if cached:
        email["body"] = cached
        return email, True, original
    try:
        polished = await polish_email_body(
            original, lang,
            {"title": getattr(row, "title", ""), "company": getattr(row, "company", "")},
            tuning.email_polish_instructions,
        )
    except PolishError as e:
        log.warning("email polish skipped for job %s: %s", getattr(row, "id", "?"), e)
        return email, False, original
    except Exception as e:  # noqa: BLE001 — 任何意外都唔可以阻礙寄信
        log.warning("email polish crashed for job %s: %s", getattr(row, "id", "?"), e)
        return email, False, original
    _store_polish(row, key, polished)
    email["body"] = polished
    return email, True, original


def _apple_str(text: str) -> str:
    """Return `text` as an AppleScript string-literal EXPRESSION.

    AppleScript does not interpret ``\\n``; real line breaks are produced with
    the ``return`` constant, so newlines become ``" & return & "`` segments.
    The result is already quoted and safe to inline (e.g. ``content:{_apple_str(body)}``).
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return '"' + escaped.replace("\n", '" & return & "') + '"'




def compose_in_mail(email: dict) -> tuple[bool, str]:
    """Open macOS Mail with a pre-filled new message. Returns (ok, note)."""
    if not email.get("to"):
        return False, "呢份工冇聯絡 email，請睇返申請須知用其他方法申請。"
    subj = _apple_str(email["subject"])
    body = _apple_str(email["body"])
    to = _apple_str(email["to"])
    script = f"""
tell application "Mail"
	set newMsg to make new outgoing message with properties {{subject:{subj}, content:{body}}}
	tell newMsg
		make new to recipient at end of to recipients with properties {{address:{to}}}
	end tell
	activate
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, f"AppleScript 失敗: {result.stderr.strip()[:300]}"
        return True, "macOS Mail 已開好一封預填嘅申請信，請檢查後自行發送。"
    except Exception as e:  # noqa: BLE001
        return False, f"開 Mail 失敗: {e}"


def attach_cv_to_draft(cv_path: str) -> tuple[bool, str]:
    """Best-effort: attach the CV file to the most recent outgoing message draft."""
    if not cv_path:
        return False, "冇 CV 路徑，請手動附件。"
    p = Path(cv_path)
    if not p.exists():
        return False, f"CV 檔案不存在: {cv_path}"
    path_str = _apple_str(str(p.resolve()))
    script = f"""
tell application "Mail"
	set theDraft to last outgoing message of first account
	tell content of theDraft
		make new attachment with properties {{file name:(POSIX file {path_str})}} at after last paragraph
	end tell
end tell
"""
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, "已附上 CV。"
        # attachment of the just-created draft may need the draft selected;
        # surface the error but do not fail the whole flow.
        log.warning("attach CV failed: %s", r.stderr.strip()[:200])
        return False, "開咗郵件但自動附件失敗，請手動加上 CV。"
    except Exception as e:  # noqa: BLE001
        log.warning("attach CV failed: %s", e)
        return False, "開咗郵件但自動附件失敗，請手動加上 CV。"


def fallback_mailto(email: dict) -> tuple[bool, str]:
    """Fallback: open mailto: link and copy the body to the clipboard."""
    try:
        to = email["to"]
        subject = email["subject"].replace("\n", " ")
        url = f"mailto:{to}?subject={_url_quote(subject)}"
        subprocess.run(["open", url], check=False, timeout=10)
        body = email["body"]
        script = f"set the clipboard to {_apple_str(body)}"
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        return True, "已開 mailto 並複製內文到剪貼簿，請貼上內文同附上 CV 後發送。"
    except Exception as e:  # noqa: BLE001
        return False, f"fallback 失敗: {e}"


def _url_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text)


async def open_email_compose(row: JobApplication, cl_text: str, send: bool = False,
                             template_key: str = "standard") -> dict:
    """Top-level entry from apply_bot: compose + open Mail (or send it).

    send=True -> create the message, attach CV and SEND immediately via Mail
    (uses the user's own Mail account; no SMTP credentials needed).
    send=False -> open a pre-filled draft for the user to review (semi-auto).

    寄之前會做一次 AI 潤色（整封內文；見 module docstring）。潤色失敗就照用
    原文，唔會阻礙發送。
    """
    from .cv_loader import VARIANT_LABEL, resolve_cv_for_job

    # 守門（用戶要求：唔可以寄垃圾畀僱主）
    # 1. 冇 Cover Letter -> 唔好寄（以前會寄「（請喺 UI 先生成/編輯 Cover Letter）」）
    if not (cl_text or "").strip():
        return {"ok": True, "kind": "needs_manual", "submitted": False,
                "message": "冇 Cover Letter——唔會自動寄出。請先喺職位詳情頁生成 CL 再投。"}
    # 2. 冇 CV 檔案 -> 自動發送一律唔寄（半自動開 draft 就照開，你自己補附件）
    from pathlib import Path as _Path
    _cv_probe, _ = resolve_cv_for_job(row.title, row.jd_language)
    has_cv = bool(_cv_probe) and _Path(_cv_probe).exists()
    if send and not has_cv:
        return {"ok": True, "kind": "needs_manual", "submitted": False,
                "message": "冇 CV 檔案可以附件——未寄出。請先去設定頁上載/設定 CV 路徑。"}

    # Attach the CV version matching the job (AI → Full-stack → Developer →
    # 通用) for the JD language; falls back to the other language.
    cv_path, cv_variant = resolve_cv_for_job(row.title, row.jd_language)
    if not cv_path:
        from .cv_loader import resolve_cv_path
        cv_path = resolve_cv_path("zh" if row.jd_language == "en" else "en")
        cv_variant = "default"
    if not has_cv:
        log.warning("email 冇 CV 附件（job %s / %s）", getattr(row, "id", "?"), row.title)

    # 發送前最後一執：AI 潤色整封內文（稱呼／簽名保留）。失敗就照用原文。
    email, polished, original_body = await build_email_polished(
        row, cl_text, cv_path, template_key)

    # 話俾用戶知用咗邊個版本嘅 CV（AI 版／Full-stack 版／Developer 版／通用版）
    cv_tag = f"（CV：{VARIANT_LABEL.get(cv_variant, cv_variant)}）"
    polish_tag = "✨ 內文已 AI 潤色。" if polished else ""

    def _preview() -> dict:
        return {"to": email["to"], "subject": email["subject"], "body": email["body"],
                "body_original": original_body, "polished": polished}

    if send:
        ok, note = send_email_via_mail(email)
        if ok:
            warn = "" if has_cv else "（⚠ 冇 CV 附件）"
            return {"ok": True, "kind": "email_sent", "to": email["to"],
                    "message": f"{note} {polish_tag}{cv_tag}{warn}".strip(),
                    "submitted": True, "preview": _preview()}
        # sending failed -> fall back to opening a draft for review
        ok2, note2 = compose_in_mail(email)
        if ok2:
            note2 += " " + (attach_cv_to_draft(cv_path)[1] if cv_path else "")
            return {"ok": True, "kind": "email", "to": email["to"],
                    "message": f"自動發送失敗（{note}），已改為開 draft 俾你手動發送。"
                               f"{note2} {polish_tag}{cv_tag}".strip(),
                    "submitted": False, "preview": _preview()}
        return {"ok": False, "kind": "email_failed", "to": email["to"],
                "message": f"自動發送同開 Mail 都失敗：{note}；{note2}",
                "submitted": False, "preview": _preview()}

    ok, note = compose_in_mail(email)
    if ok:
        # attach CV right after the draft exists
        if cv_path:
            ok2, note2 = attach_cv_to_draft(cv_path)
            note += " " + note2
        return {"ok": True, "kind": "email", "to": email["to"],
                "message": f"{note} {polish_tag}{cv_tag}".strip(),
                "submitted": False, "preview": _preview()}

    # Mail unavailable -> fallback
    ok2, note2 = fallback_mailto(email)
    return {"ok": ok2, "kind": "email_fallback", "to": email["to"], "message": note2,
            "submitted": False, "preview": _preview()}


def send_email_via_mail(email: dict) -> tuple[bool, str]:
    """Compose + attach CV + SEND via macOS Mail. Returns (ok, note)."""
    if not email.get("to"):
        return False, "呢份工冇聯絡 email，唔可以自動發送。"
    subj = _apple_str(email["subject"])
    body = _apple_str(email["body"])
    to = _apple_str(email["to"])
    attach = ""
    if email.get("attachment") and Path(email["attachment"]).exists():
        attach = (
            f"\ttell content of newMsg\n"
            f"\t\tmake new attachment with properties "
            f'{{file name:(POSIX file {_apple_str(str(Path(email["attachment"]).resolve()))})}} '
            f"at after last paragraph\n"
            f"\tend tell\n"
        )
    script = f"""
tell application "Mail"
	set newMsg to make new outgoing message with properties {{subject:{subj}, content:{body}}}
	tell newMsg
		make new to recipient at end of to recipients with properties {{address:{to}}}
	end tell
{attach}\tsend newMsg
end tell
"""
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return True, "Email 已透過 macOS Mail 自動發送。"
        return False, f"Mail 發送失敗: {r.stderr.strip()[:300]}"
    except Exception as e:  # noqa: BLE001
        return False, f"Mail 發送失敗: {e}"
