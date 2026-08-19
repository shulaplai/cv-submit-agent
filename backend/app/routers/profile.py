"""Profile (onboarding) endpoints — single row id=1."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Profile
from ..schemas import ProfileIn, ProfileOut

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)):
    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(
            id=1, name=settings.APPLICANT_NAME, email=settings.APPLICANT_EMAIL,
            cv_en_path=settings.CV_EN_PATH, cv_zh_path=settings.CV_ZH_PATH,
            gba_age_under_29=settings.GBA_AGE_UNDER_29,
            gba_edu_associate_degree=settings.GBA_EDU_ASSOCIATE_DEGREE,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.put("", response_model=ProfileOut)
def update_profile(payload: ProfileIn, db: Session = Depends(get_db)):
    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile
