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
    fake_run, _ = _fake_run(result_returncode=1, stderr="error: not authorized")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, note = compose_in_mail({"to": "hr@example.com", "subject": "s", "body": "b", "attachment": ""})
    assert not ok
    assert "not authorized" in note


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
    result = asyncio.run(open_email_compose(FakeRow(), "CL 內容", send=True))
    assert result["submitted"] is False
    assert "自動發送" in result["message"]
    assert "Mail" in " ".join(calls["last"])


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
