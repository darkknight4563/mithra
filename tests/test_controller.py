"""Tests for the deterministic load-balancing controller."""

from app.models import PlcReading
from app.services.controller import Controller
from app.services.miners import MinerService
from app.services.plc import PlcInterface, SimulatedPlc

from .conftest import FakeClock


class FixedPlc(PlcInterface):
    """PLC stub that always returns the same reading — deterministic tests."""

    def __init__(self, kw: float, status: str = "OK") -> None:
        """Store the fixed kW value and status to report."""
        self.kw = kw
        self.status = status

    def read_power(self) -> PlcReading:
        """Return the configured fixed reading."""
        return PlcReading(generator_kw=self.kw, status=self.status)  # type: ignore[arg-type]


def _make_controller(plc: PlcInterface) -> Controller:
    """Build a controller wired to ``plc`` with a fast-booting fleet."""
    return Controller(
        plc=plc,
        miners=MinerService(boot_seconds=0),  # boots complete immediately
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
    )


def test_add_decision_when_power_is_abundant():
    """Abundant available power adds the next OFF miner by priority."""
    ctrl = _make_controller(FixedPlc(300.0))  # available = 270 kW, huge headroom
    before = ctrl.state  # shared-state identity check below
    on_before = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")

    decision = ctrl.tick()

    assert decision == "ADD"
    on_after = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")
    assert on_after == on_before + 1
    assert ctrl.state.active_miners == on_after
    assert before is ctrl.state  # same shared state object


def test_remove_decision_on_overload():
    """Insufficient power sheds the lowest-priority ON miner and logs CRITICAL."""
    ctrl = _make_controller(FixedPlc(5.0))  # available = 4.5 kW, way under load
    on_before = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")

    decision = ctrl.tick()

    assert decision == "REMOVE"
    on_after = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")
    assert on_after == on_before - 1
    assert any(
        e.level == "CRITICAL" and "Power overload detected" in e.message
        for e in ctrl.recent_logs()
    )


def test_hysteresis_deadband_holds_steady():
    """Inside the hysteresis dead-band neither ADD nor REMOVE fires."""
    miners = MinerService(boot_seconds=0)
    committed = sum(
        m.power_kw for m in miners.list_miners() if m.status in ("ON", "BOOTING")
    )
    # Pick available power exactly equal to the current load: inside the
    # dead-band on both sides, so neither ADD nor REMOVE should fire.
    plc = FixedPlc(committed / 0.90)
    ctrl = Controller(
        plc=plc,
        miners=miners,
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
    )
    on_before = sum(1 for m in miners.list_miners() if m.status == "ON")

    decision = ctrl.tick()

    assert decision == "NONE"
    on_after = sum(1 for m in miners.list_miners() if m.status == "ON")
    assert on_after == on_before


def test_failsafe_zero_kw_stops_all_miners():
    """A 0 kW reading trips the fail-safe: every miner goes OFF."""
    ctrl = _make_controller(FixedPlc(0.0))

    decision = ctrl.tick()

    assert decision == "FAILSAFE"
    assert all(m.status == "OFF" for m in ctrl.miners.list_miners())
    assert ctrl.state.active_miners == 0
    assert any(
        e.level == "CRITICAL" and "gas auto-diverts to flare" in e.message
        for e in ctrl.recent_logs()
    )


def test_failsafe_on_fault_status():
    """A FAULT status (even with positive kW) also trips the fail-safe."""
    ctrl = _make_controller(FixedPlc(110.0, status="FAULT"))

    decision = ctrl.tick()

    assert decision == "FAILSAFE"
    assert all(m.status == "OFF" for m in ctrl.miners.list_miners())


def test_scenario_modifiers_change_available_power():
    """Scenario modifiers (drop then ramp) actually move the PLC reading."""
    clock = FakeClock()
    plc = SimulatedPlc(base_kw=100.0, noise_level=0.0, clock=clock)
    ctrl = Controller(
        plc=plc,
        miners=MinerService(boot_seconds=0),
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
    )

    ctrl.tick()
    baseline = ctrl.state.latest_plc_kw

    ctrl.apply_scenario("sudden_drop")  # -20%
    ctrl.tick()
    dropped = ctrl.state.latest_plc_kw
    assert dropped < baseline

    ctrl.apply_scenario("ramp_up")  # +0.5 kW/s
    clock.advance(20)
    ctrl.tick()
    assert ctrl.state.latest_plc_kw > dropped


