"""Pydantic schemas for API requests/responses."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CoverLetterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    language: str
    content: str
    version: int
    created_at: datetime


class JobApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    job_id_on_platform: str
    category: str = "it"
    url: str
    external_url: str
    title: str
    company: str
    location: str
    salary_range: str
    jd_text: str
    jd_language: str
    posted_at: str
    scraped_at: datetime
    match_score: int
    match_reason: str
    job_summary: str = ""
    # ---- fit 標籤（職位台篩選 chip 用）----
    ai_match: bool = False
    is_contract: bool = False
    is_agency: bool = False
    match_level: str = ""          # "" | under | fit | over
    # AI 相關度："" | "title"（標題有 AI／agent）| "jd"（只喺 JD 提到）
    ai_strength: str = ""
    # 邊個搜尋字詞／渠道帶入呢份工（睇 AI 搜尋組成效）
    source_query: str = ""
    # 批量 AI 檢查結果
    ai_verdict: str = ""
    ai_verdict_reason: str = ""
    # 申請時會交邊份 CV（AI 版／Full-stack 版／Developer 版／通用版）
    cv_variant: str = ""
    apply_method: str
    contact_email: str
    contact_person: str
    status: str
    applied_at: Optional[datetime] = None
    interview_stage: str
    notes: str
    # ---- 發送前 AI 潤色成品（可喺詳情頁睇返）----
    offertoday_intro_polished: str = ""
    email_polished_at: Optional[datetime] = None
    dup_key: str = ""
    dup_count: int = 0
    created_at: datetime
    updated_at: datetime
    cover_letters: list[CoverLetterOut] = []


class JobListOut(BaseModel):
    items: list[JobApplicationOut]
    total: int
    hidden_low_match: int
    # filter-chip badge counts: {"statuses": {status: n}, "platforms": {platform: n}}
    facets: dict = {}


class EmailPreview(BaseModel):
    to: str
    contact_person: str
    subject: str
    body: str
    # 潤色狀態：polished=True 表示 body 係 AI 潤色版；body_original 係未潤色版
    polished: bool = False
    body_original: str = ""


class UpdateApplicationIn(BaseModel):
    status: Optional[str] = None
    interview_stage: Optional[str] = None
    notes: Optional[str] = None


class RegenerateCLLIn(BaseModel):
    """Request body for CL regeneration (optional overrides)."""
    instructions: str = ""


class CoverLetterEditIn(BaseModel):
    content: str


class ProfileIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    cv_en_path: Optional[str] = None
    cv_zh_path: Optional[str] = None
    cv_ai_en_path: Optional[str] = None
    cv_ai_zh_path: Optional[str] = None
    cv_fullstack_en_path: Optional[str] = None
    cv_fullstack_zh_path: Optional[str] = None
    cv_developer_en_path: Optional[str] = None
    cv_developer_zh_path: Optional[str] = None
    skills_json: Optional[str] = None
    gba_age_under_29: Optional[bool] = None
    gba_edu_associate_degree: Optional[bool] = None
    llm_api_key: Optional[str] = None
    llm_fallback_api_key: Optional[str] = None
    auto_submit: Optional[bool] = None
    intro_en: Optional[str] = None
    intro_zh: Optional[str] = None
    offertoday_cv_en_keyword: Optional[str] = None
    offertoday_cv_zh_keyword: Optional[str] = None
    after_cv_intro_it_zh: Optional[str] = None
    after_cv_intro_it_en: Optional[str] = None
    after_cv_intro_general_zh: Optional[str] = None
    after_cv_intro_general_en: Optional[str] = None
    after_cv_intro_ai_zh: Optional[str] = None
    after_cv_intro_ai_en: Optional[str] = None
    cv_ai_title_keywords: Optional[str] = None
    it_keywords: Optional[str] = None
    it_track_enabled: Optional[bool] = None
    general_track_enabled: Optional[bool] = None
    general_job_keywords: Optional[str] = None
    non_it_keywords: Optional[str] = None
    it_blocked_keywords: Optional[str] = None
    general_wanted_locations: Optional[str] = None
    offertoday_cv_ai_keyword: Optional[str] = None
    offertoday_cv_it_keyword: Optional[str] = None
    offertoday_cv_general_zh_keyword: Optional[str] = None
    offertoday_cv_general_en_keyword: Optional[str] = None
    offertoday_general_search_terms: Optional[str] = None
    offertoday_it_search_terms: Optional[str] = None
    govhk_it_max_jobs: Optional[int] = None
    govhk_general_max_jobs: Optional[int] = None
    offertoday_it_max_per_search: Optional[int] = None
    offertoday_general_max_per_search: Optional[int] = None
    # ---- 掃描量（每個 track／搜尋頁開幾多）----
    offertoday_it_max_searches: Optional[int] = None
    offertoday_general_max_searches: Optional[int] = None
    max_scan_jobs: Optional[int] = None
    # ---- 高分豁免上限 ----
    cap_bypass_enabled: Optional[bool] = None
    cap_bypass_min_score: Optional[int] = None
    priority_keywords: Optional[str] = None
    priority_extra_max: Optional[int] = None
    # ---- LLM 預算 ----
    max_enrich_per_scan: Optional[int] = None
    enrich_all_it: Optional[bool] = None
    max_enrich_it_per_scan: Optional[int] = None
    enrich_general_jobs: Optional[bool] = None
    # ---- AI 搜尋組 ----
    ai_search_terms: Optional[str] = None
    ai_search_max_searches: Optional[int] = None
    ai_search_max_age_days: Optional[int] = None
    ai_stale_action: Optional[str] = None
    # ---- 批量 AI 檢查 ----
    # ---- SMTP 自動寄信 ----
    send_method: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_use_ssl: Optional[bool] = None
    smtp_bcc_self: Optional[bool] = None
    ai_check_enabled: Optional[bool] = None
    ai_check_batch_size: Optional[int] = None
    ai_check_limit: Optional[int] = None
    ai_check_after_scan: Optional[bool] = None
    # ---- 掃描節奏 ----
    scan_job_delay_min_seconds: Optional[float] = None
    scan_job_delay_max_seconds: Optional[float] = None
    scan_hour: Optional[int] = None
    scan_day_interval: Optional[int] = None
    # ---- 求職者資歷 ----
    years_experience: Optional[int] = None
    prefer_ai: Optional[bool] = None
    avoid_contract: Optional[bool] = None
    avoid_agency: Optional[bool] = None
    # ---- 發送前 AI 潤色 ----
    email_polish_enabled: Optional[bool] = None
    email_polish_instructions: Optional[str] = None
    intro_polish_enabled: Optional[bool] = None
    intro_polish_instructions: Optional[str] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    email: str
    cv_en_path: str
    cv_zh_path: str
    cv_ai_en_path: str
    cv_ai_zh_path: str
    cv_fullstack_en_path: str
    cv_fullstack_zh_path: str
    cv_developer_en_path: str
    cv_developer_zh_path: str
    skills_json: str
    gba_age_under_29: bool
    gba_edu_associate_degree: bool
    llm_api_key: str
    llm_fallback_api_key: str
    auto_submit: bool
    intro_en: str
    intro_zh: str
    offertoday_cv_en_keyword: str
    offertoday_cv_zh_keyword: str
    after_cv_intro_it_zh: str
    after_cv_intro_it_en: str
    after_cv_intro_general_zh: str
    after_cv_intro_general_en: str
    after_cv_intro_ai_zh: str
    after_cv_intro_ai_en: str
    cv_ai_title_keywords: str
    it_keywords: str
    it_track_enabled: bool
    general_track_enabled: bool
    general_job_keywords: str
    non_it_keywords: str
    it_blocked_keywords: str = ""
    general_wanted_locations: str
    offertoday_cv_ai_keyword: str
    offertoday_cv_it_keyword: str
    offertoday_cv_general_zh_keyword: str
    offertoday_cv_general_en_keyword: str
    offertoday_general_search_terms: str
    offertoday_it_search_terms: str
    govhk_it_max_jobs: int
    govhk_general_max_jobs: int
    offertoday_it_max_per_search: int
    offertoday_general_max_per_search: int
    # ---- 掃描量 ----
    offertoday_it_max_searches: int = 0
    offertoday_general_max_searches: int = 0
    max_scan_jobs: int = 0
    # ---- 高分豁免上限 ----
    cap_bypass_enabled: bool = True
    cap_bypass_min_score: int = 0
    priority_keywords: str = ""
    priority_extra_max: int = 0
    # ---- LLM 預算 ----
    max_enrich_per_scan: int = -1
    enrich_all_it: bool = True
    max_enrich_it_per_scan: int = -1
    enrich_general_jobs: bool = False
    # ---- AI 搜尋組 ----
    ai_search_terms: str = ""
    ai_search_max_searches: int = 0
    ai_search_max_age_days: int = -1
    ai_stale_action: str = ""
    # ---- 批量 AI 檢查 ----
    send_method: str = ""
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = ""
    smtp_from_email: str = ""
    smtp_use_ssl: bool = False
    smtp_bcc_self: bool = True
    ai_check_enabled: bool = False
    ai_check_batch_size: int = 0
    ai_check_limit: int = 0
    ai_check_after_scan: bool = False
    # ---- 掃描節奏 ----
    scan_job_delay_min_seconds: float = 0.0
    scan_job_delay_max_seconds: float = 0.0
    scan_hour: int = -1
    scan_day_interval: int = -1
    # ---- 求職者資歷 ----
    years_experience: int = 0
    prefer_ai: bool = True
    avoid_contract: bool = False
    avoid_agency: bool = False
    # ---- 發送前 AI 潤色 ----
    email_polish_enabled: bool = True
    email_polish_instructions: str = ""
    intro_polish_enabled: bool = True
    intro_polish_instructions: str = ""
    updated_at: datetime


class ScanResult(BaseModel):
    scanned: int
    new_jobs: int
    skipped_duplicates: int
    errors: list[str] = []


class FunnelRow(BaseModel):
    """漏斗一行：某個分組（track／AI／分數段）嘅投遞結果。"""
    key: str
    label: str
    applied: int = 0
    responded: int = 0      # 有回覆（面試中／落選／offer）
    interviewing: int = 0
    rejected: int = 0
    no_response: int = 0
    offer: int = 0
    pending: int = 0        # 已投但仲未有記錄
    response_rate: float = 0.0


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_platform: dict[str, int]
    applied_last_7d: int
    applied_last_30d: int
    weekly_applied: list[dict]  # [{week: str, count: int}]
    weekly_goal: int
    applied_this_week: int
    # 結果漏斗：按 track／AI／分數段分組（用戶用數據決定邊類工值得投）
    funnel: list[FunnelRow] = []
    funnel_by_category: list[FunnelRow] = []
    funnel_by_ai: list[FunnelRow] = []
    funnel_by_score: list[FunnelRow] = []
