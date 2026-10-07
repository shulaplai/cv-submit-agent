"""Job fit flags: AI match / 合約 / 外派（獵頭），全部純關鍵字（零 LLM 成本）。

用戶要求（職位台篩選）：
  - ai_match   職位係唔係 AI 相關（標題 或 JD 內文提到 AI 字眼）
  - is_contract 合約／臨時／兼職／實習（用戶想搵穩定工）
  - is_agency   外派／派遣／獵頭／人力資源顧問公司（EA 之類）

判斷用 classify.match_keyword（拉丁字用詞邊界、中日韓字用子字串），所以
"ai" 唔會誤中 "email"/"detail"，而「AI基礎架構」照中。
"""
from __future__ import annotations

import logging

from .classify import match_keyword

log = logging.getLogger(__name__)

# 合約／非穩定字眼（標題層面高精度；JD 層面只睇頭 800 字）
CONTRACT_KEYWORDS = [
    "合約", "臨時", "兼職", "part time", "part-time", "parttime", "時薪",
    "contract", "contractor", "temporary", "temp ", "locum", "freelance",
    "intern", "internship", "實習", "見習", "6-month", "12-month", "fixed term",
    "fixed-term", "project based", "專案制", "替假", "替工",
]

# 外派／獵頭／人力資源公司（EA = Employment Agency）
AGENCY_KEYWORDS = [
    "ea", "e.a.", "外派", "派遣", "派駐", "獵頭", "headhunt", "secondment",
    "outsourc", "outstaff", "recruit", "recruitment", "staffing",
    "employment agency", "人力資源", "人才顧問", "人事顧問", "顧問公司",
    "manpower", "adecco", "randstad", "hays", "robert walters", "michael page",
    "recruit express", "elee", "intellipro", "protalent",
]

_JD_SCAN_CHARS = 800

# 「agent」係一個唔可靠嘅 AI 訊號：香港招聘市場上大部分 "agent" 職位係
# 客服／代理／經紀／報關／訂位／獵頭（Hotline Agent、Property Agent、
# Customs Clearance Support Agent…），完全唔關 AI 事。所以呢個字要
# **配合技術語境**先算 AI 相關；"ai"／"agentic"／"llm" 等字就照舊一 hit 即中。
_WEAK_AI_KEYWORDS = {"agent"}

# 「agent」嘅技術語境字：標題有呢啲 + agent = 真 AI agent／agent 開發職位
# （繁簡都要有：OfferToday 好多大陸公司用簡體字出標題）
_AGENT_CONTEXT_KEYWORDS = [
    "ai", "agentic", "llm", "gpt", "developer", "engineer", "programmer",
    "software", "architect", "platform", "python", "algorithm", "r&d",
    "research", "data", "automation", "bot",
    "工程師", "工程师", "開發", "开发", "架構", "架构", "程式", "程序",
    "軟件", "软件", "系統", "系统", "演算法", "算法", "智能",
    "技術", "技术", "自動化", "自动化", "研究", "數據", "数据",
]


def weak_ai_keywords() -> set[str]:
    """需要配合技術語境先算 AI 嘅字（畀 tuning 排優先豁免字詞用）。"""
    return set(_WEAK_AI_KEYWORDS)


def strong_ai_title_keywords() -> list[str]:
    """一 hit 就當 AI 嘅標題字（＝ AI 標題關鍵字減去弱字）。"""
    from .cv_loader import ai_title_keywords

    return [k for k in ai_title_keywords() if k.lower() not in _WEAK_AI_KEYWORDS]


def _hit(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    return any(match_keyword(k, text) for k in keywords)


def ai_relevance(title: str, jd_text: str = "", extra_text: str = "") -> str:
    """AI 相關度："title"（標題有 AI／agent）/ "jd"（只喺 JD 提到）/ ""（唔相關）。

    用戶要求：只要有「AI」或者「agent」就叫做相關（普通 programmer 唔算），
    而且呢兩類工 scan 到就一定要收（見 classify 嘅優先豁免）。
    標題級相關度較高，所以職位台可以分開排／篩。

    但「agent」單獨出現唔可以當 AI（實測 112 份噪音：Hotline Agent、
    Property Agent、報關 agent…）。呢個字要有技術語境（標題或 JD）先算。
    """
    from .cv_loader import ai_title_keywords

    if not title and not jd_text and not extra_text:
        return ""
    title = title or ""
    kws = ai_title_keywords()
    strong = [k for k in kws if k.lower() not in _WEAK_AI_KEYWORDS]
    weak = [k for k in kws if k.lower() in _WEAK_AI_KEYWORDS]
    if strong and any(match_keyword(k, title) for k in strong):
        return "title"
    if weak and any(match_keyword(k, title) for k in weak):
        if any(match_keyword(c, title) for c in _AGENT_CONTEXT_KEYWORDS):
            return "title"
        blob = f"{jd_text or ''} {extra_text or ''}"[:_JD_SCAN_CHARS]
        if blob.strip() and any(match_keyword(k, blob) for k in kws):
            return "jd"
        return ""
    blob = f"{jd_text or ''} {extra_text or ''}"[:_JD_SCAN_CHARS]
    if blob.strip() and any(match_keyword(k, blob) for k in kws):
        return "jd"
    return ""


def ai_match(title: str, jd_text: str = "", extra_text: str = "") -> bool:
    """標題或 JD 提到 AI／agent（＝相關）。"""
    return bool(ai_relevance(title, jd_text, extra_text))


def is_contract(title: str, jd_text: str = "") -> bool:
    if _hit(title or "", CONTRACT_KEYWORDS):
        return True
    return _hit((jd_text or "")[:_JD_SCAN_CHARS], CONTRACT_KEYWORDS)


def is_agency(title: str, company: str = "", jd_text: str = "") -> bool:
    if _hit(title or "", AGENCY_KEYWORDS) or _hit(company or "", AGENCY_KEYWORDS):
        return True
    return _hit((jd_text or "")[:_JD_SCAN_CHARS], AGENCY_KEYWORDS)


def compute_flags(row) -> dict:
    """砌好三個 flag（ai_match / is_contract / is_agency）。

    ``row`` 只需要有 title / company / jd_text 屬性。
    """
    title = getattr(row, "title", "") or ""
    company = getattr(row, "company", "") or ""
    jd = getattr(row, "jd_text", "") or ""
    strength = ai_relevance(title, jd)
    return {
        "ai_match": bool(strength),
        "ai_strength": strength,
        "is_contract": is_contract(title, jd),
        "is_agency": is_agency(title, company, jd),
    }