# ---- frequency governor ------------------------------------------------------


class FreqPlc(PlcInterface):
    """PLC stub with abundant kW and a settable bus frequency."""

    def __init__(self, hz: float | None, kw: float = 300.0) -> None:
        """Store the frequency (None = source reports no Hz) and kW."""
        self.hz = hz
        self.kw = kw

    def read_power(self) -> PlcReading:
        """Return an OK reading at the configured kW and Hz."""
        return PlcReading(generator_kw=self.kw, status="OK", frequency_hz=self.hz)


def _on_count(ctrl: Controller) -> int:
    return sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")


def test_classify_frequency_bands():
    """Nominal 50 Hz with default margins maps onto the five governor states."""
    ctrl = _make_controller(FreqPlc(50.0))
    assert ctrl.classify_frequency(None) == "N/A"
    assert ctrl.classify_frequency(50.0) == "OK"
    assert ctrl.classify_frequency(49.71) == "OK"
    assert ctrl.classify_frequency(49.6) == "HOLD"
    assert ctrl.classify_frequency(48.9) == "LOW"
    assert ctrl.classify_frequency(47.4) == "TRIP"


def test_no_frequency_keeps_kw_only_behaviour():
    """A kW-only source (Hz None) behaves exactly as before: ADD on headroom."""
    ctrl = _make_controller(FreqPlc(None))
    assert ctrl.tick() == "ADD"
    assert ctrl.state.frequency_status == "N/A"
    assert ctrl.state.latest_hz is None


def test_hold_band_blocks_add_despite_headroom():
    """Below nominal minus the add margin nothing is added even with spare kW."""
    ctrl = _make_controller(FreqPlc(49.5))
    before = _on_count(ctrl)
    assert ctrl.tick() == "NONE"
    assert _on_count(ctrl) == before
    assert ctrl.state.frequency_status == "HOLD"
    assert any("holding fleet" in e.message for e in ctrl.recent_logs())


def test_low_band_sheds_one_miner_per_tick_despite_headroom():
    """Under-frequency sheds the lowest-priority miner even though kW looks fine."""
    ctrl = _make_controller(FreqPlc(48.8))
    before = _on_count(ctrl)
    assert ctrl.tick() == "REMOVE"
    assert _on_count(ctrl) == before - 1
    assert ctrl.tick() == "REMOVE"
    assert _on_count(ctrl) == before - 2
    assert ctrl.state.frequency_status == "LOW"
    assert any("Under-frequency" in e.message for e in ctrl.recent_logs())


def test_trip_band_is_failsafe():
    """Frequency collapse trips every miner OFF in one tick."""
    ctrl = _make_controller(FreqPlc(47.0))
    assert _on_count(ctrl) > 0
    assert ctrl.tick() == "FAILSAFE"
    assert _on_count(ctrl) == 0
    assert ctrl.state.active_miners == 0
    assert ctrl.state.frequency_status == "TRIP"
    assert any("Under-frequency trip" in e.message for e in ctrl.recent_logs())


def test_recovery_after_frequency_trip():
    """Once Hz recovers the controller adds miners back one per tick."""
    plc = FreqPlc(47.0)
    ctrl = _make_controller(plc)
    ctrl.tick()
    assert _on_count(ctrl) == 0
    plc.hz = 50.0
    assert ctrl.tick() == "ADD"
    assert _on_count(ctrl) == 1


def test_sixty_hz_nominal_shifts_bands():
    """The governor is nominal-relative: a 60 Hz site sheds at 59, trips at 57.5."""
    ctrl = Controller(
        plc=FreqPlc(58.5),
        miners=MinerService(boot_seconds=0),
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
        nominal_hz=60.0,
    )
    assert ctrl.classify_frequency(59.5) == "HOLD"
    assert ctrl.tick() == "REMOVE"


def test_simulated_plc_underfrequency_scenario():
    """The simulator's forced sag is visible to the controller and then clears."""
    clock = FakeClock()
    plc = SimulatedPlc(noise_level=0.0, clock=clock, hz_jitter=0.0)
    ctrl = Controller(
        plc=plc,
        miners=MinerService(boot_seconds=0, clock=clock),
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
    )
    ctrl.apply_scenario("underfrequency")
    assert ctrl.tick() == "REMOVE"
    assert ctrl.state.frequency_status == "LOW"
    clock.advance(30)
    ctrl.tick()
    assert ctrl.state.frequency_status == "OK"
