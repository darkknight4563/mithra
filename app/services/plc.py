"""PLC access layer.

A single interface (`PlcInterface`) abstracts power readings so the rest of the
system never cares whether it's talking to a simulator or real hardware:

  * `SimulatedPlc` — in-process fake used in SIMULATION mode. Supports scenario
    modifiers (ramp / step / noise) that the controller drives.
  * `RealPlc` — pymodbus-backed reader for LIVE mode. Reads a holding register,
    scales it to kW, and degrades to a FAULT reading (last-good value) with a
    reconnect attempt on I/O errors.

`get_plc(settings)` returns the right one for the configured MODE.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

from pymodbus.exceptions import (
    ConnectionException,
    ModbusException,
    ModbusIOException,
)

from app.models import PlcReading

# Nominal generator output for the simulator (~100-120 kW band).
DEFAULT_BASE_KW = 110.0
# Default per-read fluctuation as a fraction of the value (small, "steady" gas).
DEFAULT_NOISE_LEVEL = 0.01
# Holding-register raw value is kW * 10 (one decimal of precision).
DEFAULT_KW_SCALE = 10.0


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
        """Initialise the simulator with a base output, noise and a clock."""
        self.base_kw = base_kw
        self.noise_level = noise_level
        self.step_multiplier = 1.0
        self._ramp_rate = 0.0  # kW per second
        self._ramp_t0: Optional[float] = None
        self._clock = clock

    def read_power(self) -> PlcReading:
        """Compute and return the current simulated reading."""
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
    """Modbus TCP-backed PLC for LIVE mode.

    Reads a single holding register and scales it to kW. On a Modbus I/O error
    it logs, attempts a reconnect, retries, and finally degrades to a FAULT
    reading carrying the last-good value (or 0.0 if none) so the controller's
    fail-safe can act.
    """

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        kw_register: int,
        *,
        scale: float = DEFAULT_KW_SCALE,
        max_retries: int = 1,
        client: object = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Store connection params and prepare (or accept) a Modbus client."""
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.kw_register = kw_register
        self.scale = scale
        self.max_retries = max_retries
        self._logger = logger or logging.getLogger(__name__)
        self._last_good_kw: Optional[float] = None
        self._connected = False
        if client is not None:
            self._client = client
        else:
            # Lazy import so SIMULATION mode never needs a working pymodbus stack.
            from pymodbus.client import ModbusTcpClient

            self._client = ModbusTcpClient(host=host, port=port)

    def _ensure_connected(self) -> None:
        """Open the Modbus connection if it isn't already established."""
        if not self._connected:
            self._connected = bool(self._client.connect())

    def _reconnect(self) -> None:
        """Close and reopen the Modbus connection after a failure."""
        try:
            self._client.close()
        except Exception:  # closing must never raise upward
            pass
        try:
            self._connected = bool(self._client.connect())
        except Exception:
            self._connected = False

    def read_power(self) -> PlcReading:
        """Read generator kW, retrying/reconnecting and degrading to FAULT."""
        for attempt in range(self.max_retries + 1):
            try:
                self._ensure_connected()
                # NOTE: `slave` is the pymodbus 3.x kwarg; confirm against the
                # installed version when wiring real hardware.
                result = self._client.read_holding_registers(
                    self.kw_register, count=1, slave=self.unit_id
                )
                if result.isError():
                    raise ModbusIOException(
                        f"Error response reading register {self.kw_register}"
                    )
                value = result.registers[0] / self.scale
                self._last_good_kw = value
                return PlcReading(generator_kw=round(value, 2), status="OK")
            except (ModbusException, ConnectionException, OSError) as exc:
                self._logger.error(
                    "PLC read failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                self._connected = False
                self._reconnect()
        # All attempts failed: degrade to FAULT with the last-good value.
        last_good = self._last_good_kw if self._last_good_kw is not None else 0.0
        return PlcReading(generator_kw=round(last_good, 2), status="FAULT")


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
