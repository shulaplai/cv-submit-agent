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


# macOS Automation（自動化）權限被拒／未開：AppleScript 會回 -10004（errAEEventNotPermitted）
_AUTOMATION_DENIED_MARKERS = ("-10004", "越權", "not authorized", "not permitted",
                              "errAEEventNotPermitted")

def host_app() -> str:
    """邊個 app 啟動咗呢個 process（macOS TCC 自動化權限就係認住佢）。

    ``__CFBundleIdentifier`` 由父 app 繼承落嚟，所以由 Terminal 跑就會見到
    ``com.apple.Terminal``；由 VS Code 跑就係 ``com.microsoft.VSCode``。
    診斷訊息會講返出嚟，等你知去「系統設定 → 自動化」邊一行勾。
    """
    import os

    bundle = (os.environ.get("__CFBundleIdentifier") or "").strip()
    term = (os.environ.get("TERM_PROGRAM") or "").strip()
    if bundle:
        return f"{term or bundle}（{bundle}）"
    return term or "（查唔到，可能係背景服務）"


def permission_hint() -> str:
    """權限被拒嘅完整教路（含「啟動 server 嘅 app」名）。"""
    return (
        f"{MAIL_PERMISSION_HINT}\n"
        f"👉 你嘅 server 而家由 **{host_app()}** 啟動 —— 喺「自動化」清單揾佢，"
        f"勾返「郵件 / Mail」。\n"
        f"👉 如果佢唔喺清單／勾唔到：喺同一個 Terminal 跑 "
        f"`tccutil reset AppleEvents` 再試（會重新彈授權對話框）。"
    )


MAIL_PERMISSION_HINT = (
    "⚠ macOS 唔准我用 AppleScript 控制 Mail（自動化權限未開或者上次被拒）。"
    "解決：① 系統設定 → 隱私權與安全性 → 自動化 → 揾「終端機／Terminal」"
    "（或你啟動 server 嗰個程式）→ 勾返「郵件 / Mail」；② 之後重啟 server 再試。"
    "（TCC 權限係跟「邊個程式叫 Mail」，所以由你自己嘅 Terminal 跑 ./run.sh 最穩）"
)


def _applescript_error(stderr: str, *, what: str = "AppleScript") -> str:
    """AppleScript 錯誤 -> 人話。特別處理 macOS Automation 權限被拒（-10004）。"""
    text = (stderr or "").strip()
    low = text.lower()
    if any(m.lower() in low for m in _AUTOMATION_DENIED_MARKERS):
        return permission_hint()
    return f"{what} 失敗: {text[:300]}"


# ⚠ 唔可以用 `get version` 做權限檢查：macOS 唔需要 Automation 權限就答得到
#   （`get name` 一樣），所以會出現「檢查 OK 但真發送 -10004」嘅假陽性。
#   `count of accounts` 係真 Apple Event：有權限就回數字，冇權限就 -10004。
ACCESS_PROBE = 'tell application "Mail" to count of accounts'


def mail_access() -> dict:
    """檢查「呢個 process 可唔可以真正控制 Mail」（唔會開信、唔會寄信）。"""
    try:
        r = subprocess.run(["osascript", "-e", ACCESS_PROBE],
                           capture_output=True, text=True, timeout=15)
        version = subprocess.run(["osascript", "-e", 'tell application "Mail" to get version'],
                                 capture_output=True, text=True, timeout=15)
        ver = (version.stdout or "").strip() if version.returncode == 0 else ""
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "version": "", "note": f"跑唔到 osascript：{e}"}
    if r.returncode == 0:
        accounts = (r.stdout or "").strip()
        return {"ok": True, "version": ver, "host_app": host_app(),
                "note": f"✓ 可以控制 Mail（版本 {ver}，帳戶數 {accounts}）—— "
                        f"自動發送／開 draft 應該正常"}
    log.warning("mail permission probe failed: %s", (r.stderr or "").strip()[:300])
    return {"ok": False, "version": ver, "host_app": host_app(),
            "note": _applescript_error(r.stderr, what="檢查 Mail 權限")}


SELFTEST_SCRIPT = """
tell application "Mail"
	set newMsg to make new outgoing message with properties {subject:"[cv-submit] Mail 權限測試", content:"呢封係權限測試，會即刻關閉，唔會寄出。", visible:false}
	close newMsg saving no
end tell
""".strip()


