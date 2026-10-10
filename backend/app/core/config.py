
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # ---------------------------------------------------------
    # Supabase
    # ---------------------------------------------------------

    supabase_url: str = ""
    supabase_secret_key: str = Field(default="", repr=False)

    # ---------------------------------------------------------
    # Groq AI
    # ---------------------------------------------------------

    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"

    # ---------------------------------------------------------
    # Planning & Scheduling Agent
    # ---------------------------------------------------------

    planning_api_key: str | None = Field(default=None, repr=False)

    # ---------------------------------------------------------
    # Risk & Knowledge Agent
    # ---------------------------------------------------------

    risk_agent_url: str = "http://localhost:8000"
    risk_agent_timeout: int = 60

    # ---------------------------------------------------------
    # Risk & Knowledge Retrieval Agent - ChromaDB
    # ---------------------------------------------------------

    chroma_persist_directory: str = "data/chroma"
    chroma_collection_name: str = "project_knowledge"

    # ---------------------------------------------------------
    # PostgreSQL
    # ---------------------------------------------------------

    postgres_database_url: str | None = Field(default=None, repr=False)
    postgres_search_limit: int = 20

    # ---------------------------------------------------------
    # Document Uploads
    # ---------------------------------------------------------

    upload_directory: str = "data/documents"
    max_upload_size_mb: int = 10

    # ---------------------------------------------------------
    # Planning Agent callback
    # ---------------------------------------------------------

    planning_risk_results_url: str = (
        "http://127.0.0.1:8000/api/planning/risk-results"
    )

    # ---------------------------------------------------------
    # Admin Authentication
    # ---------------------------------------------------------

    admin_username: str = "admin"
    # Passwords and signing material are exact values, not whitespace-normalized.
    admin_password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        default="#&thvtksep26", repr=False
    )
    admin_password_hash: Annotated[
        str | None, StringConstraints(strip_whitespace=False)
    ] = Field(default=None, repr=False)


    jwt_secret_key: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        default="", repr=False
    )
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "apm"
    jwt_audience: str = "apm-admin"
    account_jwt_audience: str = "apm-accounts"
    access_token_expire_minutes: int = 60

    # ---------------------------------------------------------
    # CORS Configuration
    # ---------------------------------------------------------

    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return tuple(
                origin.strip()
                for origin in value.split(",")
                if origin.strip()
            )

        return value

    # ---------------------------------------------------------
    # Environment Configuration
    # ---------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


# ---------------------------------------------------------
# Settings Instance
# ---------------------------------------------------------

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Compatibility with existing Risk Agent imports
settings = get_settings()


# ---------------------------------------------------------
# Configuration Validation
# ---------------------------------------------------------

def validate_configuration() -> Settings:

    current_settings = get_settings()

    missing: list[str] = []

    if not current_settings.supabase_url:
        missing.append("SUPABASE_URL")

    if not current_settings.supabase_secret_key:
        missing.append("SUPABASE_SECRET_KEY")

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )

    return current_settings
