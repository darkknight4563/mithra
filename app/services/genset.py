"""Genset controller access over Modbus TCP — kW, frequency and alarms.

`RealPlc` (app/services/plc.py) reads one kW register and nothing else. That is
fine for a bench, but an islanded generator is protected by *frequency*: when
load exceeds what the engine can carry, RPM and therefore Hz sag before any kW
number tells you so. `ModbusGenset` reads the three things an operator watches
on the panel — total kW, bus frequency, and the controller's alarm word — and
returns them as a single `PlcReading`.

Register addresses differ between controller vendors, so the layout lives in a
`RegisterMap`. Named presets are provided for the two most common standalone
genset controllers; each preset records whether its addresses have been
verified against hardware. **Unverified presets are usable but log a WARN at
start-up: confirm the addresses against the vendor's Modbus/GenComm document
for your exact model before relying on them for protection.**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Optional

from pymodbus.exceptions import (
    ConnectionException,
    ModbusException,
    ModbusIOException,
)

from app.models import PlcReading
from app.services.plc import PlcInterface


@dataclass(frozen=True)
class RegisterMap:
    """Where kW, Hz and the alarm word live on a genset controller.

    Addresses are zero-based holding-register offsets as pymodbus expects them
    (i.e. the "PDU address"; some vendor tables print them 1-based or as 4xxxx).
    """

    name: str
    kw_register: int
    kw_scale: float = 1.0  # raw / kw_scale -> kW
    kw_words: int = 1  # 1 = 16-bit, 2 = 32-bit (high word first)
    kw_signed: bool = True
    hz_register: Optional[int] = None
    hz_scale: float = 10.0  # raw / hz_scale -> Hz
    alarm_register: Optional[int] = None
    alarm_mask: int = 0xFFFF  # any set bit under the mask == shutdown alarm
    verified: bool = False
    notes: str = ""

    def with_overrides(self, **kwargs) -> "RegisterMap":
        """Return a copy with the non-None entries of ``kwargs`` applied."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean)


# ---- presets ---------------------------------------------------------------
#
# generic: the original single-register contract (kW x10, no Hz, no alarms).
#
# dse_gencomm: Deep Sea Electronics 7xxx/8xxx family, GenComm protocol.
#   Page 4 (base 1024) holds live engine/electrical values with generator
#   frequency at page offset 7 (Hz x10); page 6 (base 1536) holds the 32-bit
#   power readings with generator total watts at page offset 24. Alarm
#   summaries sit on page 8 (base 2048). Offsets taken from the GenComm
#   register listing; NOT yet verified on hardware by this project.
#
# comap_intelig: ComAp InteliGen / InteliSys NT. ComAp maps are configurable
#   per controller and exported from InteliConfig, so the preset only fixes
#   the scale conventions (Hz x10, kW x1) and expects addresses via overrides.
GENSET_MAPS: dict[str, RegisterMap] = {
    "generic": RegisterMap(
        name="generic",
        kw_register=100,
        kw_scale=10.0,
        kw_words=1,
        kw_signed=False,
        verified=True,
        notes="Single kW register (kW x10). No frequency, no alarms.",
    ),
    "dse_gencomm": RegisterMap(
        name="dse_gencomm",
        kw_register=1024 + 512 + 24,  # page 6, generator total watts (32-bit)
        kw_scale=1000.0,  # W -> kW
        kw_words=2,
        kw_signed=True,
        hz_register=1024 + 7,  # page 4, generator frequency (Hz x10)
        hz_scale=10.0,
        alarm_register=2048,  # page 8, alarm summary word
        alarm_mask=0xFFFF,
        verified=False,
        notes="DSE GenComm pages 4/6/8. Verify offsets for your model.",
    ),
    "comap_intelig": RegisterMap(
        name="comap_intelig",
        kw_register=0,
        kw_scale=1.0,
        kw_words=1,
        kw_signed=True,
        hz_register=None,
        hz_scale=10.0,
        alarm_register=None,
        verified=False,
        notes="ComAp maps are exported per controller; set PLC_*_REGISTER overrides.",
    ),
}


