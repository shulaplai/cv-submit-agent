"""Email body templates for gov.hk (email) applications.

Each template composes the email body from the applicant's self-intro, the
per-job cover letter, and the job/company context — in the JD language
(中文 JD → 中文 email；英文 JD → 英文 email).
"""
from __future__ import annotations


def list_templates() -> list[dict]:
    """Templates shown in the UI for the user to pick before sending."""
    return [
        {"key": "standard", "label_zh": "標準", "label_en": "Standard",
         "desc": "自我介紹 + Cover Letter + 簽名"},
        {"key": "concise", "label_zh": "簡潔", "label_en": "Concise",
         "desc": "只附 Cover Letter + 簡短簽名"},
        {"key": "formal", "label_zh": "正式", "label_en": "Formal",
         "desc": "正式稱呼 + Cover Letter + 正式結尾"},
        {"key": "direct", "label_zh": "直接", "label_en": "Direct",
         "desc": "直接附 Cover Letter + 簽名（唔加開場）"},
    ]


def compose_body(key: str, ctx: dict) -> str:
    fn = {
        "standard": _standard,
        "concise": _concise,
        "formal": _formal,
        "direct": _direct,
    }.get(key, _standard)
    return fn(ctx)


def _greeting(lang: str, person: str) -> str:
    if person:
        return f"Dear {person}," if lang == "en" else f"{person} 您好，"
    return "Dear Hiring Manager," if lang == "en" else "致招聘經理／人事部："


def _sign_off(lang: str, formal: bool = False) -> str:
    if lang == "en":
        return "Yours faithfully," if formal else "Thank you for your consideration."
    return "此致\n敬禮" if formal else "煩請查收附件履歷，期待貴公司嘅回覆，謝謝。"


def _signature(ctx: dict) -> str:
    parts = [p for p in (ctx.get("applicant_name"), ctx.get("applicant_email")) if p]
    return "\n".join(parts)


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p and p.strip())


def _standard(ctx: dict) -> str:
    lang = ctx.get("lang", "zh")
    parts = [_greeting(lang, ctx.get("contact_person", ""))]
    if ctx.get("intro"):
        parts.append(ctx["intro"])
    parts.append(ctx.get("cl", ""))
    parts.append(_sign_off(lang))
    if _signature(ctx):
        parts.append(_signature(ctx))
    return _join(parts)


def _concise(ctx: dict) -> str:
    lang = ctx.get("lang", "zh")
    parts = [_greeting(lang, ctx.get("contact_person", ""))]
    parts.append(ctx.get("cl", ""))
    parts.append("Thank you for your consideration." if lang == "en" else "謝謝。")
    if _signature(ctx):
        parts.append(_signature(ctx))
    return _join(parts)


def _formal(ctx: dict) -> str:
    lang = ctx.get("lang", "zh")
    parts = ["Dear Hiring Manager," if lang == "en" else "敬啟者："]
    if ctx.get("intro"):
        parts.append(ctx["intro"])
    parts.append(ctx.get("cl", ""))
    parts.append(_sign_off(lang, formal=True))
    if _signature(ctx):
        parts.append(_signature(ctx))
    return _join(parts)


def _direct(ctx: dict) -> str:
    parts = [ctx.get("cl", "")]
    if _signature(ctx):
        parts.append(_signature(ctx))
    return _join(parts)
