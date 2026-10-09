
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

    # PostgreSQL is currently used for integration with the
    # Planning & Scheduling Agent.
    #
    # It is optional during independent development of the
    # Risk & Knowledge Agent because document-based retrieval
    # does not require PostgreSQL.
    #
    # Example:
    # postgresql+psycopg://username:password@localhost:5432/projectflow

    postgres_database_url: str | None = None

    # Maximum number of PostgreSQL records returned during search.
    postgres_search_limit: int = 20

    # ---------------------------------------------------------
    # Application
    # ---------------------------------------------------------

    upload_directory: str = "data/documents"

    max_upload_size_mb: int = 10

    # ---------------------------------------------------------
    # Admin Authentication
    # ---------------------------------------------------------

    # Username used by the administrator to log in.
    admin_username: str

    # Password used by the administrator to log in.
    admin_password: str = '#&thvtksep26'

    # Secret used to sign and verify JWT access tokens.
    #
    # This MUST be stored in the .env file and must never
    # be hard-coded or committed to Git.
    jwt_secret_key: str = "zFRDmue7IxtnchRvIRPQt20GbhkzetBF5A5IFlybndLDNmNbVCnvrQsW_NrlErFAPU66HBaF3GPcBPmnWsQxEw"

    # JWT signing algorithm.
    jwt_algorithm: str = "HS256"

    # Lifetime of an issued access token in minutes.
    access_token_expire_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
