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
    ("profiles", "offertoday_general_search_terms", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "govhk_it_max_jobs", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "govhk_general_max_jobs", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_it_max_per_search", "INTEGER NOT NULL DEFAULT 0"),
    ("profiles", "offertoday_general_max_per_search", "INTEGER NOT NULL DEFAULT 0"),
    ("job_applications", "dup_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("job_applications", "job_summary", "TEXT NOT NULL DEFAULT ''"),
    ("job_applications", "category", "VARCHAR(10) NOT NULL DEFAULT 'it'"),
    ("job_applications", "posted_date", "DATE"),
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
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_dup_key "
                          "ON job_applications (dup_key)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_category "
                          "ON job_applications (category)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_applications_posted_date "
                          "ON job_applications (posted_date)"))
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
    from .services.classify import classify, resolve_it_keywords

    try:
        from .models import JobApplication
    except Exception:  # noqa: BLE001
        return
    it_kws = resolve_it_keywords()
    db = SessionLocal()
    try:
        changed = 0
        for row in db.query(JobApplication).all():
            cat = classify(row.title or "", it_kws)
            if cat != row.category:
                row.category = cat
                changed += 1
        if changed:
            db.commit()
            log.info("category backfill: re-tagged %s legacy rows", changed)
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    migrate()
