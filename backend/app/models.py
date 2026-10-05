"""ORM models."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Profile(Base):
    """Single-row onboarding/profile settings (id always 1)."""
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    cv_en_path: Mapped[str] = mapped_column(String(500), default="")
    cv_zh_path: Mapped[str] = mapped_column(String(500), default="")
    # ---- CV 版本（每種語言各有 AI／Full-stack／Developer 版）----
    # 申請時按職位標題揀版本：AI 職位 -> AI 版；冇 AI 版 -> Full-stack 版；
    # 連 Full-stack 版都冇 -> Developer 版；全部都冇 -> cv_*_path（通用版）。
    # 留空 = 冇嗰個版本。
    cv_ai_en_path: Mapped[str] = mapped_column(String(500), default="")
    cv_ai_zh_path: Mapped[str] = mapped_column(String(500), default="")
    cv_fullstack_en_path: Mapped[str] = mapped_column(String(500), default="")
    cv_fullstack_zh_path: Mapped[str] = mapped_column(String(500), default="")
    cv_developer_en_path: Mapped[str] = mapped_column(String(500), default="")
    cv_developer_zh_path: Mapped[str] = mapped_column(String(500), default="")
    skills_json: Mapped[str] = mapped_column(Text, default="[]")  # list[str]
    gba_age_under_29: Mapped[bool] = mapped_column(default=True)
    gba_edu_associate_degree: Mapped[bool] = mapped_column(default=True)
    # Optional LLM keys configured in the UI (override .env when set)
    llm_api_key: Mapped[str] = mapped_column(String(300), default="")
    llm_fallback_api_key: Mapped[str] = mapped_column(String(300), default="")
    # True = auto-submit applications (fill + click submit / send email)
    auto_submit: Mapped[bool] = mapped_column(default=True)
    # Short self-introduction embedded in application messages/emails
    intro_en: Mapped[str] = mapped_column(Text, default="")
    intro_zh: Mapped[str] = mapped_column(Text, default="")
    # OfferToday: filename keywords to pick the right pre-uploaded resume
    # (override config/env; empty -> heuristic)
    offertoday_cv_en_keyword: Mapped[str] = mapped_column(String(200), default="")
    offertoday_cv_zh_keyword: Mapped[str] = mapped_column(String(200), default="")
    # OfferToday: ~100-char self-intro sent AFTER the CV, per JD language x topic
    after_cv_intro_it_zh: Mapped[str] = mapped_column(Text, default="")
    after_cv_intro_it_en: Mapped[str] = mapped_column(Text, default="")
    after_cv_intro_general_zh: Mapped[str] = mapped_column(Text, default="")
    after_cv_intro_general_en: Mapped[str] = mapped_column(Text, default="")
    # OfferToday: 「AI Agent 版」自我介紹（職位標題有 AI 字眼時用；空 = 退回 IT 版）
    after_cv_intro_ai_zh: Mapped[str] = mapped_column(Text, default="")
    after_cv_intro_ai_en: Mapped[str] = mapped_column(Text, default="")
    # 判斷「AI 職位」嘅標題字眼（逗號分隔；空 = .env CV_AI_TITLE_KEYWORDS）
    cv_ai_title_keywords: Mapped[str] = mapped_column(Text, default="")
    # Comma-separated keywords to classify a job as IT/programming (empty = built-in defaults)
    it_keywords: Mapped[str] = mapped_column(Text, default="")
    # ---- Job-track settings (IT vs 一般), editable in the Settings page ----
    it_track_enabled: Mapped[bool] = mapped_column(default=True)
    general_track_enabled: Mapped[bool] = mapped_column(default=True)
    # 保險／地產／sales agent 類「封鎖字眼」（絕對否決，改都改唔到入 IT 軌；
    # 留空 = 內建清單。用戶要求：呢類職位唔要）
    it_blocked_keywords: Mapped[str] = mapped_column(Text, default="")
    # 「唔當 IT」嘅職位字眼（擋住 engineer／工程師／技術員 等過闊字；空 = 內建）
    non_it_keywords: Mapped[str] = mapped_column(Text, default="")
    # 一般工「想去嘅地點」白名單（逗號分隔，空 = 唔篩地點）
    general_wanted_locations: Mapped[str] = mapped_column(Text, default="")
    # Non-IT track keywords (empty = .env GENERAL_JOB_KEYWORDS or built-ins)
    general_job_keywords: Mapped[str] = mapped_column(Text, default="")
    # OfferToday「揀履歷」對話框：4 類 CV 嘅檔名關鍵字
    # （AI 版／IT 版唔分中英；一般版分中英）
    offertoday_cv_ai_keyword: Mapped[str] = mapped_column(Text, default="")
    offertoday_cv_it_keyword: Mapped[str] = mapped_column(Text, default="")
    offertoday_cv_general_zh_keyword: Mapped[str] = mapped_column(Text, default="")
    offertoday_cv_general_en_keyword: Mapped[str] = mapped_column(Text, default="")
    # OfferToday general track: comma-separated search terms (empty = general keywords)
    offertoday_general_search_terms: Mapped[str] = mapped_column(Text, default="")
    # OfferToday IT track: extra keyword searches on top of the 3 category pages
    # (empty = .env OFFERTODAY_IT_SEARCH_TERMS)
    offertoday_it_search_terms: Mapped[str] = mapped_column(Text, default="")
    # Per-source scan caps per track (0 = .env default at profile creation)
    govhk_it_max_jobs: Mapped[int] = mapped_column(Integer, default=0)
    govhk_general_max_jobs: Mapped[int] = mapped_column(Integer, default=0)
    offertoday_it_max_per_search: Mapped[int] = mapped_column(Integer, default=0)
    offertoday_general_max_per_search: Mapped[int] = mapped_column(Integer, default=0)
    # 每個渠道／每個 track 要開幾個搜尋頁（0 = .env 預設）
    offertoday_it_max_searches: Mapped[int] = mapped_column(Integer, default=0)
    offertoday_general_max_searches: Mapped[int] = mapped_column(Integer, default=0)
    # 每個 track 每次掃描總上限（0 = .env MAX_SCAN_JOBS；0 = 唔設限）
    max_scan_jobs: Mapped[int] = mapped_column(Integer, default=0)
    # ---- 高分豁免上限（用戶要求：評級好高嘅工無視數量限制照收）----
    cap_bypass_enabled: Mapped[bool] = mapped_column(default=True)
    # 0 = .env CAP_BYPASS_MIN_SCORE
    cap_bypass_min_score: Mapped[int] = mapped_column(Integer, default=0)
    # 優先字詞（逗號分隔；空 = AI 職位關鍵字）。命中就一定收，唔計軟上限。
    priority_keywords: Mapped[str] = mapped_column(Text, default="")
    # 每個渠道每次掃描最多豁免收幾多份（0 = .env PRIORITY_EXTRA_MAX）
    priority_extra_max: Mapped[int] = mapped_column(Integer, default=0)
    # ---- LLM 預算 ----
    max_enrich_per_scan: Mapped[int] = mapped_column(Integer, default=-1)  # -1 = .env 預設
    enrich_all_it: Mapped[bool] = mapped_column(default=True)
    max_enrich_it_per_scan: Mapped[int] = mapped_column(Integer, default=-1)  # -1 = .env；0 = 不限
    # 一般工要唔要 LLM 評分（預設唔要：省 API，只有 IT 工評分）
    enrich_general_jobs: Mapped[bool] = mapped_column(default=False)
    # ---- 自動寄 email（SMTP，唔需要 macOS Mail 權限）----
    send_method: Mapped[str] = mapped_column(String(10), default="")   # "" = .env
    smtp_host: Mapped[str] = mapped_column(String(200), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=0)         # 0 = .env
    smtp_user: Mapped[str] = mapped_column(String(200), default="")
    smtp_password: Mapped[str] = mapped_column(String(300), default="")
    smtp_from_name: Mapped[str] = mapped_column(String(200), default="")
    smtp_from_email: Mapped[str] = mapped_column(String(200), default="")
    smtp_use_ssl: Mapped[bool] = mapped_column(default=False)
    smtp_bcc_self: Mapped[bool] = mapped_column(default=True)
    # ---- 批量 AI 檢查（LLM 分批判斷係唔係真 IT 工）----
    ai_check_enabled: Mapped[bool] = mapped_column(default=False)
    ai_check_batch_size: Mapped[int] = mapped_column(Integer, default=0)   # 0 = .env
    ai_check_limit: Mapped[int] = mapped_column(Integer, default=0)        # 0 = .env
    ai_check_after_scan: Mapped[bool] = mapped_column(default=False)
    # ---- AI 搜尋組（搵 AI／agent 工）----
    ai_search_terms: Mapped[str] = mapped_column(Text, default="")        # 空 = .env
    ai_search_max_searches: Mapped[int] = mapped_column(Integer, default=0)  # 0 = .env
    ai_search_max_age_days: Mapped[int] = mapped_column(Integer, default=-1)  # -1 = .env
    ai_stale_action: Mapped[str] = mapped_column(String(10), default="")  # "" = .env
    # ---- 掃描節奏（0 = .env 預設）----
    scan_job_delay_min_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    scan_job_delay_max_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    scan_hour: Mapped[int] = mapped_column(Integer, default=-1)       # -1 = .env 預設
    scan_day_interval: Mapped[int] = mapped_column(Integer, default=-1)  # -1 = .env 預設
    # 一次性「舊預設上限 -> 新預設」提升做過未（見 db._uplift_legacy_caps）
    caps_uplifted: Mapped[bool] = mapped_column(default=False)
    # ---- 求職者資歷（影響 match 評分同排序）----
    years_experience: Mapped[int] = mapped_column(Integer, default=0)
    prefer_ai: Mapped[bool] = mapped_column(default=True)
    avoid_contract: Mapped[bool] = mapped_column(default=False)
    avoid_agency: Mapped[bool] = mapped_column(default=False)
    # ---- 發送前 AI 潤色 ----
    email_polish_enabled: Mapped[bool] = mapped_column(default=True)
    email_polish_instructions: Mapped[str] = mapped_column(Text, default="")
    intro_polish_enabled: Mapped[bool] = mapped_column(default=True)
    intro_polish_instructions: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("platform", "job_id_on_platform", name="uq_platform_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(30))  # jobsdb | offertoday | govhk_gbayes | govhk_it | govhk_general
    job_id_on_platform: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(10), default="it")  # it | general (職位台分頁)
    url: Mapped[str] = mapped_column(String(600), default="")
    external_url: Mapped[str] = mapped_column(String(600), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    # 一般工：地點寫唔明（列表／JD 都冇提任何地區）-> 保留但標示（唔靜靜篩走）
    location_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    salary_range: Mapped[str] = mapped_column(String(200), default="")
    jd_text: Mapped[str] = mapped_column(Text, default="")
    jd_language: Mapped[str] = mapped_column(String(10), default="zh")  # en | zh
    posted_at: Mapped[str] = mapped_column(String(50), default="")
    # Normalized posting date parsed from posted_at (DD/MM/YYYY, ISO, relative).
    # Used by the 刊登日期 range filter + sort; backfilled every boot.
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_reason: Mapped[str] = mapped_column(Text, default="")
    job_summary: Mapped[str] = mapped_column(Text, default="")  # AI short summary (zh) for display
    apply_method: Mapped[str] = mapped_column(String(20), default="form")  # form | external_link | email
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    # pending_review | low_match | applied | needs_manual_intervention | failed | interviewing | rejected | offer | no_response
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_stage: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    dup_key: Mapped[str] = mapped_column(String(300), default="", index=True)  # cross-platform dup (company+title normalized)
    # ---- 求職者 fit 標籤（純關鍵字／LLM 判斷，職位台篩選用）----
    ai_match: Mapped[bool] = mapped_column(Boolean, default=False)      # 標題或 JD 提到 AI
    is_contract: Mapped[bool] = mapped_column(Boolean, default=False)  # 合約／臨時／兼職／實習
    is_agency: Mapped[bool] = mapped_column(Boolean, default=False)    # 外派／獵頭／人力資源公司
    match_level: Mapped[str] = mapped_column(String(10), default="")   # "" | under | fit | over
    # AI 相關度："" = 唔相關；"title" = 標題有 AI／agent；"jd" = 只喺 JD 提到
    ai_strength: Mapped[str] = mapped_column(String(10), default="")
    # 邊個搜尋字詞／分類頁帶入呢份工（用戶要求：追蹤 AI 搜尋組 + 7 日限制）
    source_query: Mapped[str] = mapped_column(String(120), default="")
    # ---- 批量 AI 檢查（LLM 分批判斷係唔係真 IT／AI 工）----
    ai_verdict: Mapped[str] = mapped_column(String(10), default="")  # "" | it_ai | it | non_it
    ai_verdict_reason: Mapped[str] = mapped_column(Text, default="")
    ai_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ---- 發送前 AI 潤色（發送內容 + 潤色來源 key，避免重複洗 LLM）----
    email_body_polished: Mapped[str] = mapped_column(Text, default="")
    email_polished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_polish_key: Mapped[str] = mapped_column(String(80), default="")
    offertoday_intro_polished: Mapped[str] = mapped_column(Text, default="")
    offertoday_intro_polished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    cover_letters: Mapped[list[CoverLetter]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="CoverLetter.version"
    )


class CoverLetter(Base):
    __tablename__ = "cover_letters"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_application_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"))
    language: Mapped[str] = mapped_column(String(10), default="en")  # en | zh
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    application: Mapped[JobApplication] = relationship(back_populates="cover_letters")
