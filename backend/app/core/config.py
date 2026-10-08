from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_secret_key: str

    groq_api_key: str | None
    groq_model: str

    planning_api_key: str | None

    cors_origins: tuple[str, ...]


def get_settings() -> Settings:

    supabase_url = os.getenv(
        "SUPABASE_URL",
        "",
    ).strip()

    supabase_secret_key = os.getenv(
        "SUPABASE_SECRET_KEY",
        "",
    ).strip()

    groq_api_key = (
        os.getenv(
            "GROQ_API_KEY",
            "",
        ).strip()
        or None
    )

    groq_model = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-120b",
    ).strip()

    planning_api_key = (
        os.getenv(
            "PLANNING_API_KEY",
            "",
        ).strip()
        or None
    )

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
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        planning_api_key=planning_api_key,
        cors_origins=cors_origins,
    )


def validate_configuration() -> Settings:

    settings = get_settings()

    missing: list[str] = []

    if not settings.supabase_url:
        missing.append("SUPABASE_URL")

    if not settings.supabase_secret_key:
        missing.append("SUPABASE_SECRET_KEY")

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )

    return settings