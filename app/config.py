"""Centralized application configuration.

All runtime configuration is loaded from environment variables (via a ``.env``
file in development, or real environment variables in production). Secrets are
NEVER hardcoded — they are read here and only here.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: <root>/app/config.py -> parents[1] == <root>
ROOT_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = ROOT_DIR / "storage"
PDF_DIR = STORAGE_DIR / "pdfs"
NEWSLETTER_DIR = STORAGE_DIR / "newsletters"
CACHE_DIR = STORAGE_DIR / "cache"
KNOWLEDGE_DIR = STORAGE_DIR / "clinical_knowledge"
PROMPTS_DIR = ROOT_DIR / "app" / "prompts"


# ---------------------------------------------------------------------------
# Target journals monitored weekly by the Journal Scout agent.
# Ordered loosely by the user's clinical priorities (andrology first).
# ---------------------------------------------------------------------------
TARGET_JOURNALS: list[str] = [
    "Journal of Sexual Medicine",
    "Sexual Medicine",
    "Sexual Medicine Reviews",
    "Andrology",
    "Fertility and Sterility",
    "Human Reproduction",
    "Translational Andrology and Urology",
    "European Urology",
    "European Urology Open Science",
    "The Journal of Urology",
    "BJU International",
    "Urology",
    "World Journal of Urology",
    "Nature Reviews Urology",
]

# PubMed search terms scoped to the user's priority areas. Combined with an OR.
PRIORITY_QUERY_TERMS: list[str] = [
    "andrology",
    "male infertility",
    "male reproduction",
    "spermatogenesis",
    "azoospermia",
    "varicocele",
    "hypogonadism",
    "testosterone replacement therapy",
    "erectile dysfunction",
    "Peyronie disease",
    "premature ejaculation",
    "benign prostatic hyperplasia",
    "prostate cancer",
]

# Clinical priority weighting applied during triage/appraisal scoring.
PRIORITY_WEIGHTS: dict[str, float] = {
    "andrology": 1.0,
    "sexual medicine": 1.0,
    "male infertility": 1.0,
    "male reproduction": 1.0,
    "hypogonadism": 0.85,
    "testosterone": 0.85,
    "erectile dysfunction": 0.85,
    "peyronie": 0.85,
    "premature ejaculation": 0.85,
    "benign prostatic hyperplasia": 0.6,
    "prostate cancer": 0.6,
    "uro-oncology": 0.6,
}


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: str = "production"
    log_level: str = "INFO"
    timezone: str = "America/Sao_Paulo"

    # --- LLM providers ---
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    triage_model: str = "claude-haiku-5-5"
    analysis_model: str = "claude-sonnet-5-5"
    appraisal_model: str = "claude-sonnet-5-5"
    newsletter_model: str = "claude-sonnet-5-5"
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072

    # --- PostgreSQL ---
    database_url: str = Field(
        default="postgresql+psycopg2://urology:urology@localhost:5432/urology",
        validation_alias="DATABASE_URL",
    )

    # --- Qdrant ---
    qdrant_url: str = Field(default="http://localhost:6333", validation_alias="QDRANT_URL")
    qdrant_api_key: str = Field(default="", validation_alias="QDRANT_API_KEY")
    qdrant_collection: str = "urology_knowledge"

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    # --- External scientific APIs ---
    ncbi_api_key: str = Field(default="", validation_alias="NCBI_API_KEY")
    ncbi_email: str = "matheusmenezesurologia@gmail.com"
    ncbi_tool: str = "urology-intelligence"
    crossref_mailto: str = "matheusmenezesurologia@gmail.com"
    semantic_scholar_api_key: str = Field(default="", validation_alias="SEMANTIC_SCHOLAR_API_KEY")
    openalex_mailto: str = "matheusmenezesurologia@gmail.com"
    unpaywall_email: str = "matheusmenezesurologia@gmail.com"

    # --- Delivery channel: email | whatsapp | both ---
    delivery_channel: str = Field(default="email", validation_alias="DELIVERY_CHANNEL")

    # --- Email (SMTP, e.g. Gmail with an app password) ---
    smtp_host: str = Field(default="smtp.gmail.com", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str = Field(default="", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="SMTP_PASSWORD")
    email_from: str = Field(default="", validation_alias="EMAIL_FROM")
    email_to: str = Field(default="", validation_alias="EMAIL_TO")

    # --- WhatsApp Cloud API (optional secondary channel) ---
    whatsapp_api_version: str = "v21.0"
    whatsapp_phone_number_id: str = Field(default="", validation_alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_access_token: str = Field(default="", validation_alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_recipient: str = Field(default="", validation_alias="WHATSAPP_RECIPIENT")
    whatsapp_template_name: str = ""

    # --- Scheduler (Friday 18:00 America/Sao_Paulo) ---
    pipeline_cron_day_of_week: str = "fri"
    pipeline_cron_hour: int = 18
    pipeline_cron_minute: int = 0

    # --- Pipeline tuning ---
    lookback_days: int = 7
    top_n_articles: int = 10
    triage_relevance_threshold: int = 55
    max_concurrent_llm: int = 4
    http_timeout_seconds: int = 45
    http_max_retries: int = 4

    # --- Derived helpers ---
    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_access_token and self.whatsapp_phone_number_id and self.whatsapp_recipient)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def wants_email(self) -> bool:
        return self.delivery_channel.lower() in {"email", "both"}

    @property
    def wants_whatsapp(self) -> bool:
        return self.delivery_channel.lower() in {"whatsapp", "both"}

    def ensure_storage_dirs(self) -> None:
        """Create the storage directory tree if it does not yet exist."""
        for path in (PDF_DIR, NEWSLETTER_DIR, CACHE_DIR, KNOWLEDGE_DIR):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton ``Settings`` instance."""
    settings = Settings()
    settings.ensure_storage_dirs()
    return settings


# Convenience module-level singleton.
settings = get_settings()
