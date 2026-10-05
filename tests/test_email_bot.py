"""Email bot tests with subprocess mocked (never opens real Mail)."""
import subprocess

from app.services.email_bot import compose_in_mail, fallback_mailto


def _fake_run(result_returncode: int = 0, stderr: str = ""):
    calls = {}

    def fake_run(args, capture_output=False, text=False, timeout=None):
        calls["args"] = args
        calls["script"] = args[-1]

        class R:
            def __init__(self):
                self.returncode = result_returncode
                self.stdout = ""
                self.stderr = stderr

        return R()

    return fake_run, calls


def test_compose_in_mail_ok(monkeypatch):
    fake_run, calls = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, note = compose_in_mail({
        "to": "hr@example.com",
        "subject": "應徵：AI 工程師（測試公司）",
        "body": "你好，我係申請人。",
        "attachment": "",
    })
    assert ok
    assert "hr@example.com" in calls["script"]
    assert "應徵：AI 工程師" in calls["script"]
    assert "你好" in calls["script"]


def test_compose_in_mail_failure(monkeypatch):
    fake_run, _ = _fake_run(result_returncode=1, stderr="error: something broke")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = compose_in_mail({"to": "hr@example.com", "subject": "s", "body": "b", "attachment": ""})
    assert not ok
    assert "something broke" in note


def test_compose_in_mail_automation_denied_gives_hint(monkeypatch):
    """macOS Automation 權限被拒（-10004）-> 要講人話同教點開權限。"""
    fake_run, _ = _fake_run(
        result_returncode=1,
        stderr='40:1282: execution error: 「Mail」發生錯誤：發生越權取用的錯誤。 (-10004)')
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = compose_in_mail({"to": "hr@example.com", "subject": "s", "body": "b", "attachment": ""})
    assert not ok
    assert "越權" not in note or "自動化權限" in note
    assert "自動化" in note and "Mail" in note


def test_send_email_via_mail_automation_denied_gives_hint(monkeypatch):
    from app.services.email_bot import send_email_via_mail

    fake_run, _ = _fake_run(result_returncode=1, stderr="execution error: (-10004)")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = send_email_via_mail({"to": "hr@example.com", "subject": "s", "body": "b",
                                    "attachment": ""})
    assert not ok
    assert "自動化權限" in note
    assert "系統設定" in note          # 有教路點開


def test_mail_access_probe_uses_a_real_event(monkeypatch):
    """唔可以用 `get version` 做權限判斷（macOS 未批准都會答版本 -> 假陽性）。"""
    from app.services import email_bot

    seen = []

    def run(args, capture_output=True, text=False, timeout=None):
        seen.append(args[-1])

        class R:
            returncode = 0
            stdout = "2" if "count of accounts" in args[-1] else "16.0"
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", run)
    info = email_bot.mail_access()
    assert info["ok"] is True
    assert any("count of accounts" in s for s in seen)      # 有做真事件探針
    assert "帳戶數 2" in info["note"]


def test_mail_selftest_ok(monkeypatch):
    from app.services import email_bot

    def run(args, capture_output=True, text=False, timeout=None):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", run)
    assert email_bot.mail_selftest()["ok"] is True


def test_mail_selftest_denied(monkeypatch):
    from app.services import email_bot

    def run(args, capture_output=True, text=False, timeout=None):
        class R:
            returncode = 1
            stdout = ""
            stderr = "39:160: execution error: 「Mail」發生錯誤：發生越權取用的錯誤。 (-10004)"
        return R()

    monkeypatch.setattr(subprocess, "run", run)
    info = email_bot.mail_selftest()
    assert info["ok"] is False
    assert "-10004" in info["error"]
    assert "自動化權限" in info["note"]


def test_mail_access_ok(monkeypatch):
    from app.services import email_bot

    def run_ok(args, capture_output=False, text=False, timeout=None):
        class R:
            returncode = 0
            stdout = "16.0"
            stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", run_ok)
    info = email_bot.mail_access()
    assert info["ok"] is True and info["version"] == "16.0"


def test_permission_hint_names_the_host_app(monkeypatch):
    """權限被拒嘅訊息要講出「邊個 app 叫 Mail」（TCC 就係認住佢）。"""
    from app.services import email_bot

    monkeypatch.setenv("__CFBundleIdentifier", "com.apple.Terminal")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    assert "com.apple.Terminal" in email_bot.host_app()
    hint = email_bot.permission_hint()
    assert "com.apple.Terminal" in hint
    assert "tccutil reset AppleEvents" in hint


