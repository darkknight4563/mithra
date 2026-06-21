"""PLC access layer.

A single interface (`PlcInterface`) abstracts power readings so the rest of the
system never cares whether it's talking to a simulator or real hardware:

  * `SimulatedPlc` — in-process fake used in SIMULATION mode. Supports scenario
    modifiers (ramp / step / noise) that the controller drives.
  * `RealPlc` — pymodbus-backed stub for LIVE mode. Same interface; not yet
    implemented.

`get_plc(settings)` returns the right one for the configured MODE.
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from typing import Callable

from app.models import PlcReading

# Nominal generator output for the simulator (~100-120 kW band).
DEFAULT_BASE_KW = 110.0
# Default per-read fluctuation as a fraction of the value (small, "steady" gas).
DEFAULT_NOISE_LEVEL = 0.01


class PlcInterface(ABC):
    """Abstract PLC. Implementations return the current generator output."""

    @abstractmethod
    def read_power(self) -> PlcReading:
        """Return the latest generator power reading."""
        raise NotImplementedError


class SimulatedPlc(PlcInterface):
    """In-process fake generator.

    Produces a reading around ``base_kw`` with a little noise. Three injectable
    modifiers let the controller/dashboard reshape the signal:

      * ramp        — adds ``rate`` kW per second of wall-clock time
      * step        — multiplies the output (e.g. an instantaneous -20% drop)
      * noise level — fractional +/- jitter applied each read
    """

    def __init__(
        self,
        base_kw: float = DEFAULT_BASE_KW,
        noise_level: float = DEFAULT_NOISE_LEVEL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_kw = base_kw
        self.noise_level = noise_level
        self.step_multiplier = 1.0
        self._ramp_rate = 0.0  # kW per second
        self._ramp_t0: float | None = None
        self._clock = clock

    def read_power(self) -> PlcReading:
        value = self.base_kw
        if self._ramp_rate and self._ramp_t0 is not None:
            value += self._ramp_rate * (self._clock() - self._ramp_t0)
        value *= self.step_multiplier
        if self.noise_level:
            value += value * random.uniform(-self.noise_level, self.noise_level)
        value = max(0.0, value)
        return PlcReading(generator_kw=round(value, 2), status="OK")

    # ---- scenario modifiers (driven by the controller) -------------------

    def start_ramp(self, rate_kw_per_sec: float = 0.5) -> None:
        """Begin (or re-anchor) a linear ramp at ``rate_kw_per_sec``."""
        # Fold any ramp accrued so far into the base so the ramp stays smooth.
        if self._ramp_rate and self._ramp_t0 is not None:
            self.base_kw += self._ramp_rate * (self._clock() - self._ramp_t0)
        self._ramp_rate = rate_kw_per_sec
        self._ramp_t0 = self._clock()

    def sudden_drop(self, pct: float = 0.20) -> None:
        """Instantly reduce output by ``pct`` (0.20 == -20%)."""
        self.step_multiplier *= 1.0 - pct

    def set_noise(self, level: float) -> None:
        """Set the fractional noise level (e.g. 0.03 for +/-3%)."""
        self.noise_level = level

    def reset(self) -> None:
        """Clear all scenario modifiers."""
        self.step_multiplier = 1.0
        self._ramp_rate = 0.0
        self._ramp_t0 = None


class RealPlc(PlcInterface):
    """Modbus TCP-backed PLC for LIVE mode. STUB — not implemented yet."""

    def __init__(self, host: str, port: int, unit_id: int, kw_register: int) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.kw_register = kw_register
        # Lazy import so SIMULATION mode never requires a working pymodbus stack.
        from pymodbus.client import ModbusTcpClient

        self._client = ModbusTcpClient(host=host, port=port)

    def read_power(self) -> PlcReading:
        # TODO(LIVE): implement the real read:
        #   1. self._client.connect()
        #   2. rr = self._client.read_holding_registers(self.kw_register, count=1,
        #          slave=self.unit_id)
        #   3. handle rr.isError() -> PlcReading(status="FAULT", generator_kw=0)
        #   4. decode/scale rr.registers[0] into kW (confirm register scaling
        #      and word order with the actual PLC documentation).
        raise NotImplementedError(
            "RealPlc.read_power() is not implemented. Running in LIVE mode "
            "requires a real Modbus TCP PLC at "
            f"{self.host}:{self.port} (unit {self.unit_id}, register "
            f"{self.kw_register}). Use MODE=SIMULATION until hardware exists."
        )


def get_plc(settings) -> PlcInterface:
    """Return the PLC implementation appropriate for the configured MODE."""
    if settings.mode == "LIVE":
        return RealPlc(
            host=settings.plc_host,
            port=settings.plc_port,
            unit_id=settings.plc_unit_id,
            kw_register=settings.plc_kw_register,
        )
    return SimulatedPlc()
