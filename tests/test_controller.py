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
