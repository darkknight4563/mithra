"""Application configuration loaded from environment / .env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values can be overridden via environment or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Gatekeeper AI / Flare Control System"
    version: str = "0.1.0"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS — the React (Lovable) dashboard origin(s). "*" is fine for local dev.
    cors_origins: list[str] = ["*"]


settings = Settings()
