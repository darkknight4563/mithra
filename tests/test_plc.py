"""Smoke tests for the simulated PLC service."""

import pytest

from app.models import PlcReading
from app.services.plc import RealPlc, SimulatedPlc

from .conftest import FakeClock


def test_simulated_plc_returns_readings_in_band():
    plc = SimulatedPlc(noise_level=0.02)
    for _ in range(20):
        reading = plc.read_power()
        assert isinstance(reading, PlcReading)
        assert reading.status == "OK"
        # ~110 kW base with +/-2% noise -> comfortably inside this band.
        assert 90.0 < reading.generator_kw < 140.0


def test_sudden_drop_reduces_power():
    plc = SimulatedPlc(noise_level=0.0)
    before = plc.read_power().generator_kw
    plc.sudden_drop(0.20)
    after = plc.read_power().generator_kw
    assert after == pytest.approx(before * 0.8, rel=1e-6)


def test_ramp_increases_power_over_time():
    clock = FakeClock()
    plc = SimulatedPlc(noise_level=0.0, clock=clock)
    base = plc.read_power().generator_kw
    plc.start_ramp(0.5)  # +0.5 kW/sec
    clock.advance(10)
    assert plc.read_power().generator_kw == pytest.approx(base + 5.0, rel=1e-6)


def test_real_plc_read_is_not_implemented():
    plc = RealPlc(host="127.0.0.1", port=502, unit_id=1, kw_register=100)
    with pytest.raises(NotImplementedError):
        plc.read_power()