def test_host_app_unknown_for_background_service(monkeypatch):
    from app.services import email_bot

    monkeypatch.delenv("__CFBundleIdentifier", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert "背景服務" in email_bot.host_app()


def test_mail_access_denied(monkeypatch):
    def run_denied(args, capture_output=False, text=False, timeout=None):
        class R:
            returncode = 1
            stdout = ""
            stderr = "「Mail」發生錯誤：發生越權取用的錯誤。 (-10004)"
        return R()

    monkeypatch.setattr(subprocess, "run", run_denied)
    from app.services import email_bot

    info = email_bot.mail_access()
    assert info["ok"] is False
    assert "自動化權限" in info["note"]


def test_fallback_mailto(monkeypatch):
    calls = {}

    def fake_run(args, capture_output=False, text=False, timeout=None, **kwargs):
        calls.setdefault("argv", []).append(args[0])
        if args[0] == "open":
            calls["open_url"] = args[1]
        elif args[0] == "osascript":
            calls["clip_script"] = args[-1]

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = fallback_mailto({
        "to": "hr@example.com",
        "subject": "應徵：AI 工程師",
        "body": "內文內容",
    })
    assert ok
    assert calls["open_url"].startswith("mailto:hr@example.com")
    assert "內文內容" in calls["clip_script"]


# ------------------------------------------------------------ auto-send path

def test_send_email_via_mail_ok(monkeypatch, tmp_path):
    from app.services.email_bot import send_email_via_mail

    cv = tmp_path / "CV_zh.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")

    fake_run, calls = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = send_email_via_mail({
        "to": "hr@example.com",
        "subject": "應徵：AI 工程師",
        "body": "你好，內文。",
        "attachment": str(cv),
    })
    assert ok
    script = calls["script"]
    assert "send newMsg" in script          # actually sends
    assert "hr@example.com" in script
    assert "CV_zh.pdf" in script            # attachment included


def test_send_email_via_mail_missing_recipient():
    from app.services.email_bot import send_email_via_mail

    ok, note = send_email_via_mail({"to": "", "subject": "s", "body": "b", "attachment": ""})
    assert not ok
    assert "冇聯絡 email" in note


def test_send_email_via_mail_failure_falls_to_draft(monkeypatch, tmp_path):
    """When auto-send fails, open_email_compose must fall back to a draft."""
    import asyncio

    from app.models import JobApplication
    from app.services.email_bot import open_email_compose

    cv = tmp_path / "CV_zh.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")

    class FakeRow:
        title = "AI 工程師"
        company = "測試公司"
        platform = "govhk"
        contact_email = "hr@example.com"
        contact_person = "陳先生"
        jd_language = "zh"

    calls = {"n": 0}

    def fake_run(args, capture_output=False, text=False, timeout=None, **kwargs):
        calls["n"] += 1
        calls["last"] = args
        calls.setdefault("all", []).append(args)

        class R:
            returncode = 1  # send fails
            stdout = ""
            stderr = "Mail not configured"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 有 CV 檔案先可以自動寄（新守門：冇 CV 唔會自動寄）
    import app.services.cv_loader as _cvl
    monkeypatch.setattr(_cvl, "resolve_cv_for_job",
                        lambda title, lang: (__file__, "default"))
    # 呢個測試想驗「發送失敗 -> 開 draft」：權限 check 要當 OK
    from app.services import email_bot as _eb
    monkeypatch.setattr(_eb, "mail_access",
                        lambda: {"ok": True, "version": "16.0", "note": "ok"})
    result = asyncio.run(open_email_compose(FakeRow(), "CL 內容", send=True))
    assert result["submitted"] is False
    assert "自動發送" in result["message"]
    # 有試過直接發送 + 開 draft（兩次 Mail AppleScript 都試過）
    mail_calls = [a for a in calls["all"] if 'tell application "Mail"' in " ".join(a)]
    assert len(mail_calls) >= 2
    # 兩條 Mail 路都失敗 -> 最後一著 fallback（mailto + 剪貼簿），唔會卡死
    assert result["kind"] == "email_fallback"
    assert "mailto" in result["message"] or "剪貼簿" in result["message"]


