"""Pydantic models shared across the API."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthResponse(BaseModel):
    """Response body for the GET /health endpoint."""

    status: str


class PlcReading(BaseModel):
    """A single power reading from the generator's PLC."""

    timestamp: datetime = Field(default_factory=_utcnow)
    generator_kw: float
    status: Literal["OK", "FAULT"] = "OK"


class Miner(BaseModel):
    """State of a single mining unit."""

    id: Annotated[int, Field(ge=1, le=10)]
    ip: str
    priority: Annotated[int, Field(ge=1, le=10)]
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


class LogEvent(BaseModel):
    """A timestamped event for the activity log."""

    timestamp: datetime = Field(default_factory=_utcnow)
    level: Literal["INFO", "WARN", "CRITICAL"]
    message: str
