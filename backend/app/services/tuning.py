"""Tuning resolution: Settings page (profile row) -> .env -> built-in defaults.

一次過解出「掃描量 / 高分豁免 / LLM 預算 / 節奏 / 潤色 / 求職者資歷」嘅有效值，
令 scanner、routers、apply flow 用同一套判斷（同 classify.resolve_* 一樣嘅
「profile 覆寫 .env」慣例）。

數值欄位用 -1 / 0 做「未設定」哨兵：
  -1 = 完全未設定（用 .env）
   0 = 用戶明確填 0（有意義：例如「唔設上限」、「0 秒延遲」）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings

# 擴散到 profile 欄位嘅哨兵
_UNSET_INT = -1
_UNSET_FLOAT = 0.0   # 0 = 用 .env（延遲 0 秒冇實際用途，所以 0 當未設定）


@dataclass
class Tuning:
    """有效掃描／潤色設定。"""

    # ---- 掃描量 ----
    max_scan_jobs: int = 0
    offertoday_it_max_searches: int = 0
    offertoday_general_max_searches: int = 0
    # ---- 高分豁免 ----
    cap_bypass_enabled: bool = True
    cap_bypass_min_score: int = 70
    priority_keywords_text: str = ""
    priority_extra_max: int = 50
    # ---- LLM 預算 ----
    max_enrich_per_scan: int = 30
    enrich_all_it: bool = True
    max_enrich_it_per_scan: int = 0
    enrich_general_jobs: bool = False
    # ---- 節奏 ----
    scan_job_delay_min: float = 0.0
    scan_job_delay_max: float = 0.0
    scan_hour: int = 3
    scan_day_interval: int = 2
    # ---- 發送前潤色 ----
    email_polish_enabled: bool = True
    email_polish_instructions: str = ""
    intro_polish_enabled: bool = True
    intro_polish_instructions: str = ""
    # ---- 求職者資歷 ----
    years_experience: int = 0
    prefer_ai: bool = True
    avoid_contract: bool = False
    avoid_agency: bool = False
    # 邊個欄位係用戶明確設定（唔係 .env 預設）— UI 顯示用
    explicit: set[str] = field(default_factory=set)


def _int(profile, attr: str, env_value: int, sentinel: int = _UNSET_INT) -> int:
    """profile 數值 -> .env。sentinel 代表「未設定」。"""
    if profile is None:
        return env_value
    val = getattr(profile, attr, sentinel)
    if val is None or val == sentinel:
        return env_value
    return int(val)


def _float(profile, attr: str, env_value: float, sentinel: float = _UNSET_FLOAT) -> float:
    if profile is None:
        return env_value
    val = getattr(profile, attr, sentinel)
    if val is None or val == sentinel:
        return env_value
    return float(val)


def _bool(profile, attr: str, env_value: bool) -> bool:
    if profile is None:
        return env_value
    val = getattr(profile, attr, None)
    return env_value if val is None else bool(val)


def _text(profile, attr: str, env_value: str) -> str:
    if profile is None:
        return env_value or ""
    val = getattr(profile, attr, "") or ""
    return val.strip() or (env_value or "")


def load_tuning(db=None) -> Tuning:
    """有效設定。`db` 有就重用（唔會自己開 session）。"""
    profile = None
    if db is not None:
        from ..models import Profile

        profile = db.get(Profile, 1)
    else:
        try:
            from ..db import SessionLocal
            from ..models import Profile

            _db = SessionLocal()
            try:
                profile = _db.get(Profile, 1)
                if profile is not None:
                    _db.expunge(profile)
            finally:
                _db.close()
        except Exception:  # noqa: BLE001 — 冇 DB（測試早期）就用 .env
            profile = None

    t = Tuning(
        max_scan_jobs=_int(profile, "max_scan_jobs", settings.MAX_SCAN_JOBS, sentinel=0),
        offertoday_it_max_searches=_int(
            profile, "offertoday_it_max_searches", settings.OFFERTODAY_IT_MAX_SEARCHES,
            sentinel=0),
        offertoday_general_max_searches=_int(
            profile, "offertoday_general_max_searches", settings.OFFERTODAY_GENERAL_MAX_SEARCHES,
            sentinel=0),
        cap_bypass_enabled=_bool(profile, "cap_bypass_enabled", settings.CAP_BYPASS_ENABLED),
        cap_bypass_min_score=_int(
            profile, "cap_bypass_min_score", settings.CAP_BYPASS_MIN_SCORE, sentinel=0),
        priority_keywords_text=(getattr(profile, "priority_keywords", "") or "").strip()
        if profile is not None else "",
        priority_extra_max=_int(
            profile, "priority_extra_max", settings.PRIORITY_EXTRA_MAX, sentinel=0),
        max_enrich_per_scan=_int(
            profile, "max_enrich_per_scan", settings.MAX_ENRICH_PER_SCAN),
        enrich_all_it=_bool(profile, "enrich_all_it", settings.ENRICH_ALL_IT),
        max_enrich_it_per_scan=_int(
            profile, "max_enrich_it_per_scan", settings.MAX_ENRICH_IT_PER_SCAN),
        enrich_general_jobs=_bool(
            profile, "enrich_general_jobs", settings.ENRICH_GENERAL_JOBS),
        scan_job_delay_min=_float(
            profile, "scan_job_delay_min_seconds", settings.SCAN_JOB_DELAY_MIN_SECONDS),
        scan_job_delay_max=_float(
            profile, "scan_job_delay_max_seconds", settings.SCAN_JOB_DELAY_MAX_SECONDS),
        scan_hour=_int(profile, "scan_hour", settings.SCAN_HOUR),
        scan_day_interval=_int(profile, "scan_day_interval", settings.SCAN_DAY_INTERVAL),
        email_polish_enabled=_bool(profile, "email_polish_enabled", settings.EMAIL_POLISH_ENABLED),
        email_polish_instructions=_text(
            profile, "email_polish_instructions", settings.EMAIL_POLISH_INSTRUCTIONS),
        intro_polish_enabled=_bool(profile, "intro_polish_enabled", settings.INTRO_POLISH_ENABLED),
        intro_polish_instructions=_text(
            profile, "intro_polish_instructions", settings.INTRO_POLISH_INSTRUCTIONS),
        years_experience=int(getattr(profile, "years_experience", 0) or 0) if profile else 0,
        prefer_ai=_bool(profile, "prefer_ai", True),
        avoid_contract=_bool(profile, "avoid_contract", False),
        avoid_agency=_bool(profile, "avoid_agency", False),
    )
    if t.scan_job_delay_max < t.scan_job_delay_min:
        t.scan_job_delay_max = t.scan_job_delay_min
    return t


def priority_keywords(t: Tuning) -> list[str]:
    """有效「優先（豁免上限）字詞」：profile 設定 -> AI 職位關鍵字。

    用戶設定嘅字詞係額外加嘅；AI 字眼永遠有效（用戶要求：高分／AI 工唔受上限）。
    """
    from .classify import parse_keywords
    from .cv_loader import ai_title_keywords

    merged: list[str] = []
    seen: set[str] = set()
    for kw in parse_keywords(t.priority_keywords_text) + ai_title_keywords():
        low = kw.lower()
        if low and low not in seen:
            seen.add(low)
            merged.append(kw)
    return merged
