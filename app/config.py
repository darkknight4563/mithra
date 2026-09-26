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

    app_name: str = "Mithra — Flare Control System"
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
    # Genset controller register map (see app/services/genset.py). "generic"
    # reads only PLC_KW_REGISTER (kW x10) and reports no frequency; named maps
    # (dse_gencomm, comap_intelig) add frequency and alarm words. Any field can
    # be overridden with PLC_HZ_REGISTER / PLC_ALARM_REGISTER etc.
    genset_map: str = "generic"
    plc_hz_register: int | None = None
    plc_hz_scale: float | None = None
    plc_alarm_register: int | None = None
    plc_alarm_mask: int | None = None
    plc_kw_scale: float | None = None
    plc_kw_words: int | None = None  # 1 (16-bit) or 2 (32-bit)

    # Frequency governor (islanded bus protection). A loaded genset is protected
    # by frequency, not kW: under-frequency means the engine is overloaded.
    nominal_hz: float = 50.0  # 60.0 in the Americas
    hz_add_margin: float = 0.3  # no ADD while Hz < nominal - this
    hz_shed_margin: float = 1.0  # shed one miner per tick while Hz < nominal - this
    hz_trip_margin: float = 2.5  # failsafe (all OFF) while Hz < nominal - this

    # Miner fleet adapter. SIMULATED = in-process fake fleet. CGMINER = real
    # ASICs over the CGMiner/BOSminer/LuxOS TCP API (port 4028 by default),
    # inventory read from FLEET_CONFIG (see fleet.example.json).
    fleet_backend: Literal["SIMULATED", "CGMINER"] = "SIMULATED"
    fleet_config: str = "fleet.json"
    miner_api_port: int = 4028
    miner_api_timeout: float = 3.0
    fleet_poll_interval: float = 5.0  # seconds between real-fleet API polls

    # Control-loop tuning.
    buffer_factor: float = 0.90  # only schedule load up to this fraction of available power
    hysteresis: float = 0.05  # dead-band to avoid flapping miners on/off
    loop_interval: int = 10  # seconds between control-loop iterations

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a clean list of exact origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
