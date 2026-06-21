"""API request/response schemas (the wire contract for the frontend).

Domain models live in app.models; these wrap them into the envelopes the Lovable
dashboard expects and provide the camelCase superset for controller state.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.models import LogEvent, Miner, PlcReading


class ControllerStateOut(BaseModel):
    """Controller state as camelCase JSON.

    Superset: both the names from the written spec (loopInterval / availableKw /
    miningLoadKw) and the names the live frontend reads (loopIntervalSec /
    availablePowerKw / currentLoadKw) are emitted so neither side breaks.
    """

    model_config = ConfigDict(populate_by_name=True)

    mode: str
    bufferFactor: float
    hysteresis: float
    loopInterval: int
    loopIntervalSec: int
    availableKw: float
    availablePowerKw: float
    miningLoadKw: float
    currentLoadKw: float
    activeMiners: int
    latestPlcKw: float

    @classmethod
    def from_state(cls, s) -> "ControllerStateOut":
        """Build the camelCase superset DTO from an internal ControllerState."""
        return cls(
            mode=s.mode,
            bufferFactor=s.buffer_factor,
            hysteresis=s.hysteresis,
            loopInterval=s.loop_interval,
            loopIntervalSec=s.loop_interval,
            availableKw=s.available_kw,
            availablePowerKw=s.available_kw,
            miningLoadKw=s.mining_load_kw,
            currentLoadKw=s.mining_load_kw,
            activeMiners=s.active_miners,
            latestPlcKw=s.latest_plc_kw,
        )


# ---- response envelopes ------------------------------------------------------


class PlcLatestResponse(BaseModel):
    """Envelope for the latest PLC reading."""

    reading: PlcReading


class MinersResponse(BaseModel):
    """Envelope for the full miner list."""

    miners: list[Miner]


class MinerResponse(BaseModel):
    """Envelope for a single (updated) miner."""

    miner: Miner


class ControllerStateResponse(BaseModel):
    """Envelope for the controller state."""

    state: ControllerStateOut


class LogsResponse(BaseModel):
    """Envelope for a list of log events."""

    items: list[LogEvent]


# ---- request bodies ----------------------------------------------------------


class PowerCommand(BaseModel):
    """Body for a miner power toggle."""

    state: Literal["ON", "OFF"]


class ConfigUpdate(BaseModel):
    """Live controller-param update. Accepts both loopInterval and loopIntervalSec."""

    model_config = ConfigDict(populate_by_name=True)

    bufferFactor: Optional[float] = None
    hysteresis: Optional[float] = None
    loopInterval: Optional[int] = None
    loopIntervalSec: Optional[int] = None


class ScenarioCommand(BaseModel):
    """Body for the written-spec scenario endpoint."""

    scenario: Literal["ramp_up", "sudden_drop", "noisy_gas"]
    enabled: bool = True
