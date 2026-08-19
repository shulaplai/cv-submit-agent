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
