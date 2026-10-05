"""SMTP 自動寄信：真正做到「自己寄出去」（內文 + CV 附件），唔需要 macOS Mail 權限。

背景：macOS Mail 嘅 AppleScript 路徑要「自動化（TCC）權限」，而權限係跟
「邊個 app 叫 Mail」——由背景服務／agent 跑通常冇（-10004），所以有咗 SMTP
呢條路：Python 直接用 smtplib 連郵箱寄出，完全繞過 macOS 權限。
"""
import asyncio

import pytest

from app.models import Profile
from app.services import email_bot


class _FakeSMTP:
    """假 SMTP client：記低 login／send_message，可設定拋錯。"""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.logged_in = None
        self.sent: list = []
        self.started_tls = False
        self.quit_called = False
        self.error = _FakeSMTP.error_to_raise
        _FakeSMTP.instances.append(self)

    error_to_raise: Exception | None = None

    def starttls(self, context=None):
        self.started_tls = True
        if self.error:
            raise self.error

    def login(self, user, password):
        if self.error:
            raise self.error
        self.logged_in = (user, password)

    def send_message(self, msg):
        if self.error:
            raise self.error
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


@pytest.fixture()
def smtp_settings(db):
    db.add(Profile(id=1, send_method="smtp", smtp_host="smtp.example.com",
                   smtp_port=587, smtp_user="me@example.com",
                   smtp_password="app-password", smtp_from_name="Lai Shu Lap",
                   smtp_from_email="me@example.com", smtp_use_ssl=False,
                   smtp_bcc_self=True))
    db.commit()
    _FakeSMTP.instances = []
    _FakeSMTP.error_to_raise = None
    return db


def test_smtp_ready_requires_all_fields(db):
    assert email_bot.smtp_ready() is False
    db.add(Profile(id=1, smtp_host="h", smtp_user="u", smtp_password="p",
                   smtp_from_email="f@x.com"))
    db.commit()
    assert email_bot.smtp_ready() is True


def test_send_email_smtp_sends_with_attachment(monkeypatch, smtp_settings, tmp_path):
    cv = tmp_path / "cv_zh.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv")
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)

    ok, note = email_bot.send_email_smtp({
        "to": "hr@company.com", "subject": "應徵：AI Engineer",
        "body": "你好，附件係我嘅履歷。", "attachment": str(cv),
    })
    assert ok is True
    assert "已自動寄出" in note and "cv_zh.pdf" in note

    client = _FakeSMTP.instances[-1]
    assert client.logged_in == ("me@example.com", "app-password")
    assert client.started_tls is True            # 587 -> STARTTLS
    assert client.quit_called is True
    msg = client.sent[0]
    assert msg["To"] == "hr@company.com"
    assert "Lai Shu Lap" in msg["From"]
    assert msg["Bcc"] == "me@example.com"        # BCC 備份
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["cv_zh.pdf"]                # CV 真係附咗


def test_send_email_smtp_uses_ssl_when_configured(monkeypatch, smtp_settings):
    smtp_settings.get(Profile, 1).smtp_use_ssl = True
    smtp_settings.commit()
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)

    ok, _note = email_bot.send_email_smtp({
        "to": "hr@x.com", "subject": "s", "body": "b", "attachment": ""})
    assert ok is True
    assert _FakeSMTP.instances[-1].started_tls is False    # SSL 模式唔會 STARTTLS


def test_send_email_smtp_auth_failure_gives_app_password_hint(monkeypatch, smtp_settings):
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    _FakeSMTP.error_to_raise = Exception("535 5.7.8 Username and Password not accepted")

    ok, note = email_bot.send_email_smtp({
        "to": "hr@x.com", "subject": "s", "body": "b", "attachment": ""})
    assert ok is False
    assert "應用程式密碼" in note and "SMTP 登入失敗" in note


def test_send_email_smtp_without_config(db):
    ok, note = email_bot.send_email_smtp({
        "to": "hr@x.com", "subject": "s", "body": "b", "attachment": ""})
    assert ok is False
    assert "SMTP 未設定" in note


def test_test_smtp_reports_ok_and_errors(monkeypatch, smtp_settings):
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    assert email_bot.test_smtp()["ok"] is True
    assert "登入成功" in email_bot.test_smtp()["note"]

    _FakeSMTP.instances = []
    _FakeSMTP.error_to_raise = Exception("getaddrinfo failed")
    out = email_bot.test_smtp()
    assert out["ok"] is False and "SMTP 寄信失敗" in out["note"]


# ---------------------------------------------- 投遞流程會用 SMTP（唔洗手動 mailto）

def _row(db, jid="smtp1"):
    from app.models import CoverLetter, JobApplication

    row = JobApplication(platform="govhk", job_id_on_platform=jid,
                         title="系統工程師", company="測試公司", jd_language="zh",
                         status="pending_review", contact_email="hr@company.com")
    db.add(row)
    db.flush()
    db.add(CoverLetter(application_id=row.id, language="zh", content="原文 CL", version=1))
    db.commit()
    return row


def test_apply_auto_sends_via_smtp(monkeypatch, smtp_settings, tmp_path):
    """send=True + 已設定 SMTP -> 真係自動寄出（唔會開 Mail、唔會 mailto）。"""
    cv = tmp_path / "CV_zh.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    import app.services.cv_loader as cvl

    monkeypatch.setattr(cvl, "resolve_cv_for_job", lambda title, lang: (str(cv), "default"))
    monkeypatch.setattr(email_bot, "polish_email_body", _identity_polish)

    def boom(*a, **k):
        raise AssertionError("SMTP 路徑唔應該叫 Mail")

    monkeypatch.setattr(email_bot, "send_email_via_mail", boom)
    monkeypatch.setattr(email_bot, "fallback_mailto", boom)

    row = _row(smtp_settings)
    res = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=True))
    assert res["submitted"] is True
    assert res["kind"] == "email_sent"
    assert "已自動寄出" in res["message"]
    assert _FakeSMTP.instances[-1].sent            # 真係寄咗


def test_apply_with_method_smtp_reports_error_instead_of_mailto(monkeypatch, smtp_settings):
    """send_method=smtp 但寄唔出 -> 直接報錯（唔會靜靜改用 Mail／mailto）。"""
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    _FakeSMTP.error_to_raise = Exception("connection refused")
    import app.services.cv_loader as cvl

    monkeypatch.setattr(cvl, "resolve_cv_for_job", lambda title, lang: (__file__, "default"))
    monkeypatch.setattr(email_bot, "polish_email_body", _identity_polish)

    def boom(*a, **k):
        raise AssertionError("唔應該行 Mail／mailto")

    monkeypatch.setattr(email_bot, "send_email_via_mail", boom)
    monkeypatch.setattr(email_bot, "fallback_mailto", boom)

    row = _row(smtp_settings, "smtp2")
    res = asyncio.run(email_bot.open_email_compose(row, "原文 CL", send=True))
    assert res["submitted"] is False
    assert res["kind"] == "smtp_failed"


async def _identity_polish(body, lang, ctx=None, instructions=""):
    return body
