"""Tests for the Modbus genset reader — mocked pymodbus client, no network."""

import logging
from unittest.mock import MagicMock

import pytest
from pymodbus.exceptions import ModbusIOException

from app.config import Settings
from app.services.genset import GENSET_MAPS, ModbusGenset, RegisterMap, resolve_map


def _ok(registers):
    result = MagicMock()
    result.isError.return_value = False
    result.registers = registers
    return result


def _err():
    result = MagicMock()
    result.isError.return_value = True
    return result


def _client_with(table: dict[int, list[int]]):
    """Mock client whose read_holding_registers answers from a register table."""
    client = MagicMock()

    def read(register, count=1, slave=1):
        if register not in table:
            return _err()
        return _ok(table[register][:count])

    client.read_holding_registers.side_effect = read
    return client


FULL_MAP = RegisterMap(
    name="test",
    kw_register=10,
    kw_scale=1000.0,
    kw_words=2,
    kw_signed=True,
    hz_register=20,
    hz_scale=10.0,
    alarm_register=30,
    alarm_mask=0x00FF,
    verified=True,
)


def test_reads_kw_hz_and_ok_status():
    """32-bit W, Hz x10 and a clean alarm word produce an OK reading."""
    watts = 112_400  # 112.4 kW
    table = {10: [watts >> 16, watts & 0xFFFF], 20: [498], 30: [0]}
    g = ModbusGenset("h", 502, 1, FULL_MAP, client=_client_with(table))
    r = g.read_power()
    assert r.status == "OK"
    assert r.generator_kw == pytest.approx(112.4)
    assert r.frequency_hz == pytest.approx(49.8)


def test_signed_32bit_decoding():
    """Negative (reverse-power) values decode as two's complement."""
    raw = (-5000) & 0xFFFFFFFF  # -5 kW
    table = {10: [raw >> 16, raw & 0xFFFF], 20: [500], 30: [0]}
    g = ModbusGenset("h", 502, 1, FULL_MAP, client=_client_with(table))
    assert g.read_power().generator_kw == pytest.approx(-5.0)


def test_alarm_bit_under_mask_is_fault():
    """A set shutdown-alarm bit reports FAULT while still carrying kW and Hz."""
    table = {10: [0, 50_000], 20: [500], 30: [0x0004]}
    g = ModbusGenset("h", 502, 1, FULL_MAP, client=_client_with(table))
    r = g.read_power()
    assert r.status == "FAULT"
    assert r.generator_kw == pytest.approx(50.0)
    assert r.frequency_hz == pytest.approx(50.0)


def test_alarm_bit_outside_mask_is_ignored():
    """Bits above the mask (warnings, not shutdowns) do not trip FAULT."""
    table = {10: [0, 50_000], 20: [500], 30: [0x0100]}
    g = ModbusGenset("h", 502, 1, FULL_MAP, client=_client_with(table))
    assert g.read_power().status == "OK"


def test_generic_map_reads_kw_only():
    """The generic single-register map yields no frequency."""
    table = {100: [1124]}
    g = ModbusGenset("h", 502, 1, GENSET_MAPS["generic"], client=_client_with(table))
    r = g.read_power()
    assert r.generator_kw == pytest.approx(112.4)
    assert r.frequency_hz is None


def test_io_error_degrades_to_fault_with_last_good():
    """After a good read, a dead link returns FAULT with the last-good values."""
    table = {10: [0, 90_000], 20: [500], 30: [0]}
    client = _client_with(table)
    g = ModbusGenset("h", 502, 1, FULL_MAP, client=client, max_retries=1)
    assert g.read_power().status == "OK"
    client.read_holding_registers.side_effect = ModbusIOException("link down")
    r = g.read_power()
    assert r.status == "FAULT"
    assert r.generator_kw == pytest.approx(90.0)
    assert r.frequency_hz == pytest.approx(50.0)
    assert client.close.called


def test_unverified_map_logs_warning(caplog):
    """Presets not verified on hardware warn loudly at construction."""
    with caplog.at_level(logging.WARNING):
        ModbusGenset("h", 502, 1, GENSET_MAPS["dse_gencomm"], client=MagicMock())
    assert "NOT hardware-verified" in caplog.text


def test_resolve_map_applies_overrides():
    """Settings overrides replace preset fields; generic keeps PLC_KW_REGISTER."""
    s = Settings(genset_map="generic", plc_kw_register=7, plc_hz_register=8, plc_hz_scale=100)
    m = resolve_map(s)
    assert (m.kw_register, m.hz_register, m.hz_scale) == (7, 8, 100.0)
    s = Settings(genset_map="dse_gencomm", plc_kw_register=7)
    assert resolve_map(s).kw_register == GENSET_MAPS["dse_gencomm"].kw_register
    with pytest.raises(ValueError):
        resolve_map(Settings(genset_map="nope"))
