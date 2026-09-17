"""CV loading: PDF -> plain text + skills list extraction."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from ..config import settings

log = logging.getLogger(__name__)


class CVError(RuntimeError):
    pass


# ---------------------------------------------------------------- CV variants
# 用戶嘅規則：AI 職位交 AI 版 CV；冇 AI 版 -> Full-stack 版；
# 連 Full-stack 都冇 -> Developer 版；全部都冇 -> 通用版（cv_en/cv_zh）。
VARIANT_FIELD = {
    "ai": "cv_ai",
    "fullstack": "cv_fullstack",
    "developer": "cv_developer",
}
_VARIANT_FIELD = VARIANT_FIELD
VARIANT_LABEL = {
    "ai": "AI 版",
    "fullstack": "Full-stack 版",
    "developer": "Developer 版",
    "default": "通用版",
}
# OfferToday「選擇履歷」對話框：用檔名認版本。
# 拉丁字用「詞」（token）比對——所以 Lai_Developer_CV.pdf 唔會誤中 "ai"；
# 中日韓字就用子字串比對。
VARIANT_FILENAME_MARKERS = {
    "ai": {"tokens": (("ai",),), "cjk": ("人工智能", "機器學習", "機器學習")},
    "fullstack": {"tokens": (("fullstack",), ("full", "stack")), "cjk": ("全端", "全棧")},
    "developer": {"tokens": (("developer",), ("dev",)), "cjk": ("開發", "工程師")},
}


def _filename_tokens(filename: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (filename or "").lower()) if t}


def filename_matches_variant(filename: str, variant: str) -> bool:
    """True when an OfferToday resume filename looks like that CV variant."""
    spec = VARIANT_FILENAME_MARKERS.get(variant)
    if not spec:
        return False
    low = (filename or "").lower()
    if any(m in low for m in spec.get("cjk", ())):
        return True
    tokens = _filename_tokens(filename)
    return any(all(part in tokens for part in group) for group in spec.get("tokens", ()))


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


def ai_title_keywords() -> list[str]:
    """Keywords that mark a job title as AI-related.

    設定頁（profile.cv_ai_title_keywords）-> .env CV_AI_TITLE_KEYWORDS -> 內建。
    """
    profile = _profile()
    text = (getattr(profile, "cv_ai_title_keywords", "") if profile else "") or ""
    text = text.strip() or settings.CV_AI_TITLE_KEYWORDS
    return [k.strip().lower() for k in text.split(",") if k.strip()]


def title_is_ai(title: str) -> bool:
    """True when the JOB TITLE mentions AI (title-only, per the user's choice).

    Latin keywords match on word boundaries so "ai" never hits "email"/"detail";
    CJK keywords match as substrings.
    """
    t = (title or "").lower()
    if not t:
        return False
    for kw in ai_title_keywords():
        if not kw:
            continue
        if kw.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", t):
                return True
        elif kw in t:
            return True
    return False


def variant_preference(title: str) -> list[str]:
    """CV variants to try, best first, for this job title.

    AI 職位 -> AI 版、Full-stack 版、Developer 版；其他 -> Full-stack、Developer。
    """
    if title_is_ai(title):
        return ["ai", "fullstack", "developer"]
    return ["fullstack", "developer"]


def resolve_cv_for_job(title: str, language: str) -> tuple[str, str]:
    """Pick the CV (path, variant) for a job title + language.

    Follows the user's ladder, ending with the generic CV for that language.
    """
    for variant in variant_preference(title):
        path = resolve_cv_path(language, variant)
        if path:
            return path, variant
    path = resolve_cv_path(language)
    return (path, "default") if path else ("", "")


def offertoday_variant_preference(title: str) -> list[str]:
    """Ordered CV variants to look for in the OfferToday resume list."""
    return variant_preference(title)


def variant_for_filename(filename: str) -> str:
    """Best-effort variant of an OfferToday resume filename ('' = unknown)."""
    for variant in ("ai", "fullstack", "developer"):
        if filename_matches_variant(filename, variant):
            return variant
    return ""


def resolve_cv_path(language: str, variant: str = "") -> str:
    """CV path for a language (+ optional variant); profile overrides .env.

    variant: "" (generic CV) | "ai" | "fullstack" | "developer".
    Returns "" when that variant has no path configured.
    """
    profile = _profile()
    field = _VARIANT_FIELD.get(variant)
    if field:
        col = f"{field}_{language}_path"       # e.g. cv_ai_en_path
        env_key = f"CV_{variant.upper()}_{language.upper()}_PATH"   # CV_AI_EN_PATH
        val = getattr(profile, col, "") if profile else ""
        return (val or "") or getattr(settings, env_key, "") or ""
    if language == "zh":
        return (profile.cv_zh_path if profile and profile.cv_zh_path else "") or settings.CV_ZH_PATH
    return (profile.cv_en_path if profile and profile.cv_en_path else "") or settings.CV_EN_PATH


def get_cv_text(language: str, title: str = "") -> str:
    """Return CV plain text for the given language ('en' | 'zh').

    ``title`` (the job title) selects the CV VARIANT when one is configured, so
    the text used for the cover letter matches the CV that gets attached.
    """
    path, _variant = resolve_cv_for_job(title, language)
    if not path:
        # fall back to whichever CV exists
        path = resolve_cv_path("zh" if language == "en" else "en")
    if not path:
        raise CVError("no CV path configured（設定頁用文件揀選器上傳，或 .env 填 CV_EN_PATH / CV_ZH_PATH）")
    return pdf_to_text(path)
