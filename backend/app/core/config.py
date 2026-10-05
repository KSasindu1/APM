from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---------------------------------------------------------
    # Groq
    # ---------------------------------------------------------

    groq_api_key: str
    groq_model: str = "openai/gpt-oss-120b"

    # ---------------------------------------------------------
    # ChromaDB
    # ---------------------------------------------------------

    chroma_persist_directory: str = "data/chroma"

    chroma_collection_name: str = "project_knowledge"

    # ---------------------------------------------------------
    # PostgreSQL
    # ---------------------------------------------------------

    # Example:
    # postgresql+psycopg://username:password@localhost:5432/projectflow
    #
    # This should be placed in .env.
    postgres_database_url: str

    # Maximum number of PostgreSQL records returned during search.
    postgres_search_limit: int = 20

    # ---------------------------------------------------------
    # Application
    # ---------------------------------------------------------

    upload_directory: str = "data/documents"

    max_upload_size_mb: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()