def test_open_email_compose_polishes_body(monkeypatch, db):
    """發送前 AI 潤色**整封 email 內文**（稱呼／簽名保留），並存落 DB。"""
    import asyncio

    from app.models import CoverLetter, JobApplication
    from app.services import email_bot

    row = JobApplication(platform="govhk", job_id_on_platform="polish1",
                         title="系統工程師", company="測試公司", jd_language="zh",
                         jd_text="職責：開發系統。", status="pending_review",
                         contact_email="hr@example.com")
    db.add(row)
    db.flush()
    db.add(CoverLetter(application_id=row.id, language="zh", content="原文 CL", version=1))
    db.commit()

    async def fake_polish(body, lang, ctx=None, instructions=""):
        return "【潤色後嘅整封 email】"

    def fake_compose(email):
        return True, "draft opened"

    def fake_attach(cv_path):
        return True, ""

    monkeypatch.setattr(email_bot, "polish_email_body", fake_polish)
    monkeypatch.setattr(email_bot, "compose_in_mail", fake_compose)
    monkeypatch.setattr(email_bot, "attach_cv_to_draft", fake_attach)
    monkeypatch.setattr("app.services.cv_loader.resolve_cv_path",
                        lambda lang, variant="": "")

    result = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=False))
    assert result["ok"] is True
    assert result["preview"]["body"] == "【潤色後嘅整封 email】"
    assert result["preview"]["polished"] is True
    assert "原文 CL" in result["preview"]["body_original"]
    assert "AI 潤色" in result["message"]

    db.expire_all()
    saved = db.get(JobApplication, row.id)
    assert saved.email_body_polished == "【潤色後嘅整封 email】"
    assert saved.email_polish_key            # cache key 記低咗


def test_open_email_compose_polish_failure_uses_original(monkeypatch, db):
    """AI 潤色失敗 -> 照用原文模板，唔會阻礙發送。"""
    import asyncio

    from app.models import CoverLetter, JobApplication
    from app.services import email_bot
    from app.services.polish import PolishError

    row = JobApplication(platform="govhk", job_id_on_platform="polish2",
                         title="系統工程師", company="測試公司", jd_language="zh",
                         status="pending_review", contact_email="hr@example.com")
    db.add(row)
    db.flush()
    db.add(CoverLetter(application_id=row.id, language="zh", content="原文 CL", version=1))
    db.commit()

    async def fake_polish(body, lang, ctx=None, instructions=""):
        raise PolishError("LLM down")

    def fake_compose(email):
        return True, "draft opened"

    def fake_attach(cv_path):
        return True, ""

    monkeypatch.setattr(email_bot, "polish_email_body", fake_polish)
    monkeypatch.setattr(email_bot, "compose_in_mail", fake_compose)
    monkeypatch.setattr(email_bot, "attach_cv_to_draft", fake_attach)
    monkeypatch.setattr("app.services.cv_loader.resolve_cv_path",
                        lambda lang, variant="": "")

    result = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=False))
    assert result["ok"] is True
    assert "原文 CL" in result["preview"]["body"]
    assert result["preview"]["polished"] is False

    db.expire_all()
    saved = db.get(JobApplication, row.id)
    assert saved.email_body_polished == ""      # 冇存過潤色版


def test_email_polish_cache_skips_second_llm_call(monkeypatch, db):
    """同一封（同一 CL 內容／模板）再撳預覽唔會再洗 LLM。"""
    import asyncio

    from app.models import CoverLetter, JobApplication
    from app.services import email_bot

    row = JobApplication(platform="govhk", job_id_on_platform="polish3",
                         title="系統工程師", company="測試公司", jd_language="zh",
                         status="pending_review", contact_email="hr@example.com")
    db.add(row)
    db.flush()
    db.add(CoverLetter(application_id=row.id, language="zh", content="原文 CL", version=1))
    db.commit()

    calls = {"n": 0}

    async def fake_polish(body, lang, ctx=None, instructions=""):
        calls["n"] += 1
        return "潤色版"

    monkeypatch.setattr(email_bot, "polish_email_body", fake_polish)

    key = email_bot.polish_cache_key("原文 CL", "standard", "zh")
    first = asyncio.run(email_bot.build_email_polished(row, "原文 CL", "", "standard"))
    assert first[1] is True and calls["n"] == 1
    assert first[0]["body"] == "潤色版"

    # 第二次：cache 命中 -> 唔會再 call LLM
    row.email_polish_key = key
    row.email_body_polished = "潤色版"
    second = asyncio.run(email_bot.build_email_polished(row, "原文 CL", "", "standard"))
    assert second[1] is True and calls["n"] == 1

    # 換咗 CL -> key 唔同 -> 會重新潤色
    third = asyncio.run(email_bot.build_email_polished(row, "改過嘅 CL", "", "standard"))
    assert third[1] is True and calls["n"] == 2


