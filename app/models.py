"""Pydantic models shared across the API."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class CamelModel(BaseModel):
    """Base for models serialized to the frontend as camelCase JSON.

    Python attributes stay snake_case (service code is unaffected); JSON in/out
    uses camelCase aliases. `populate_by_name=True` lets us keep constructing
    instances with snake_case keyword args.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class HealthResponse(BaseModel):
    """Response body for the GET /health endpoint."""

    status: str


class PlcReading(CamelModel):
    """A single power reading from the generator's PLC."""

    timestamp: datetime = Field(default_factory=_utcnow)
    generator_kw: float
    status: Literal["OK", "FAULT"] = "OK"
    # Electrical frequency of the islanded bus. None when the source (a bare
    # kW register, or an older simulator) does not report it.
    frequency_hz: Optional[float] = None


class Miner(CamelModel):
    """State of a single mining unit."""

    id: Annotated[int, Field(ge=1, le=99)]
    ip: str
    priority: Annotated[int, Field(ge=1, le=99)]
    status: Literal["ON", "OFF", "BOOTING", "ERROR"]
    power_kw: float
    hashrate_mhs: Optional[float] = None
    last_seen: datetime = Field(default_factory=_utcnow)


class ControllerState(BaseModel):
    """Aggregate state of the control loop, surfaced to the dashboard."""

    mode: Literal["SIMULATION", "LIVE"]
    buffer_factor: float
    hysteresis: float
    loop_interval: int
    available_kw: float
    mining_load_kw: float
    active_miners: int
    latest_plc_kw: float
    # Frequency governor view: latest Hz and how the controller classified it.
    #   N/A  — source reports no frequency (kW-only control)
    #   OK   — inside the add/shed band
    #   HOLD — below nominal minus add margin: no ADD allowed
    #   LOW  — under-frequency: shed one miner per tick
    #   TRIP — below trip threshold: failsafe, all miners off
    latest_hz: Optional[float] = None
    frequency_status: Literal["N/A", "OK", "HOLD", "LOW", "TRIP"] = "N/A"
    # Which fleet adapter is driving the miners.
    fleet_backend: Literal["SIMULATED", "CGMINER"] = "SIMULATED"


class LogEvent(CamelModel):
    """A timestamped event for the activity log."""

    timestamp: datetime = Field(default_factory=_utcnow)
    level: Literal["INFO", "WARN", "CRITICAL"]
    message: str
