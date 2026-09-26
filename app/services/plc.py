"""PLC access layer.

A single interface (`PlcInterface`) abstracts power readings so the rest of the
system never cares whether it's talking to a simulator or real hardware:

  * `SimulatedPlc` — in-process fake used in SIMULATION mode. Supports scenario
    modifiers (ramp / step / noise) that the controller drives.
  * `RealPlc` — pymodbus-backed single-register reader (kW only). Kept for
    bench rigs and backwards compatibility.
  * `ModbusGenset` (app/services/genset.py) — reads kW, bus frequency and the
    alarm word from a genset controller via a RegisterMap. This is what LIVE
    mode uses.

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
# Simulated bus frequency: nominal with a few hundredths of a hertz of jitter.
DEFAULT_NOMINAL_HZ = 50.0
DEFAULT_HZ_JITTER = 0.03


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
        nominal_hz: float = DEFAULT_NOMINAL_HZ,
        hz_jitter: float = DEFAULT_HZ_JITTER,
    ) -> None:
        """Initialise the simulator with a base output, noise and a clock."""
        self.base_kw = base_kw
        self.noise_level = noise_level
        self.nominal_hz = nominal_hz
        self.hz_jitter = hz_jitter
        self.step_multiplier = 1.0
        self._ramp_rate = 0.0  # kW per second
        self._ramp_t0: Optional[float] = None
        self._fault_until: Optional[float] = None
        self._underfreq_hz: Optional[float] = None
        self._underfreq_until: Optional[float] = None
        self._clock = clock

    def _frequency(self) -> float:
        """Return the simulated bus frequency (a forced sag takes precedence)."""
        if self._underfreq_until is not None:
            if self._clock() < self._underfreq_until and self._underfreq_hz is not None:
                return self._underfreq_hz
            self._underfreq_until = None
            self._underfreq_hz = None
        hz = self.nominal_hz
        if self.hz_jitter:
            hz += random.uniform(-self.hz_jitter, self.hz_jitter)
        return round(hz, 2)

    def read_power(self) -> PlcReading:
        """Compute and return the current simulated reading."""
        # Forced generator fault (demo / failsafe test) takes precedence.
        if self._fault_until is not None:
            if self._clock() < self._fault_until:
                return PlcReading(generator_kw=0.0, status="FAULT", frequency_hz=0.0)
            self._fault_until = None
        value = self.base_kw
        if self._ramp_rate and self._ramp_t0 is not None:
            value += self._ramp_rate * (self._clock() - self._ramp_t0)
        value *= self.step_multiplier
        if self.noise_level:
            value += value * random.uniform(-self.noise_level, self.noise_level)
        value = max(0.0, value)
        return PlcReading(
            generator_kw=round(value, 2), status="OK", frequency_hz=self._frequency()
        )

    # ---- scenario modifiers (driven by the controller) -------------------

    def start_ramp(self, rate_kw_per_sec: float = 0.5) -> None:
        """Begin (or re-anchor) a linear ramp at ``rate_kw_per_sec``."""
        # Fold any ramp accrued so far into the base so the ramp stays smooth.
        if self._ramp_rate and self._ramp_t0 is not None:
            self.base_kw += self._ramp_rate * (self._clock() - self._ramp_t0)
        self._ramp_rate = rate_kw_per_sec
        self._ramp_t0 = self._clock()

    def freeze_ramp(self) -> None:
        """Stop an active ramp, folding what's accrued into the base value."""
        if self._ramp_rate and self._ramp_t0 is not None:
            self.base_kw += self._ramp_rate * (self._clock() - self._ramp_t0)
        self._ramp_rate = 0.0
        self._ramp_t0 = None

    def sudden_drop(self, pct: float = 0.20) -> None:
        """Instantly reduce output by ``pct`` (0.20 == -20%)."""
        self.step_multiplier *= 1.0 - pct

    def set_noise(self, level: float) -> None:
        """Set the fractional noise level (e.g. 0.03 for +/-3%)."""
        self.noise_level = level

    def force_fault(self, seconds: float = 8.0) -> None:
        """Simulate a generator fault for ``seconds`` (reads return FAULT/0)."""
        self._fault_until = self._clock() + seconds

    def force_underfrequency(self, hz: float, seconds: float = 10.0) -> None:
        """Hold the bus at ``hz`` for ``seconds`` (an overloaded, sagging engine)."""
        self._underfreq_hz = hz
        self._underfreq_until = self._clock() + seconds

    def reset(self) -> None:
        """Clear all scenario modifiers and any forced fault."""
        self.step_multiplier = 1.0
        self._ramp_rate = 0.0
        self._ramp_t0 = None
        self._fault_until = None
        self._underfreq_hz = None
        self._underfreq_until = None


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
        from app.services.genset import ModbusGenset, resolve_map

        return ModbusGenset(
            host=settings.plc_host,
            port=settings.plc_port,
            unit_id=settings.plc_unit_id,
            register_map=resolve_map(settings),
        )
    return SimulatedPlc(nominal_hz=settings.nominal_hz)