def test_email_polish_disabled_uses_template(monkeypatch, db):
    """設定頁關咗潤色 -> 0 次 LLM，照用模板原文。"""
    import asyncio

    from app.models import JobApplication, Profile
    from app.services import email_bot

    db.add(Profile(id=1, email_polish_enabled=False))
    row = JobApplication(platform="govhk", job_id_on_platform="polish4",
                         title="系統工程師", company="測試公司", jd_language="zh",
                         status="pending_review", contact_email="hr@example.com")
    db.add(row)
    db.commit()

    async def fake_polish(body, lang, ctx=None, instructions=""):
        raise AssertionError("唔應該 call LLM")

    monkeypatch.setattr(email_bot, "polish_email_body", fake_polish)
    email, polished, original = asyncio.run(
        email_bot.build_email_polished(row, "原文 CL", "", "standard"))
    assert polished is False
    assert "原文 CL" in email["body"]
    assert email["body"] == original

# ------------------------------------- 冇 Mail 權限／發送失敗 -> 一定要有 fallback

def _prep_row(db, jid="mail1"):
    from app.models import CoverLetter, JobApplication

    row = JobApplication(platform="govhk", job_id_on_platform=jid,
                         title="系統工程師", company="測試公司", jd_language="zh",
                         status="pending_review", contact_email="hr@example.com")
    db.add(row)
    db.flush()
    db.add(CoverLetter(application_id=row.id, language="zh", content="原文 CL", version=1))
    db.commit()
    return row


def test_send_without_mail_permission_uses_fallback(monkeypatch, db):
    """-10004 權限問題：唔好連試兩次都失敗 -> 即刻 fallback（mailto + 剪貼簿）+ 教路。"""
    import asyncio

    from app.services import email_bot

    row = _prep_row(db, "mailperm1")
    calls = {"sent": 0, "composed": 0, "fallback": 0}
    import app.services.cv_loader as _cvl

    monkeypatch.setattr(_cvl, "resolve_cv_for_job", lambda title, lang: (__file__, "default"))

    monkeypatch.setattr(email_bot, "mail_access",
                        lambda: {"ok": False, "version": "", "note": email_bot.MAIL_PERMISSION_HINT})
    monkeypatch.setattr(email_bot, "send_email_via_mail",
                        lambda email: (calls.__setitem__("sent", calls["sent"] + 1), (True, "sent"))[1])
    monkeypatch.setattr(email_bot, "compose_in_mail",
                        lambda email: (calls.__setitem__("composed", calls["composed"] + 1), (True, "draft"))[1])

    def fake_fallback(email):
        calls["fallback"] += 1
        return True, "已開 mailto 並複製內文到剪貼簿。"

    monkeypatch.setattr(email_bot, "fallback_mailto", fake_fallback)
    monkeypatch.setattr("app.services.cv_loader.resolve_cv_path", lambda lang, variant="": "")
    monkeypatch.setattr(email_bot, "polish_email_body", _fake_polish_identity)

    res = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=True))
    assert calls["sent"] == 0 and calls["composed"] == 0     # 冇白試
    assert calls["fallback"] == 1                            # 有 fallback
    assert res["kind"] == "email_fallback"
    assert res["submitted"] is False
    assert "自動化權限" in res["message"]                     # 有教點開權限


async def _fake_polish_identity(body, lang, ctx=None, instructions=""):
    return body


def test_send_and_draft_failure_still_falls_back(monkeypatch, db):
    """權限 check 過但實際發送／開 draft 都失敗 -> 最後一著 mailto。"""
    import asyncio

    from app.services import email_bot

    row = _prep_row(db, "mailperm2")
    calls = {"fallback": 0}
    import app.services.cv_loader as _cvl

    monkeypatch.setattr(_cvl, "resolve_cv_for_job", lambda title, lang: (__file__, "default"))

    monkeypatch.setattr(email_bot, "mail_access",
                        lambda: {"ok": True, "version": "16.0", "note": "ok"})
    monkeypatch.setattr(email_bot, "send_email_via_mail",
                        lambda email: (False, "Mail 發送失敗: boom"))
    monkeypatch.setattr(email_bot, "compose_in_mail",
                        lambda email: (False, "AppleScript 失敗: boom"))

    def fake_fallback(email):
        calls["fallback"] += 1
        return True, "已開 mailto 並複製內文到剪貼簿。"

    monkeypatch.setattr(email_bot, "fallback_mailto", fake_fallback)
    monkeypatch.setattr("app.services.cv_loader.resolve_cv_path", lambda lang, variant="": "")
    monkeypatch.setattr(email_bot, "polish_email_body", _fake_polish_identity)

    res = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=True))
    assert calls["fallback"] == 1
    assert res["kind"] == "email_fallback"
    assert res["submitted"] is False
    assert "boom" in res["message"]