def mail_selftest() -> dict:
    """真正做一次「開一封 draft」（冇收件人、即刻關閉、唔會寄出）。

    `get version` 成功唔代表 `make new outgoing message` 一定成功（TCC 權限／Mail
    狀態都可能唔同），所以診斷要用呢個。失敗時回傳人話 + 原始錯誤。
    """
    try:
        r = subprocess.run(["osascript", "-e", SELFTEST_SCRIPT],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"跑唔到 osascript：{e}", "note": "", "stderr": ""}
    if r.returncode == 0:
        return {"ok": True, "error": "", "stderr": "", "host_app": host_app(),
                "note": "✓ 可以開 Mail draft（＝發 email 申請嘅第一步冇問題）"}
    note = _applescript_error(r.stderr, what="Mail 自我測試")
    log.warning("mail selftest failed: %s", (r.stderr or "").strip()[:300])
    return {"ok": False, "error": (r.stderr or "").strip()[:300], "note": note,
            "host_app": host_app(), "stderr": (r.stderr or "").strip()[:300]}


def smtp_config(tuning=None) -> dict:
    """有效 SMTP 設定（Settings 頁 -> .env）。"""
    from .tuning import load_tuning

    t = tuning or load_tuning()
    return {
        "method": (t.send_method or "auto").strip().lower(),
        "host": (t.smtp_host or "").strip(),
        "port": int(t.smtp_port or 587),
        "user": (t.smtp_user or "").strip(),
        "password": t.smtp_password or "",
        "from_name": (t.smtp_from_name or settings.APPLICANT_NAME or "").strip(),
        "from_email": (t.smtp_from_email or t.smtp_user or "").strip(),
        "use_ssl": bool(t.smtp_use_ssl),
        "bcc_self": bool(t.smtp_bcc_self),
    }


def smtp_ready(cfg: dict | None = None) -> bool:
    """SMTP 設定齊唔齊（host + user + password + 寄件人）。"""
    cfg = cfg or smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"] and cfg["from_email"])


def _smtp_error_hint(err: Exception, cfg: dict) -> str:
    text = str(err)
    low = text.lower()
    if "auth" in low or "535" in text or "534" in text or "535" in text:
        return (f"SMTP 登入失敗（{cfg['user']}@{cfg['host']}）："
                "多數要用「應用程式密碼」而唔係你平時嘅登入密碼 —— "
                "Gmail：Google 帳戶 → 安全性 → 兩步驗證 → 應用程式密碼（16 位）；"
                "iCloud：Apple 帳戶 → 登入與安全性 → 應用程式專用密碼；"
                "Outlook/公司 Mail 就可能要管理員開 SMTP AUTH。"
                f"（原始錯誤：{text[:200]}）")
    if "certificate" in low or "ssl" in low:
        return (f"SMTP TLS/SSL 出錯：465 通常要開「用 SSL」；587 用 STARTTLS。"
                f"（原始錯誤：{text[:200]}）")
    if "timed out" in low or "timeout" in low:
        return f"SMTP 連線逾時（{cfg['host']}:{cfg['port']}）—— 檢查網絡／port 有冇被封。（{text[:160]}）"
    return f"SMTP 寄信失敗：{text[:300]}"


def send_email_smtp(email: dict, cfg: dict | None = None) -> tuple[bool, str]:
    """用 SMTP 直接寄出（內文 + CV 附件），**唔需要 macOS Mail 權限**。"""
    import smtplib
    import ssl
    from email.message import EmailMessage

    cfg = cfg or smtp_config()
    if not email.get("to"):
        return False, "呢份工冇聯絡 email，唔可以寄。"
    if not smtp_ready(cfg):
        return False, "SMTP 未設定好（要去設定頁填 host／帳號／應用程式密碼）。"

    msg = EmailMessage()
    msg["Subject"] = email.get("subject", "")
    msg["From"] = (f"{cfg['from_name']} <{cfg['from_email']}>" if cfg["from_name"]
                   else cfg["from_email"])
    msg["To"] = email["to"]
    if cfg["bcc_self"] and cfg["from_email"]:
        msg["Bcc"] = cfg["from_email"]
    msg.set_content(email.get("body", ""))

    attachment = email.get("attachment") or ""
    if attachment and Path(attachment).exists():
        data = Path(attachment).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=Path(attachment).name)

    try:
        context = ssl.create_default_context()
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=45,
                                      context=context)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=45)
        try:
            if not cfg["use_ssl"]:
                server.starttls(context=context)
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        note = _smtp_error_hint(e, cfg)
        log.warning("SMTP send failed: %s", note)
        return False, note

    attach_note = f"（已附 CV：{Path(attachment).name}）" if attachment else ""
    bcc_note = f"（已 BCC 一份去 {cfg['from_email']}）" if (cfg["bcc_self"] and cfg["from_email"]) else ""
    log.info("SMTP sent to %s %s", email["to"], attach_note)
    return True, f"✔ Email 已自動寄出（{email['to']}）{attach_note}{bcc_note}"


