"""Application configuration loaded from environment / .env via pydantic-settings."""

from typing import Literal

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

    # CORS — dev convenience only. The production dashboard is bundled and served
    # same-origin from this app (app/main.py serves app/static/index.html), so it
    # needs no cross-origin grant at all. Scope stays localhost: a standing
    # wildcard for a third-party preview host, combined with allow_credentials,
    # is dead config that reads like live config.
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:3000,http://localhost:8080,"
        "http://127.0.0.1:5173,http://127.0.0.1:3000,http://127.0.0.1:8080"
    )
    allowed_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    # Operating mode. SIMULATION uses the in-process fake PLC; LIVE talks to real hardware.
    mode: Literal["SIMULATION", "LIVE"] = "SIMULATION"

    # PLC / Modbus connection (used when MODE=LIVE).
    plc_host: str = "127.0.0.1"
    plc_port: int = 502
    plc_unit_id: int = 1
    plc_kw_register: int = 100

    # Control-loop tuning.
    buffer_factor: float = 0.90  # only schedule load up to this fraction of available power
    hysteresis: float = 0.05  # dead-band to avoid flapping miners on/off
    loop_interval: int = 10  # seconds between control-loop iterations

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a clean list of exact origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
