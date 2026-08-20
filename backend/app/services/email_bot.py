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


def build_email(row: JobApplication, cl_text: str, cv_path: str) -> dict:
    """Assemble subject/body/attachment for an email application."""
    subject = f"應徵：{row.title}（{row.company or row.platform}）"
    body = cl_text.strip()
    if settings.APPLICANT_NAME:
        body += f"\n\n{settings.APPLICANT_NAME}"
        if settings.APPLICANT_EMAIL:
            body += f"\n{settings.APPLICANT_EMAIL}"
        body += "\n"
    return {
        "to": row.contact_email,
        "contact_person": row.contact_person,
        "subject": subject,
        "body": body,
        "attachment": str(Path(cv_path).resolve()) if cv_path else "",
    }


def _apple_escape(text: str) -> str:
    """Escape a string for embedding inside an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def compose_in_mail(email: dict) -> tuple[bool, str]:
    """Open macOS Mail with a pre-filled new message. Returns (ok, note)."""
    if not email.get("to"):
        return False, "呢份工冇聯絡 email，請睇返申請須知用其他方法申請。"
    subj = _apple_escape(email["subject"])
    body = _apple_escape(email["body"])
    to = _apple_escape(email["to"])
    script = f"""
tell application "Mail"
	set newMsg to make new outgoing message with properties {{subject:"{subj}", content:"{body}"}}
	tell newMsg
		make new to recipient at end of to recipients with properties {{address:"{to}"}}
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
    path_esc = _apple_escape(str(p.resolve()))
    script = f"""
tell application "Mail"
	set theDraft to last outgoing message of first account
	add content file POSIX file "{path_esc}" to theDraft
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
        script = f"set the clipboard to {_apple_quote(body)}"
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        return True, "已開 mailto 並複製內文到剪貼簿，請貼上內文同附上 CV 後發送。"
    except Exception as e:  # noqa: BLE001
        return False, f"fallback 失敗: {e}"


def _url_quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text)


def _apple_quote(text: str) -> str:
    return '"' + _apple_escape(text) + '"'


async def open_email_compose(row: JobApplication, cl_text: str, send: bool = False) -> dict:
    """Top-level entry from apply_bot: compose + open Mail (or send it).

    send=True -> create the message, attach CV and SEND immediately via Mail
    (uses the user's own Mail account; no SMTP credentials needed).
    send=False -> open a pre-filled draft for the user to review (semi-auto).
    """
    from .apply_bot import build_application_text
    from .cv_loader import resolve_cv_path

    cv_path = resolve_cv_path("zh") or resolve_cv_path("en")
    text = build_application_text(row, cl_text)
    email = build_email(row, text or "（請喺 UI 先生成/編輯 Cover Letter）", cv_path)

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
    subj = _apple_escape(email["subject"])
    body = _apple_escape(email["body"])
    to = _apple_escape(email["to"])
    attach = ""
    if email.get("attachment") and Path(email["attachment"]).exists():
        attach = f'\tadd content file POSIX file "{_apple_escape(str(Path(email["attachment"]).resolve()))}"\n'
    script = f"""
tell application "Mail"
	set newMsg to make new outgoing message with properties {{subject:"{subj}", content:"{body}"}}
	tell newMsg
		make new to recipient at end of to recipients with properties {{address:"{to}"}}
{attach}\tend tell
\tsend newMsg
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
