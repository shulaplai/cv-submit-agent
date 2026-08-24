"""ORM models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # Comma-separated keywords to classify a job as IT/programming (empty = built-in defaults)
    it_keywords: Mapped[str] = mapped_column(Text, default="")
    # ---- Job-track settings (IT vs 一般), editable in the Settings page ----
    it_track_enabled: Mapped[bool] = mapped_column(default=True)
    general_track_enabled: Mapped[bool] = mapped_column(default=True)
    # Non-IT track keywords (empty = .env GENERAL_JOB_KEYWORDS or built-ins)
    general_job_keywords: Mapped[str] = mapped_column(Text, default="")
    # OfferToday general track: comma-separated search terms (empty = general keywords)
    offertoday_general_search_terms: Mapped[str] = mapped_column(Text, default="")
    # Per-source scan caps per track (0 = .env default at profile creation)
    govhk_it_max_jobs: Mapped[int] = mapped_column(Integer, default=0)
    govhk_general_max_jobs: Mapped[int] = mapped_column(Integer, default=0)
    offertoday_it_max_per_search: Mapped[int] = mapped_column(Integer, default=0)
    offertoday_general_max_per_search: Mapped[int] = mapped_column(Integer, default=0)
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
    salary_range: Mapped[str] = mapped_column(String(200), default="")
    jd_text: Mapped[str] = mapped_column(Text, default="")
    jd_language: Mapped[str] = mapped_column(String(10), default="zh")  # en | zh
    posted_at: Mapped[str] = mapped_column(String(50), default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_reason: Mapped[str] = mapped_column(Text, default="")
    job_summary: Mapped[str] = mapped_column(Text, default="")  # AI short summary (zh) for display
    apply_method: Mapped[str] = mapped_column(String(20), default="form")  # form | external_link | email
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    # pending_review | low_match | applied | needs_manual_intervention | failed | interviewing | rejected | offer
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_stage: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    dup_key: Mapped[str] = mapped_column(String(300), default="", index=True)  # cross-platform dup (company+title normalized)
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