def resolve_map(settings) -> RegisterMap:
    """Build the RegisterMap from settings: preset + per-field overrides."""
    try:
        base = GENSET_MAPS[settings.genset_map]
    except KeyError as exc:
        raise ValueError(
            f"Unknown GENSET_MAP {settings.genset_map!r}; "
            f"choose one of {sorted(GENSET_MAPS)}"
        ) from exc
    kw_register = settings.plc_kw_register if base.name == "generic" else None
    return base.with_overrides(
        kw_register=kw_register,
        kw_scale=settings.plc_kw_scale,
        kw_words=settings.plc_kw_words,
        hz_register=settings.plc_hz_register,
        hz_scale=settings.plc_hz_scale,
        alarm_register=settings.plc_alarm_register,
        alarm_mask=settings.plc_alarm_mask,
    )


def _to_signed(value: int, bits: int) -> int:
    """Interpret ``value`` as a two's-complement integer of ``bits`` width."""
    if value >= 1 << (bits - 1):
        return value - (1 << bits)
    return value


class ModbusGenset(PlcInterface):
    """Modbus TCP reader for a genset controller described by a RegisterMap.

    Behaviour on trouble mirrors RealPlc: transient I/O errors are retried with
    a reconnect, and if every attempt fails the reading degrades to FAULT with
    the last-good kW so the controller trips the fleet rather than guessing. A
    set shutdown-alarm bit is also reported as FAULT — the engine controller
    has already decided the set is not fit to carry load.
    """

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        register_map: RegisterMap,
        *,
        max_retries: int = 2,
        client=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Store connection params and the register map; accept an injected client."""
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.map = register_map
        self.max_retries = max_retries
        self._logger = logger or logging.getLogger(__name__)
        self._connected = False
        self._last_good_kw: Optional[float] = None
        self._last_good_hz: Optional[float] = None
        if client is not None:
            self._client = client
        else:  # pragma: no cover - real network client
            from pymodbus.client import ModbusTcpClient

            self._client = ModbusTcpClient(host=host, port=port)
        if not register_map.verified:
            self._logger.warning(
                "Genset register map %r is NOT hardware-verified — confirm "
                "addresses against the controller's Modbus document (%s)",
                register_map.name,
                register_map.notes,
            )

    # ---- connection plumbing -----------------------------------------

    def _ensure_connected(self) -> None:
        """Open the Modbus connection if it isn't already established."""
        if not self._connected:
            self._client.connect()
            self._connected = True

    def _reconnect(self) -> None:
        """Close and reopen the Modbus connection after a failure."""
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass
        self._connected = False

    def _read(self, register: int, count: int) -> list[int]:
        """Read ``count`` holding registers starting at ``register``."""
        result = self._client.read_holding_registers(
            register, count=count, slave=self.unit_id
        )
        if result.isError():
            raise ModbusIOException(f"Error response reading register {register}")
        return list(result.registers)

    # ---- decoding ----------------------------------------------------

    def _decode_kw(self, regs: list[int]) -> float:
        """Combine and scale the raw kW word(s)."""
        if self.map.kw_words == 2:
            raw = (regs[0] << 16) | regs[1]
            if self.map.kw_signed:
                raw = _to_signed(raw, 32)
        else:
            raw = regs[0]
            if self.map.kw_signed:
                raw = _to_signed(raw, 16)
        return raw / self.map.kw_scale

    def _read_once(self) -> PlcReading:
        """One full acquisition: kW, optional Hz, optional alarm word."""
        kw = self._decode_kw(self._read(self.map.kw_register, self.map.kw_words))
        hz: Optional[float] = None
        if self.map.hz_register is not None:
            hz = self._read(self.map.hz_register, 1)[0] / self.map.hz_scale
        alarm = False
        if self.map.alarm_register is not None:
            word = self._read(self.map.alarm_register, 1)[0]
            alarm = bool(word & self.map.alarm_mask)
        self._last_good_kw = kw
        self._last_good_hz = hz
        if alarm:
            self._logger.error("Genset shutdown alarm active (word masked non-zero)")
            return PlcReading(generator_kw=round(kw, 2), status="FAULT", frequency_hz=hz)
        return PlcReading(generator_kw=round(kw, 2), status="OK", frequency_hz=hz)

    # ---- public -------------------------------------------------------

    def read_power(self) -> PlcReading:
        """Return the latest reading, degrading to FAULT if the link is dead."""
        for attempt in range(self.max_retries + 1):
            try:
                self._ensure_connected()
                return self._read_once()
            except (ModbusException, ConnectionException, OSError) as exc:
                self._logger.error(
                    "Genset read failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                self._reconnect()
        last_good = self._last_good_kw if self._last_good_kw is not None else 0.0
        return PlcReading(
            generator_kw=round(last_good, 2),
            status="FAULT",
            frequency_hz=self._last_good_hz,
        )
