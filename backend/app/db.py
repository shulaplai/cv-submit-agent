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
    ("job_applications", "dup_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("job_applications", "job_summary", "TEXT NOT NULL DEFAULT ''"),
]


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
    # gov.hk now splits into two categories; legacy rows belong to the GBA scheme.
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE job_applications SET platform='govhk_gbayes' WHERE platform='govhk'"
        ))


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    migrate()
