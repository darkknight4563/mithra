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

    # CORS — the React (Lovable) dashboard origin(s). "*" is fine for local dev.
    cors_origins: list[str] = ["*"]

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


settings = Settings()
