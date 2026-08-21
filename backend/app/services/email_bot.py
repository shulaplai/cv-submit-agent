"""Email application flow for jobs.gov.hk vacancies (and any job with contact_email).

Composes the application email from the generated cover letter + CV, then opens
macOS Mail with everything pre-filled (AppleScript). The human reviews and
presses send themselves — no SMTP credentials, nothing is sent automatically.

Fallback when Mail/AppleScript is unavailable: mailto: link + copy body to
clipboard so the user pastes it anywhere.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from ..config import settings
from ..models import JobApplication

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
    chosen template (self-intro + cover letter + signature).
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
    """
    from .cv_loader import resolve_cv_path

    # Attach the CV matching the JD language; fall back to the other language.
    cv_path = resolve_cv_path(row.jd_language) or resolve_cv_path("zh" if row.jd_language == "en" else "en")
    email = build_email(row, cl_text or "（請喺 UI 先生成/編輯 Cover Letter）", cv_path, template_key)

    if send:
        ok, note = send_email_via_mail(email)
        if ok:
            return {"ok": True, "kind": "email_sent", "to": email["to"], "message": note,
                    "submitted": True,
                    "preview": {"to": email["to"], "subject": email["subject"], "body": email["body"]}}
        # sending failed -> fall back to opening a draft for review
        ok2, note2 = compose_in_mail(email)
        if ok2:
            note2 += " " + (attach_cv_to_draft(cv_path)[1] if cv_path else "")
            return {"ok": True, "kind": "email", "to": email["to"],
                    "message": f"自動發送失敗（{note}），已改為開 draft 俾你手動發送。{note2}",
                    "submitted": False,
                    "preview": {"to": email["to"], "subject": email["subject"], "body": email["body"]}}
        return {"ok": False, "kind": "email_failed", "to": email["to"],
                "message": f"自動發送同開 Mail 都失敗：{note}；{note2}",
                "submitted": False,
                "preview": {"to": email["to"], "subject": email["subject"], "body": email["body"]}}

    ok, note = compose_in_mail(email)
    if ok:
        # attach CV right after the draft exists
        if cv_path:
            ok2, note2 = attach_cv_to_draft(cv_path)
            note += " " + note2
        return {"ok": True, "kind": "email", "to": email["to"], "message": note,
                "submitted": False,
                "preview": {"to": email["to"], "subject": email["subject"], "body": email["body"]}}

    # Mail unavailable -> fallback
    ok2, note2 = fallback_mailto(email)
    return {"ok": ok2, "kind": "email_fallback", "to": email["to"], "message": note2,
            "submitted": False,
            "preview": {"to": email["to"], "subject": email["subject"], "body": email["body"]}}


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
