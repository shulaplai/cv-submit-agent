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


def get_cv_text(language: str) -> str:
    """Return CV plain text for the given language ('en' | 'zh')."""
    path = settings.CV_EN_PATH if language == "en" else settings.CV_ZH_PATH
    if not path:
        # fall back to whichever CV exists
        path = settings.CV_EN_PATH or settings.CV_ZH_PATH
    if not path:
        raise CVError("no CV path configured in .env (CV_EN_PATH / CV_ZH_PATH)")
    return pdf_to_text(path)
