from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    supabase_url: str = ""
    supabase_secret_key: str = field(default="", repr=False)

    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"

    planning_api_key: str | None = field(default=None, repr=False)

    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )

    chroma_persist_directory: str = "data/chroma"
    chroma_collection_name: str = "project_knowledge"

    postgres_database_url: str | None = field(default=None, repr=False)
    postgres_search_limit: int = 20

    upload_directory: str = "data/documents"
    max_upload_size_mb: int = 10


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        raise RuntimeError(
            f"{name} must be an integer."
        ) from None


def get_settings() -> Settings:
    cors_origins_raw = os.getenv(
        "CORS_ORIGINS",
        (
            "http://localhost:5173,"
            "http://127.0.0.1:5173,"
            "http://localhost:3000,"
            "http://127.0.0.1:3000"
        ),
    )

    cors_origins = tuple(
        origin.strip()
        for origin in cors_origins_raw.split(",")
        if origin.strip()
    )

    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", "").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip() or None,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip(),
        planning_api_key=os.getenv("PLANNING_API_KEY", "").strip() or None,
        cors_origins=cors_origins,
        chroma_persist_directory=os.getenv(
            "CHROMA_PERSIST_DIRECTORY", "data/chroma"
        ),
        chroma_collection_name=os.getenv(
            "CHROMA_COLLECTION_NAME", "project_knowledge"
        ),
        postgres_database_url=os.getenv("POSTGRES_DATABASE_URL", "").strip() or None,
        postgres_search_limit=_get_int("POSTGRES_SEARCH_LIMIT", 20),
        upload_directory=os.getenv("UPLOAD_DIRECTORY", "data/documents"),
        max_upload_size_mb=_get_int("MAX_UPLOAD_SIZE_MB", 10),
    )


# Risk services import a singleton; Planning callers refresh via get_settings().
settings = get_settings()


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
