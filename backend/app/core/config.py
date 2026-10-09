
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):

    # ---------------------------------------------------------
    # Groq AI
    # ---------------------------------------------------------

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # ---------------------------------------------------------
    # Supabase
    # ---------------------------------------------------------

    supabase_url: str = ""
    supabase_secret_key: str = ""

    # ---------------------------------------------------------
    # Planning & Scheduling Agent
    # ---------------------------------------------------------

    planning_api_key: str | None = None

    # ---------------------------------------------------------
    # ChromaDB - Risk & Knowledge Agent
    # ---------------------------------------------------------

    chroma_persist_directory: str = "data/chroma"
    chroma_collection_name: str = "project_knowledge"

    # ---------------------------------------------------------
    # PostgreSQL
    # ---------------------------------------------------------

    postgres_database_url: str | None = None
    postgres_search_limit: int = 20

    # ---------------------------------------------------------
    # Document Uploads
    # ---------------------------------------------------------

    upload_directory: str = "data/documents"
    max_upload_size_mb: int = 10

    # ---------------------------------------------------------
    # Admin Authentication
    # ---------------------------------------------------------

    admin_username: str = ""
    admin_password: str = ""

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
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
    # Environment Settings
    # ---------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )


# ---------------------------------------------------------
# Shared Settings
# ---------------------------------------------------------

@lru_cache
def get_settings() -> Settings:
    return Settings()


# Supports existing Risk Agent imports:
# from app.core.config import settings

settings = get_settings()


# ---------------------------------------------------------
# Configuration Validation
# ---------------------------------------------------------

def validate_configuration() -> Settings:

    config = get_settings()

    missing: list[str] = []

    if not config.supabase_url:
        missing.append("SUPABASE_URL")

    if not config.supabase_secret_key:
        missing.append("SUPABASE_SECRET_KEY")

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )

    return config