def test_smtp(cfg: dict | None = None) -> dict:
    """連線 + 登入測試（唔會寄信）。回傳 {ok, note}。"""
    import smtplib
    import ssl

    cfg = cfg or smtp_config()
    if not cfg["host"]:
        return {"ok": False, "note": "未填 SMTP 伺服器（例如 smtp.gmail.com）"}
    if not (cfg["user"] and cfg["password"]):
        return {"ok": False, "note": "未填 SMTP 帳號或應用程式密碼"}
    try:
        context = ssl.create_default_context()
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20,
                                      context=context)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        try:
            if not cfg["use_ssl"]:
                server.starttls(context=context)
            server.login(cfg["user"], cfg["password"])
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": _smtp_error_hint(e, cfg)}
    return {"ok": True,
            "note": f"✓ SMTP 連線同登入成功（{cfg['user']}@{cfg['host']}:{cfg['port']}）"}


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
            log.warning("Mail compose failed (draft): %s", (result.stderr or "").strip()[:300])
            return False, _applescript_error(result.stderr)
        log.info("Mail draft opened（收件人 %s）", email.get("to", ""))
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
            log.info("CV attached to Mail draft: %s", cv_path)
            return True, "已附上 CV。"
        # attachment of the just-created draft may need the draft selected;
        # surface the error but do not fail the whole flow.
        log.warning("attach CV failed: %s", r.stderr.strip()[:200])
        msg = _applescript_error(r.stderr, what="自動附件")
        if msg == MAIL_PERMISSION_HINT:
            return False, "開咗郵件但自動附件失敗（權限問題），請手動加上 CV。"
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
        # 1) SMTP 優先：直接由 Python 寄出（內文 + CV 附件），完全唔需要 macOS 權限
        cfg_smtp = smtp_config()
        method = cfg_smtp["method"] or "auto"
        use_smtp = method == "smtp" or (method == "auto" and smtp_ready(cfg_smtp))
        if use_smtp:
            ok_s, note_s = send_email_smtp(email, cfg_smtp)
            if ok_s:
                return {"ok": True, "kind": "email_sent", "to": email["to"],
                        "message": f"{note_s} {polish_tag}{cv_tag}".strip(),
                        "submitted": True, "preview": _preview()}
            if method == "smtp":
                # 用戶指定用 SMTP -> 唔好靜靜改用 Mail，直接報錯
                return {"ok": False, "kind": "smtp_failed", "to": email["to"],
                        "message": f"{note_s} {polish_tag}{cv_tag}".strip(),
                        "submitted": False, "preview": _preview()}
            log.warning("SMTP 失敗，改用 macOS Mail：%s", note_s)

        # 2) macOS Mail（AppleScript）：要「自動化權限」
        access = mail_access()
        if not access["ok"]:
            ok2, note2 = fallback_mailto(email)
            msg = f"{note2} {polish_tag}{cv_tag}".strip()
            return {"ok": ok2, "kind": "email_fallback", "to": email["to"],
                    "message": (f"{msg}\n{access['note']}\n"
                                "💡 想完全自動寄出（連 CV 附件）唔想再撞權限問題："
                                "去設定頁填 SMTP（Gmail／iCloud 應用程式密碼），"
                                "之後會直接用 SMTP 寄，唔需要 macOS Mail。"),
                    "submitted": False, "preview": _preview()}
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
        # 兩條 Mail 路都失敗 -> 最後一著：mailto + 剪貼簿（唔會卡死）
        ok3, note3 = fallback_mailto(email)
        return {"ok": ok3, "kind": "email_fallback" if ok3 else "email_failed",
                "to": email["to"],
                "message": f"自動發送同開 Mail 都失敗：{note}；{note2}。{note3}",
                "submitted": False, "preview": _preview()}

    # 半自動模式：SMTP 模式下唔應該開 Mail（會撞權限又冇必要）—— 直接話俾用戶知
    # 「撳確認就會自動寄出」，內文同 CV 都已經備好。
    cfg_semi = smtp_config()
    if (cfg_semi["method"] == "smtp"
            or (cfg_semi["method"] == "auto" and smtp_ready(cfg_semi))):
        return {"ok": True, "kind": "needs_confirm", "to": email["to"],
                "message": (f"SMTP 模式：內文（已潤色）＋ CV 附件已經備好，"
                            f"唔會開 Mail。撳「確認並自動發送」就會即刻寄出。"
                            f" {polish_tag}{cv_tag}").strip(),
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
            log.info("Mail auto-sent to %s", email.get("to", ""))
            return True, "Email 已透過 macOS Mail 自動發送。"
        log.warning("Mail auto-send failed: %s", (r.stderr or "").strip()[:300])
        return False, f"Mail 發送失敗: {_applescript_error(r.stderr)}"
    except Exception as e:  # noqa: BLE001
        return False, f"Mail 發送失敗: {e}"
