"""SQLAlchemy setup: engine, session factory, Base, dependency, migrations."""
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

log = logging.getLogger(__name__)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Idempotent column additions for existing SQLite DBs (create_all won't add columns).
_COLUMN_MIGRATIONS = [
    ("profiles", "llm_api_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("profiles", "llm_fallback_api_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("profiles", "auto_submit", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "intro_en", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "intro_zh", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "cv_ai_en_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "cv_ai_zh_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "cv_fullstack_en_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "cv_fullstack_zh_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "cv_developer_en_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "cv_developer_zh_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_cv_en_keyword", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_cv_zh_keyword", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_it_zh", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_it_en", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_general_zh", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_general_en", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "it_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "it_track_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "general_track_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "general_job_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "non_it_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "general_wanted_locations", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_ai_zh", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "after_cv_intro_ai_en", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "cv_ai_title_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "location_uncertain", "BOOLEAN NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_cv_ai_keyword", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_cv_it_keyword", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_cv_general_zh_keyword", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_cv_general_en_keyword", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_general_search_terms", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "offertoday_it_search_terms", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "govhk_it_max_jobs", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "govhk_general_max_jobs", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_it_max_per_search", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_general_max_per_search", "INTEGER NOT NULL DEFAULT 0"),
    ("job_applications", "dup_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("job_applications", "job_summary", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "category", "VARCHAR(10) NOT NULL DEFAULT 'it'"),
    ("job_applications", "posted_date", "DATE"),
    # ---- 掃描量／高分豁免（Settings 頁）----
    ("profiles", "offertoday_it_max_searches", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_general_max_searches", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "max_scan_jobs", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "cap_bypass_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "cap_bypass_min_score", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "priority_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "priority_extra_max", "INTEGER NOT NULL DEFAULT 0"),
    # ---- LLM 預算 ----
    ("profiles", "max_enrich_per_scan", "INTEGER NOT NULL DEFAULT -1"),
    ("profiles", "enrich_all_it", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "max_enrich_it_per_scan", "INTEGER NOT NULL DEFAULT -1"),
    ("profiles", "enrich_general_jobs", "BOOLEAN NOT NULL DEFAULT 0"),
    # ---- 掃描節奏 ----
    ("profiles", "scan_job_delay_min_seconds", "FLOAT NOT NULL DEFAULT 0"),
    ("profiles", "scan_job_delay_max_seconds", "FLOAT NOT NULL DEFAULT 0"),
    ("profiles", "scan_hour", "INTEGER NOT NULL DEFAULT -1"),
    ("profiles", "scan_day_interval", "INTEGER NOT NULL DEFAULT -1"),
    # ---- 求職者資歷 ----
    ("profiles", "years_experience", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "prefer_ai", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "avoid_contract", "BOOLEAN NOT NULL DEFAULT 0"),
    ("profiles", "avoid_agency", "BOOLEAN NOT NULL DEFAULT 0"),
    # ---- 發送前 AI 潤色 ----
    ("profiles", "email_polish_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "email_polish_instructions", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "intro_polish_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "intro_polish_instructions", "TEXT NOT NULL DEFAULT ''"),
    # ---- 職位 fit 標籤（AI／合約／外派）----
    ("job_applications", "ai_match", "BOOLEAN NOT NULL DEFAULT 0"),
    ("job_applications", "is_contract", "BOOLEAN NOT NULL DEFAULT 0"),
    ("job_applications", "is_agency", "BOOLEAN NOT NULL DEFAULT 0"),
    ("job_applications", "match_level", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("job_applications", "ai_strength", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("job_applications", "source_query", "VARCHAR(120) NOT NULL DEFAULT ''"),
    ("job_applications", "ai_verdict", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("job_applications", "ai_verdict_reason", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "ai_checked_at", "DATETIME"),
    ("profiles", "send_method", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_host", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_port", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "smtp_user", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_password", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_from_name", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_from_email", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("profiles", "smtp_use_ssl", "BOOLEAN NOT NULL DEFAULT 0"),
    ("profiles", "smtp_bcc_self", "BOOLEAN NOT NULL DEFAULT 1"),
    ("profiles", "ai_check_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("profiles", "ai_check_batch_size", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "ai_check_limit", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "ai_check_after_scan", "BOOLEAN NOT NULL DEFAULT 0"),
    ("profiles", "it_blocked_keywords", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "ai_search_terms", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "ai_search_max_searches", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "ai_search_max_age_days", "INTEGER NOT NULL DEFAULT -1"),
    ("profiles", "ai_stale_action", "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("job_applications", "outcome_at", "DATETIME"),
    # ---- 發送前 AI 潤色嘅成品（+ cache key）----
    ("job_applications", "email_body_polished", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "email_polished_at", "DATETIME"),
    ("job_applications", "email_polish_key", "VARCHAR(80) NOT NULL DEFAULT ''"),
    ("job_applications", "offertoday_intro_polished", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "offertoday_intro_polished_at", "DATETIME"),
    # 一次性上限提升嘅標記（見 _uplift_legacy_caps）
    ("profiles", "caps_uplifted", "BOOLEAN NOT NULL DEFAULT 0"),
]

# Seed per-source scan caps from .env into the (single) profile row when the
# column is still 0 — 0 means "use the .env default". Runs every boot; values
# the user edits in the Settings page are never overwritten.
_CAP_SEEDS = {
    "govhk_it_max_jobs": "GOVHK_IT_MAX_JOBS",
    "govhk_general_max_jobs": "GOVHK_GENERAL_MAX_JOBS",
    "offertoday_it_max_per_search": "OFFERTODAY_MAX_PER_SEARCH",
    "offertoday_general_max_per_search": "OFFERTODAY_GENERAL_MAX_PER_SEARCH",
}

# 一次性「上限提升」：舊 .env 預設嘅渠道上限（50／20／80／15）釘咗喺 profile 度，
# 令新嘅（更闊）預設永遠唔會生效。用戶要求「其他工嘅數量可以再多啲」，所以
# 開機時如果見到**仲係舊預設值**就升去新預設，並用 profiles.caps_uplifted 記住
# 做過（只做一次；之後你手動改返細都唔會被改）。
_LEGACY_CAP_UPLIFT = [
    ("govhk_it_max_jobs", "GOVHK_IT_MAX_JOBS", 50),
    ("govhk_general_max_jobs", "GOVHK_GENERAL_MAX_JOBS", 20),
    ("offertoday_it_max_per_search", "OFFERTODAY_MAX_PER_SEARCH", 80),
    ("offertoday_general_max_per_search", "OFFERTODAY_GENERAL_MAX_PER_SEARCH", 15),
]


# 用戶要求（2026-10）：AI 相關只認「AI」同「agent」；IT 搜尋字詞只要
# agent／developer／FDE／programmer／AI。舊 profile 值係上一代預設，一次性改返。
_OLD_AI_KEYWORDS_DEFAULT = (
    "ai, artificial intelligence, 人工智能, 機器學習, machine learning, "
    "深度學習, deep learning, llm, 大模型, nlp, 自然語言處理, 電腦視覺, "
    "computer vision, 生成式, genai, ai agent, 算法"
)
_NEW_AI_KEYWORDS = "ai,agent"
_OLD_IT_SEARCH_TERMS = "AI, developer,工程師,  Programmer, engineer , agent"
_NEW_IT_SEARCH_TERMS = "agent,developer,FDE,programmer,AI"


def _migrate_ai_keyword_prefs() -> None:
    """一次性：profile 仲係舊預設就換成用戶要嘅 AI／agent 設定。"""
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT cv_ai_title_keywords, offertoday_it_search_terms "
                "FROM profiles WHERE id=1")).fetchone()
            if row is None:
                return
            ai_kw = (row[0] or "").strip()
            terms = (row[1] or "").strip()
            sets: list[str] = []
            params: dict = {}
            if ai_kw == _OLD_AI_KEYWORDS_DEFAULT.strip():
                sets.append("cv_ai_title_keywords=:ai")
                params["ai"] = _NEW_AI_KEYWORDS
            # 舊值有冇多餘空白都當同一套
            if terms.replace(" ", "") == _OLD_IT_SEARCH_TERMS.replace(" ", ""):
                sets.append("offertoday_it_search_terms=:terms")
                params["terms"] = _NEW_IT_SEARCH_TERMS
            if sets:
                conn.execute(text(f"UPDATE profiles SET {', '.join(sets)} WHERE id=1"), params)
        if sets:
            log.info("AI 偏好遷移：cv_ai_title_keywords -> %r；搜尋字詞 -> %r",
                     _NEW_AI_KEYWORDS if "cv_ai_title_keywords=:ai" in " ".join(sets) else "(不變)",
                     _NEW_IT_SEARCH_TERMS if "offertoday_it_search_terms=:terms" in " ".join(sets) else "(不變)")
    except Exception:  # noqa: BLE001
        log.warning("AI 偏好遷移失敗", exc_info=True)


BLOCKED_REASON = "保險／地產／sales agent 類職位（IT 軌封鎖清單，設定頁可改）"


def _purge_blocked_jobs() -> None:
    """一次性：已經入咗 IT 軌嘅保險／地產／agent 類職位 -> 標低匹配（唔刪）。

    用戶要求：呢類「都唔要」。標成 low_match 就唔會出主頁（職位台預設隱藏低匹配），
    但你仍然可以喺「低匹配」chip 睇返／還原 —— 唔會靜靜刪咗你嘅資料。
    """
    from .services.classify import blocked_reason, resolve_blocked_keywords

    try:
        from .models import JobApplication, Profile
    except Exception:  # noqa: BLE001
        return
    db = SessionLocal()
    try:
        profile = db.get(Profile, 1)
        user_kws = (getattr(profile, "it_blocked_keywords", "") or "").strip()
        kws = resolve_blocked_keywords(user_kws)
        custom = bool(user_kws)
        changed = restored = 0
        rows = (db.query(JobApplication)
                .filter(JobApplication.category == "it",
                        JobApplication.status.notin_(("applied", "interviewing",
                                                      "rejected", "offer",
                                                      "no_response")))
                .all())
        for row in rows:
            # 自訂清單 = 硬封鎖；內建 = 兩層判斷（見 classify.blocked_reason）
            hit = blocked_reason(row.title or "", kws) if custom else blocked_reason(row.title or "")
            if hit and row.status != "low_match":
                log.info("blocked job #%s「%s」命中「%s」-> 標低匹配", row.id, row.title, hit)
                row.status = "low_match"
                row.match_reason = BLOCKED_REASON
                changed += 1
            elif not hit and row.match_reason == BLOCKED_REASON:
                # 之前一次過封鎖規則太闊（例如「AI Engineer (保險公司)」）-> 還原
                log.info("blocked job #%s「%s」唔再符合封鎖條件 -> 還原", row.id, row.title)
                row.status = "pending_review"
                row.match_reason = ""
                restored += 1
        if changed or restored:
            db.commit()
            log.info("blocked purge: 標低匹配 %s 份；還原 %s 份", changed, restored)
    except Exception:  # noqa: BLE001
        log.warning("blocked purge failed", exc_info=True)
    finally:
        db.close()


def _uplift_legacy_caps() -> None:
    """把仍然等於舊 .env 預設嘅渠道上限升去新預設（只做一次，冪等）。"""
    try:
        if "caps_uplifted" not in _table_columns("profiles"):
            return
    except Exception:  # noqa: BLE001
        return
    try:
        with engine.begin() as conn:
            row = conn.execute(text("SELECT caps_uplifted FROM profiles WHERE id=1")).fetchone()
            if row is None or row[0]:
                return
            updates: list[str] = []
            params: dict = {}
            for column, env_name, legacy in _LEGACY_CAP_UPLIFT:
                new_default = int(getattr(settings, env_name, legacy) or 0)
                if new_default <= legacy:
                    continue
                updates.append(f"{column}=:v_{column}")
                params[f"v_{column}"] = new_default
                params[f"legacy_{column}"] = legacy
            if updates:
                where = " OR ".join(f"{c.split('=')[0]}=:legacy_{c.split('=')[0]}"
                                    for c in updates)
                conn.execute(text(
                    f"UPDATE profiles SET {', '.join(updates)} WHERE id=1 AND ({where})"),
                    params)
            conn.execute(text("UPDATE profiles SET caps_uplifted=1 WHERE id=1"))
        if updates:
            log.info("cap uplift: 舊預設上限已升到新預設（%s）",
                     ", ".join(c.split("=")[0] for c in updates))
    except Exception:  # noqa: BLE001 — 提升失敗唔應該阻礙開機
        log.warning("cap uplift failed", exc_info=True)


def _table_columns(table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def migrate() -> None:
    """Add any missing columns to existing tables (safe to run every boot)."""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        try:
            cols = _table_columns(table)
        except Exception:  # noqa: BLE001 — table may not exist yet
            continue
        if column not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            log.info("migrated: %s.%s added", table, column)
    # 舊欄位：一度加過「排除工地點」黑名單，已改成「想去嘅地點」白名單。
    try:
        if "general_exclude_locations" in _table_columns("profiles"):
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE profiles DROP COLUMN general_exclude_locations"))
            log.info("migrated: profiles.general_exclude_locations dropped")
    except Exception:  # noqa: BLE001 — 舊 SQLite 唔支援 DROP COLUMN 就由佢
        log.warning("could not drop profiles.general_exclude_locations", exc_info=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_dup_key "
                          "ON job_applications (dup_key)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_category "
                          "ON job_applications (category)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_posted_date "
                          "ON job_applications (posted_date)"))
    _backfill_jd_language()
    # gov.hk now splits into two categories; legacy rows belong to the GBA scheme.
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE job_applications SET platform='govhk_gbayes' WHERE platform='govhk'"
        ))
    # seed per-source scan caps into the profile row (0 = use .env default)
    for col, env_name in _CAP_SEEDS.items():
        env_val = getattr(settings, env_name, 0)
        with engine.begin() as conn:
            conn.execute(text(
                f"UPDATE profiles SET {col}=:val WHERE {col}=0"
            ), {"val": int(env_val)})
    _backfill_categories()
    _backfill_posted_dates()
    _backfill_job_flags()
    _uplift_legacy_caps()
    _migrate_ai_keyword_prefs()
    _purge_blocked_jobs()


def _backfill_job_flags() -> None:
    """填 AI／合約／外派 flag（純關鍵字、決定性，每次開機跑都安全）。

    新工入庫時已經有 flag；呢個 pass 覆蓋舊資料（現有 970 行），令職位台嘅
    新篩選 chip 一裝即用得，唔會出空列表。``match_level`` 需要 LLM，唔喺度填
    （留返俾掃描／「補齊」）。
    """
    from .services.jobflags import compute_flags

    try:
        from .models import JobApplication
    except Exception:  # noqa: BLE001
        return
    db = SessionLocal()
    try:
        changed = 0
        for row in db.query(JobApplication).all():
            flags = compute_flags(row)
            if (row.ai_match != flags["ai_match"]
                    or row.ai_strength != flags.get("ai_strength", "")
                    or row.is_contract != flags["is_contract"]
                    or row.is_agency != flags["is_agency"]):
                row.ai_match = flags["ai_match"]
                row.ai_strength = flags.get("ai_strength", "")
                row.is_contract = flags["is_contract"]
                row.is_agency = flags["is_agency"]
                changed += 1
        if changed:
            db.commit()
            log.info("job flag backfill: updated %s rows", changed)
    finally:
        db.close()


def _backfill_posted_dates() -> None:
    """Fill the normalized posted_date column from posted_at strings.

    Idempotent (only touches NULL rows), so running it every boot is safe.
    Relative dates ("24d ago") resolve against the day they are parsed — close
    enough for the 刊登日期 filter/sort.
    """
    from .services.jobdate import parse_posted_date

    try:
        from .models import JobApplication
    except Exception:  # noqa: BLE001
        return
    db = SessionLocal()
    try:
        changed = 0
        for row in db.query(JobApplication).filter(JobApplication.posted_date.is_(None)).all():
            d = parse_posted_date(row.posted_at or "")
            if d is not None:
                row.posted_date = d
                changed += 1
        if changed:
            db.commit()
            log.info("posted_date backfill: parsed %s rows", changed)
    finally:
        db.close()


def _backfill_categories() -> None:
    """One-time (idempotent) pass: tag rows by title classification.

    New rows carry their track category at insert time; this re-tags legacy
    rows (which defaulted to 'it') so the IT / 一般 board split is accurate.
    Deterministic, so running it every boot is harmless.
    """
    from .services.classify import classify, resolve_it_keywords, resolve_non_it_keywords

    try:
        from .models import JobApplication
    except Exception:  # noqa: BLE001
        return
    it_kws = resolve_it_keywords()
    non_it_kws = resolve_non_it_keywords()
    db = SessionLocal()
    try:
        changed = 0
        for row in db.query(JobApplication).all():
            cat = classify(row.title or "", it_kws, non_it_kws)
            if cat != row.category:
                row.category = cat
                changed += 1
        if changed:
            db.commit()
            log.info("category backfill: re-tagged %s legacy rows", changed)
    finally:
        db.close()


def _backfill_jd_language() -> None:
    """一次性（可重複執行）校正 jd_language。

    以前入庫硬編 "en"，令中文 JD（尤其 OfferToday）一律標錯語言，申請時就會
    交錯語言嘅 CV／自我介紹。呢個 pass 按 JD／標題重新判斷，唔會改任何 status。
    """
    from .services.language import detect_language

    try:
        from .models import JobApplication
    except Exception:  # noqa: BLE001
        return
    db = SessionLocal()
    try:
        changed = 0
        for row in db.query(JobApplication).all():
            correct = detect_language((row.jd_text or "").strip() or (row.title or ""))
            if correct and row.jd_language != correct:
                row.jd_language = correct
                changed += 1
        if changed:
            db.commit()
            log.info("jd_language backfill: corrected %s rows", changed)
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    migrate()
