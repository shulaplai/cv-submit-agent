"""CV loading: PDF -> plain text + skills list extraction."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pypdf import PdfReader

from ..config import settings

log = logging.getLogger(__name__)


class CVError(RuntimeError):
    pass


def pdf_to_text(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise CVError(f"CV file not found: {p}")
    try:
        reader = PdfReader(str(p))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:  # noqa: BLE001
        raise CVError(f"failed to read PDF {p}: {e}") from e
    text = "\n".join(pages)
    if not text.strip():
        raise CVError(f"PDF produced no text (scanned image?): {p}")
    return text


def load_skills() -> list[str]:
    """Skills list from DB profile (set during onboarding) or config default."""
    from ..db import SessionLocal
    from ..models import Profile

    db = SessionLocal()
    try:
        profile = db.get(Profile, 1)
        if profile and profile.skills_json:
            try:
                return json.loads(profile.skills_json)
            except json.JSONDecodeError:
                log.warning("profile.skills_json not valid JSON; ignoring")
    finally:
        db.close()
    return []


def _profile() -> "Profile | None":
    from ..db import SessionLocal
    from ..models import Profile

    try:
        db = SessionLocal()
        try:
            return db.get(Profile, 1)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def resolve_cv_path(language: str) -> str:
    """CV path for a language: profile (UI upload/settings) overrides .env."""
    profile = _profile()
    if language == "zh":
        return (profile.cv_zh_path if profile and profile.cv_zh_path else "") or settings.CV_ZH_PATH
    return (profile.cv_en_path if profile and profile.cv_en_path else "") or settings.CV_EN_PATH


def get_cv_text(language: str) -> str:
    """Return CV plain text for the given language ('en' | 'zh')."""
    path = resolve_cv_path(language)
    if not path:
        # fall back to whichever CV exists
        path = resolve_cv_path("zh" if language == "en" else "en")
    if not path:
        raise CVError("no CV path configured（設定頁用文件揀選器上傳，或 .env 填 CV_EN_PATH / CV_ZH_PATH）")
    return pdf_to_text(path)
