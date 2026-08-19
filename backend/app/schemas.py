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
    apply_method: str
    contact_email: str
    contact_person: str
    status: str
    applied_at: Optional[datetime] = None
    interview_stage: str
    notes: str
    created_at: datetime
    updated_at: datetime
    cover_letters: list[CoverLetterOut] = []


class JobListOut(BaseModel):
    items: list[JobApplicationOut]
    total: int
    hidden_low_match: int


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
    skills_json: Optional[str] = None
    gba_age_under_29: Optional[bool] = None
    gba_edu_associate_degree: Optional[bool] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    email: str
    cv_en_path: str
    cv_zh_path: str
    skills_json: str
    gba_age_under_29: bool
    gba_edu_associate_degree: bool
    updated_at: datetime


class ScanResult(BaseModel):
    scanned: int
    new_jobs: int
    skipped_duplicates: int
    errors: list[str] = []


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_platform: dict[str, int]
    applied_last_7d: int
    applied_last_30d: int
    weekly_applied: list[dict]  # [{week: str, count: int}]
