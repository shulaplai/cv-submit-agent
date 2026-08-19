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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("platform", "job_id_on_platform", name="uq_platform_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(30))  # jobsdb | offertoday | govhk
    job_id_on_platform: Mapped[str] = mapped_column(String(120))
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
    apply_method: Mapped[str] = mapped_column(String(20), default="form")  # form | external_link | email
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    # pending_review | low_match | applied | needs_manual_intervention | failed | interviewing | rejected | offer
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_stage: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
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
