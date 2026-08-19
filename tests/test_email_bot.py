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
    result = asyncio.run(open_email_compose(FakeRow(), "CL 內容", send=True))
    assert result["submitted"] is False
    assert "自動發送" in result["message"]
    assert "Mail" in " ".join(calls["last"])
