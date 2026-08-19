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
    SCAN_INTERVAL_HOURS: float = 6.0
    GOVHK_ENABLED: bool = True

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
