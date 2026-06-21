"""Tests for the PLC service — simulated readings and mocked Modbus hardware.

No network is touched: RealPlc is driven entirely by a mocked Modbus client.
"""

import logging
from unittest.mock import MagicMock

import pytest
from pymodbus.exceptions import ModbusIOException

from app.models import PlcReading
from app.services.plc import RealPlc, SimulatedPlc

from .conftest import FakeClock


def _ok_result(registers):
    """Build a mock Modbus result that succeeds with the given registers."""
    result = MagicMock()
    result.isError.return_value = False
    result.registers = registers
    return result


def test_simulated_plc_returns_readings_in_band():
    """SimulatedPlc yields OK readings within the expected ~110 kW band."""
    plc = SimulatedPlc(noise_level=0.02)
    for _ in range(20):
        reading = plc.read_power()
        assert isinstance(reading, PlcReading)
        assert reading.status == "OK"
        # ~110 kW base with +/-2% noise -> comfortably inside this band.
        assert 90.0 < reading.generator_kw < 140.0


def test_sudden_drop_reduces_power():
    """The sudden-drop modifier scales output down by the given fraction."""
    plc = SimulatedPlc(noise_level=0.0)
    before = plc.read_power().generator_kw
    plc.sudden_drop(0.20)
    after = plc.read_power().generator_kw
    assert after == pytest.approx(before * 0.8, rel=1e-6)


def test_ramp_increases_power_over_time():
    """The ramp modifier adds power linearly with elapsed clock time."""
    clock = FakeClock()
    plc = SimulatedPlc(noise_level=0.0, clock=clock)
    base = plc.read_power().generator_kw
    plc.start_ramp(0.5)  # +0.5 kW/sec
    clock.advance(10)
    assert plc.read_power().generator_kw == pytest.approx(base + 5.0, rel=1e-6)


def test_real_plc_decodes_and_scales_register():
    """RealPlc reads a holding register and scales raw/10 -> kW (1234 -> 123.4)."""
    client = MagicMock()
    client.connect.return_value = True
    client.read_holding_registers.return_value = _ok_result([1234])

    plc = RealPlc("10.0.0.5", 502, 1, 100, client=client)
    reading = plc.read_power()

    assert reading.status == "OK"
    assert reading.generator_kw == 123.4
    assert client.connect.called
    client.read_holding_registers.assert_called_once()


def test_real_plc_handles_io_error_and_retries(caplog):
    """On a Modbus I/O error RealPlc logs, reconnects, and degrades to FAULT.

    A prior good read seeds the last-good value, which the FAULT reading then
    carries; the client's connect() is called again, proving a reconnect attempt.
    """
    client = MagicMock()
    client.connect.return_value = True
    client.read_holding_registers.return_value = _ok_result([1234])

    plc = RealPlc("10.0.0.5", 502, 1, 100, client=client)
    assert plc.read_power().generator_kw == 123.4  # seed last-good
    connects_after_good = client.connect.call_count

    client.read_holding_registers.side_effect = ModbusIOException("bus down")
    with caplog.at_level(logging.ERROR):
        reading = plc.read_power()

    assert reading.status == "FAULT"
    assert reading.generator_kw == 123.4  # last good value retained
    assert "PLC read failed" in caplog.text
    assert client.connect.call_count > connects_after_good  # reconnect attempted
    assert client.close.called


def test_real_plc_fault_without_last_good_returns_zero():
    """With no prior good read, a persistent failure yields FAULT at 0.0 kW."""
    client = MagicMock()
    client.connect.return_value = True
    client.read_holding_registers.side_effect = ModbusIOException("bus down")

    plc = RealPlc("10.0.0.5", 502, 1, 100, client=client)
    reading = plc.read_power()

    assert reading.status == "FAULT"
    assert reading.generator_kw == 0.0


def test_real_plc_treats_error_response_as_failure():
    """An isError() Modbus response is handled as a fault, not a value."""
    client = MagicMock()
    client.connect.return_value = True
    error_result = MagicMock()
    error_result.isError.return_value = True
    client.read_holding_registers.return_value = error_result

    plc = RealPlc("10.0.0.5", 502, 1, 100, client=client)
    reading = plc.read_power()

    assert reading.status == "FAULT"
