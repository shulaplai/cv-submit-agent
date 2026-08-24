"""Shared job-track classification: IT vs 一般 (non-IT).

Every job lands on exactly one of two tracks:
  - ``it``       jobs whose title matches the IT keywords
  - ``general``  everything else that matches the general-track keywords

Keyword resolution (user-adjustable in the Settings page):
  IT       profile.it_keywords / .env JOB_KEYWORDS  UNION built-in defaults
           (user keywords first — they double as the focused search terms;
           the built-ins are the safety net so e.g. "Software Engineer" is
           never classified as non-IT because the user list is narrow)
  general  profile.general_job_keywords -> .env GENERAL_JOB_KEYWORDS -> built-in defaults
           (a whitelist: only jobs matching these are kept on the 一般 page)

Matching: CJK keywords are substring matches; latin keywords are whole-word
(case-insensitive) so "data" does not match "database" and "it" does not
match "its".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import settings

# Union of the keyword sets previously duplicated across the scrapers and
# apply_bot. A title matching any of these is an IT / tech job.
DEFAULT_IT_KEYWORDS = [
    "ai", "agent", "developer", "programmer", "programming", "software",
    "engineer", "engineering", "frontend", "backend", "full stack", "full-stack",
    "python", "javascript", "typescript", "java", "node", "react", "sql",
    "database", "devops", "cloud", "llm", "machine learning", "deep learning",
    "ml ", "data ", "algorithm", "qa", "it ", "it support", "system admin",
    "systems admin", "system administrator", "systems administrator", "sysadmin",
    "helpdesk", "help desk", "desktop support", "technical support", "network",
    "資訊科技", "資訊技術", "工程師", "程式", "程序", "程式設計", "系統", "軟件",
    "軟體", "前端", "後端", "人工智能", "技術員", "數據", "數據分析", "大模型",
    "模型", "機器學習", "深度學習", "網絡", "網路", "網絡安全", "雲端", "雲",
    "全棧", "計算機", "計算機科學", "編程", "演算法", "編碼", "科技", "開發",
    "測試", "技術支援", "桌面", "維護",
]

# Non-IT (一般) track: office / admin / customer-service style roles the
# applicant might accept as backup. Broadly editable in Settings.
DEFAULT_GENERAL_KEYWORDS = [
    "文員", "行政助理", "辦公室助理", "秘書", "客戶服務", "會計", "助理",
    "營運", "跟單", "採購", "資料輸入", "接待員", "店務", "收銀",
    "clerk", "admin", "assistant", "secretary", "customer service",
    "accounting", "operation", "merchandiser", "purchasing", "receptionist",
]


def parse_keywords(text: str) -> list[str]:
    """Split a comma-separated keyword string into cleaned non-empty tokens."""
    return [k.strip() for k in (text or "").split(",") if k.strip()]


def resolve_it_keywords(profile_text: str = "") -> list[str]:
    """Effective IT keywords: user list (profile -> .env) UNION built-in defaults.

    User keywords come first so they double as the focused JobsDB/OfferToday
    search terms; the defaults guarantee a sane classification breadth even
    when the user list is narrow (e.g. only "AI Engineer").
    """
    user = []
    if profile_text.strip():
        user = parse_keywords(profile_text)
    elif (settings.JOB_KEYWORDS or "").strip():
        user = parse_keywords(settings.JOB_KEYWORDS)
    seen: set[str] = set()
    merged = [k for k in user if not (k in seen or seen.add(k))]
    for k in DEFAULT_IT_KEYWORDS:
        if k not in seen:
            seen.add(k)
            merged.append(k)
    return merged


def resolve_general_keywords(profile_text: str = "") -> list[str]:
    """Effective general-track keywords: profile -> .env -> built-in defaults."""
    if profile_text.strip():
        kws = parse_keywords(profile_text)
        if kws:
            return kws
    if (settings.GENERAL_JOB_KEYWORDS or "").strip():
        kws = parse_keywords(settings.GENERAL_JOB_KEYWORDS)
        if kws:
            return kws
    return list(DEFAULT_GENERAL_KEYWORDS)


def match_keyword(keyword: str, text: str) -> bool:
    """One keyword against text: CJK -> substring; latin -> whole-word.

    Latin boundaries use lookarounds on [a-zA-Z0-9] instead of ``\\b`` so a
    latin keyword directly attached to CJK still matches (e.g. "ai" in
    "AI基礎架構"), while "data" still does not match "database".
    """
    kw = keyword.strip()
    if not kw:
        return False
    if any(ord(c) > 127 for c in kw):
        return kw in text
    return re.search(
        rf"(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])",
        text,
        re.IGNORECASE,
    ) is not None


def title_matches(title: str, keywords: list[str]) -> bool:
    """True when any keyword matches the (lowercased) title."""
    if not keywords or not title:
        return False
    return any(match_keyword(k, title) for k in keywords)


def classify(title: str, it_keywords: list[str] | None = None) -> str:
    """Classify a job title into 'it' or 'general'.

    IT wins when the title matches the IT keywords; everything else is
    'general'. (JD text is deliberately NOT considered — JDs mention computers
    everywhere and would drown the general track.)
    """
    kws = it_keywords if it_keywords is not None else resolve_it_keywords()
    return "it" if title_matches(title, kws) else "general"


@dataclass
class TrackConfig:
    """Per-track scan settings passed down to the scrapers.

    One instance per enabled track (it / general). The scanner builds these
    from the user's Profile (Settings page) with .env / built-in fallbacks;
    ``defaults()`` is the no-DB fallback used by tests and run_once().
    """
    name: str                 # "it" | "general"
    label: str                # "IT" | "一般"
    keywords: list[str]       # filter keywords for this track
    it_keywords: list[str]    # IT classification keywords (exclusion for general)
    govhk_max_jobs: int       # gov.hk per-scan cap (IT category / general quickview)
    offertoday_max_per_search: int
    offertoday_search_terms: list[str] = field(default_factory=list)
    max_searches: int = 0     # OfferToday general: cap on keyword searches

    @staticmethod
    def defaults(name: str) -> "TrackConfig":
        """Track config from pure settings/env — used when no Profile row exists."""
        it_kws = resolve_it_keywords()
        if name == "it":
            return TrackConfig(
                name="it", label="IT",
                keywords=it_kws, it_keywords=it_kws,
                govhk_max_jobs=settings.GOVHK_IT_MAX_JOBS,
                offertoday_max_per_search=settings.OFFERTODAY_MAX_PER_SEARCH,
            )
        general_kws = resolve_general_keywords()
        return TrackConfig(
            name="general", label="一般",
            keywords=general_kws, it_keywords=it_kws,
            govhk_max_jobs=settings.GOVHK_GENERAL_MAX_JOBS,
            offertoday_max_per_search=settings.OFFERTODAY_GENERAL_MAX_PER_SEARCH,
            offertoday_search_terms=(
                parse_keywords(settings.OFFERTODAY_GENERAL_SEARCH_TERMS) or general_kws
            ),
            max_searches=settings.OFFERTODAY_GENERAL_MAX_SEARCHES,
        )
