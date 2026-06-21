"""Tests for the deterministic load-balancing controller."""

from app.models import PlcReading
from app.services.controller import Controller
from app.services.miners import MinerService
from app.services.plc import PlcInterface


class FixedPlc(PlcInterface):
    """PLC stub that always returns the same reading — deterministic tests."""

    def __init__(self, kw: float, status: str = "OK") -> None:
        self.kw = kw
        self.status = status

    def read_power(self) -> PlcReading:
        return PlcReading(generator_kw=self.kw, status=self.status)  # type: ignore[arg-type]


def _make_controller(plc: PlcInterface) -> Controller:
    return Controller(
        plc=plc,
        miners=MinerService(boot_seconds=0),  # boots complete immediately
        buffer_factor=0.90,
        hysteresis=0.05,
        loop_interval=10,
    )


def test_add_decision_when_power_is_abundant():
    ctrl = _make_controller(FixedPlc(300.0))  # available = 270 kW, huge headroom
    before = ctrl.state  # not yet populated; count from the fleet instead
    on_before = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")

    decision = ctrl.tick()

    assert decision == "ADD"
    on_after = sum(1 for m in ctrl.miners.list_miners() if m.status == "ON")
    assert on_after == on_before + 1
    assert ctrl.state.active_miners == on_after
    assert before is ctrl.state  # same shared state object


def test_remove_decision_on_overload():
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
    ctrl = _make_controller(FixedPlc(0.0))

    decision = ctrl.tick()

    assert decision == "FAILSAFE"
    assert all(m.status == "OFF" for m in ctrl.miners.list_miners())
    assert ctrl.state.active_miners == 0
    assert any(
        e.level == "CRITICAL"
        and "gas auto-diverts to flare" in e.message
        for e in ctrl.recent_logs()
    )


def test_failsafe_on_fault_status():
    ctrl = _make_controller(FixedPlc(110.0, status="FAULT"))

    decision = ctrl.tick()

    assert decision == "FAILSAFE"
    assert all(m.status == "OFF" for m in ctrl.miners.list_miners())
