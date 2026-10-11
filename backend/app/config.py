"""Application configuration.

All settings are driven by environment variables (see ``.env.example``).
Sensible, fully local defaults are provided so the application starts with no
keys and no external services.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", str(BACKEND_ROOT / ".env"), str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application -------------------------------------------------------
    APP_NAME: str = "ReguLens AI"
    APP_VERSION: str = "1.0.0"
    APP_TAGLINE: str = "From changing regulations to evidence-backed compliance decisions."
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # ---- Database ----------------------------------------------------------
    # Defaults to the docker-compose PostgreSQL+pgvector service. Set
    # DATABASE_URL=sqlite:///./regulens.db for a zero-dependency local run.
    DATABASE_URL: str = "postgresql+psycopg://regulens:regulens@localhost:55432/regulens"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_CONNECT_TIMEOUT: int = 10

    # ---- Storage -----------------------------------------------------------
    UPLOAD_DIR: Path = REPO_ROOT / "data" / "uploads"
    SAMPLE_DIR: Path = REPO_ROOT / "data" / "samples"
    MAX_UPLOAD_MB: int = 25
    ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "text/html",
    )
    ALLOWED_EXTENSIONS: tuple[str, ...] = (
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".html",
        ".htm",
    )

    # ---- Security ----------------------------------------------------------
    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480
    CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173")
    AUTH_ENABLED: bool = False  # local prototype; enable for shared deployments
    DEFAULT_ADMIN_USERNAME: str = "compliance.officer"
    DEFAULT_ADMIN_PASSWORD: str = "change-me-locally"
    REQUEST_BODY_LIMIT_BYTES: int = 30 * 1024 * 1024

    # ---- Hosted AI providers (Gemini + Cerebras; deterministic default) -----
    # No provider is required. When neither is enabled/configured the app runs
    # fully deterministically. API keys are server-side only and never sent to
    # the browser or written to logs (see app.llm.redaction).
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_ENABLED: bool = True
    GEMINI_TIMEOUT_SECONDS: int = 60
    GEMINI_MAX_OUTPUT_TOKENS: int = 2048
    GEMINI_MAX_RETRIES: int = 2

    CEREBRAS_API_KEY: str = ""
    CEREBRAS_MODEL: str = "gpt-oss-120b"
    CEREBRAS_ENABLED: bool = True
    CEREBRAS_TIMEOUT_SECONDS: int = 45
    CEREBRAS_MAX_OUTPUT_TOKENS: int = 1024
    CEREBRAS_MAX_RETRIES: int = 2
    # Hard application-level budget guard. This is NOT an account-wide cap:
    # the provider dashboard is authoritative for the real balance.
    CEREBRAS_BUDGET_USD: float = 4.00
    CEREBRAS_MAX_CALL_COST_USD: float = 0.05
    CEREBRAS_DAILY_BUDGET_USD: float = 1.00
    CEREBRAS_LEDGER_PATH: str = ""

    # Provider routing. Must stay centralised in app.llm.router; application
    # modules must call get_router().generate_for_task(task, ...) instead of
    # importing a provider directly.
    LLM_PROVIDER: Literal["gemini", "cerebras", "deterministic"] = "gemini"
    LLM_FALLBACK_PROVIDER: Literal["gemini", "cerebras", "deterministic", "none"] = "cerebras"
    LLM_ROUTING_OVERRIDES: str = ""  # optional JSON object: {"obligation_extraction": "gemini", ...}
    LLM_MAX_INPUT_CHARS: int = 24000
    LLM_CACHE_ENABLED: bool = True
    LLM_CACHE_TTL_SECONDS: int = 3600
    LLM_MAX_CONCURRENT: int = 2
    DETERMINISTIC_MODE: bool = False
    LLM_TIMEOUT_SECONDS: int = 60
    LLM_MAX_RETRIES: int = 2
    EMBEDDING_DIMENSION: int = 384

    # ---- OCR (optional) ----------------------------------------------------
    ENABLE_OCR: bool = True
    OCR_LANGUAGES: str = "eng"
    OCR_PAGE_LIMIT: int = 10
    OCR_TIMEOUT_SECONDS: int = 120

    # ---- Risk engine defaults (configurable at runtime) --------------------
    RISK_WEIGHTS: dict[str, float] = Field(
        default_factory=lambda: {
            "regulatory_severity": 0.35,
            "failure_likelihood": 0.25,
            "exposure": 0.20,
            "urgency": 0.20,
        }
    )
    EVIDENCE_FRESHNESS_DAYS: int = 180
    HIGH_RISK_THRESHOLD: float = 70.0
    MEDIUM_RISK_THRESHOLD: float = 40.0

    # ---- Retrieval ---------------------------------------------------------
    RETRIEVAL_TOP_K: int = 8
    RETRIEVAL_MAX_CANDIDATES: int = 20000
    # Calibrated on the bundled demo corpus (RBI IO directions vs sample
    # control matrix): true matches score 0.25-0.55 with lexical retrieval.
    # Raise toward 0.35+ for larger corpora; always configurable per call.
    MAPPING_MIN_CONFIDENCE: float = 0.25
    SIMILARITY_MIN_SCORE: float = 0.35

    # ---- Ag
    AGENT_TIMEOUT_SECONDS: int = 300
    AGENT_MAX_RETRIES: int = 2
    ORCHESTRATION_BACKEND: Literal["native", "neurosan", "auto"] = "native"

    # ---- Logging -----------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    @field_validator("UPLOAD_DIR", "SAMPLE_DIR", mode="after")
    @classmethod
    def _abs(cls, v: Path) -> Path:
        return Path(v).resolve()

    @field_validator(
        "CEREBRAS_BUDGET_USD",
        "CEREBRAS_MAX_CALL_COST_USD",
        "CEREBRAS_DAILY_BUDGET_USD",
        mode="after",
    )
    @classmethod
    def _non_negative_budget(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Cerebras budget limits must be >= 0")
        return v

    @field_validator("GEMINI_MODEL", "CEREBRAS_MODEL", mode="after")
    @classmethod
    def _non_empty_model(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Model id must be a non-empty string; it is configurable, never hardcoded-only.")
        return v.strip()

    @property
    def gemini_configured(self) -> bool:
        return bool(self.GEMINI_ENABLED and self.GEMINI_API_KEY.strip() and self.GEMINI_MODEL.strip())

    @property
    def cerebras_configured(self) -> bool:
        return bool(self.CEREBRAS_ENABLED and self.CEREBRAS_API_KEY.strip() and self.CEREBRAS_MODEL.strip())

    @property
    def any_provider_configured(self) -> bool:
        return self.gemini_configured or self.cerebras_configured

    @property
    def effective_deterministic(self) -> bool:
        """True when hosted AI must not be called.

        Explicit DETERMINISTIC_MODE forces it; otherwise it is effective
        whenever neither provider is enabled+configured. This supports the
        four required modes: gemini-only, cerebras-only, both, neither.
        """
        if self.DETERMINISTIC_MODE:
            return True
        return not self.any_provider_configured

    @property
    def cerebras_ledger_path(self) -> Path:
        if self.CEREBRAS_LEDGER_PATH:
            return Path(self.CEREBRAS_LEDGER_PATH).resolve()
        return (REPO_ROOT / "data" / ".cerebras_usage.json").resolve()

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    def upload_dir_for(self, org_slug: str) -> Path:
        p = self.UPLOAD_DIR / org_slug
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
