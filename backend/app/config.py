"""Application configuration loaded from .env (pydantic-settings)."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (DeepSeek primary, Qwen DashScope fallback) ---
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_API_KEY: str = ""
    LLM_FALLBACK_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_FALLBACK_MODEL: str = "qwen-plus"
    LLM_FALLBACK_API_KEY: str = ""

    # --- Applicant ---
    APPLICANT_NAME: str = ""
    APPLICANT_EMAIL: str = ""  # sender shown in mailto / Mail drafts
    CV_EN_PATH: str = ""       # absolute path to English CV PDF
    CV_ZH_PATH: str = ""       # absolute path to Chinese CV PDF

    # --- Job targeting ---
    JOB_KEYWORDS: str = "AI Engineer,agent developer,AI developer,developer,programmer,frontend developer,資訊科技工程師,AI 工程師,AI基礎架構"
    MATCH_THRESHOLD: int = 50  # 0-100; below -> low_match (hidden by default)
    # Scheduled scan: every SCAN_DAY_INTERVAL days at SCAN_HOUR (0 disables)
    SCAN_HOUR: int = 23
    SCAN_DAY_INTERVAL: int = 2
    # LLM budget per scan: only the top-N (by keyword pre-score) new jobs get
    # full LLM match + CL; the rest are left for backfill / manual refresh.
    MAX_ENRICH_PER_SCAN: int = 30
    # Only keep jobs whose posting date is within this many days (2 months).
    # Applies to EVERY platform incl. gov.hk IT category (its DD/MM/YYYY
    # posting date is checked against the scan day). Jobs with an unknown
    # posting date (e.g. OfferToday) are always kept. Set 0 to disable.
    MAX_JOB_AGE_DAYS: int = 60
    # gov.hk 大灣區青年就業計劃: posting date must be within 1 month (30 days).
    GBAY_MAX_JOB_AGE_DAYS: int = 30
    # gov.hk 資訊及科技界: only the first N jobs per scan (list is newest-first).
    GOVHK_IT_MAX_JOBS: int = 50
    # Optional global cap on total jobs kept per scan (fair-share round-robin
    # across platforms). 0 = no global cap; per-channel caps govern.
    MAX_SCAN_JOBS: int = 0
    # OfferToday: each search result (資訊科技/工程師/科技) contributes at most
    # this many drafts per scan. Set 0 for no cap.
    OFFERTODAY_MAX_PER_SEARCH: int = 40
    GOAL_APPLICATIONS_PER_WEEK: int = 15
    GOVHK_ENABLED: bool = True
    # JobsDB is hidden for now (semi-auto flow pending); set true to re-enable.
    JOBSDB_ENABLED: bool = False

    # --- Application behavior ---
    # True = agent fills the form AND clicks submit / sends the email itself.
    # The UI (settings) and per-job manual mode can override this.
    AUTO_SUBMIT: bool = True

    # --- OfferToday pre-uploaded resume picking ---
    # OfferToday sends a resume already uploaded to the account (via the
    # 「發履歷」 dialog). These are optional filename substrings to pick the
    # right one per JD language; when empty, a heuristic is used
    # (zh/chinese/中文 -> Chinese; anything else -> English).
    OFFERTODAY_CV_EN_KEYWORD: str = ""
    OFFERTODAY_CV_ZH_KEYWORD: str = ""

    # --- Browser automation ---
    # JobsDB/OfferToday automation connects to a real Chrome window via CDP.
    # IMPORTANT: Chrome 136+ ignores --remote-debugging-port on the DEFAULT
    # profile (security), so we use a DEDICATED profile dir. The user logs in
    # ONCE there; their normal Chrome is never touched.
    CHROME_CDP_URL: str = "http://127.0.0.1:9222"
    CHROME_PROFILE_DIR: str = "~/Library/Application Support/Google/Chrome-CVSubmit"

    # --- GBA scheme eligibility (jobs.gov.hk GBA vacancies) ---
    GBA_AGE_UNDER_29: bool = True
    GBA_EDU_ASSOCIATE_DEGREE: bool = True

    # --- Paths ---
    DATA_DIR: Path = PROJECT_ROOT / "data"
    DB_PATH: Path = PROJECT_ROOT / "data" / "cvsubmit.db"
    PROFILES_DIR: Path = PROJECT_ROOT / "data" / "profiles"

    @property
    def keywords(self) -> list[str]:
        return [k.strip() for k in self.JOB_KEYWORDS.split(",") if k.strip()]

    def ensure_dirs(self) -> None:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.PROFILES_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
