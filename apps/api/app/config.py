from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env at the monorepo root regardless of the working directory
# uvicorn is started from:
#   config.py  →  app/  →  apps/api/  →  apps/  →  repo root
_ROOT_ENV = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    app_base_url: str = "http://localhost:3000"  # set APP_BASE_URL in env; also used as CORS allow_origin

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/eventdb"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Neo4j (Phase 5)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    # Spotify (Phase 5)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Maps / Places
    google_places_api_key: str = ""

    # Ingestion sources
    eventbrite_api_key: str = ""
    meetup_api_key: str = ""

    # SerpApi (Google Events)
    serpapi_api_key: str = ""
    # Comma-separated city strings, e.g. "San Francisco CA,New York NY,Austin TX"
    serpapi_locations: str = ""
    # htichips filter — see https://serpapi.com/google-events-api
    serpapi_date_filter: str = "date:week"

    # Task queue
    redis_url: str = "redis://localhost:6379/0"

    # OpenAI
    openai_api_key: str = ""

    # Admin
    admin_api_key: str = "change-me-in-production"

    # Rate limiting (admin endpoints)
    rate_limit_ingest_per_minute: int = 5


settings = Settings()
