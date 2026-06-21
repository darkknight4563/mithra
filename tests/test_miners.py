"""Smoke tests for the simulated miner fleet service."""

from app.services.miners import MinerService

from .conftest import FakeClock


def test_fleet_is_seeded_realistically():
    svc = MinerService()
    miners = svc.list_miners()
    assert len(miners) == 10
    assert [m.id for m in miners] == list(range(1, 11))
    assert [m.ip for m in miners] == [f"192.168.1.{100 + i}" for i in range(1, 11)]
    assert sorted(m.priority for m in miners) == list(range(1, 11))
    for m in miners:
        assert 2.88 <= m.power_kw <= 3.25
    # Exactly one unit left in ERROR, plus a mix of ON/OFF.
    statuses = [m.status for m in miners]
    assert statuses.count("ERROR") == 1
    assert "ON" in statuses and "OFF" in statuses
    # ON miners report a hashrate; everything else reports null.
    for m in miners:
        if m.status == "ON":
            assert m.hashrate_mhs is not None
        else:
            assert m.hashrate_mhs is None


def test_turn_on_goes_through_booting_then_on():
    clock = FakeClock()
    svc = MinerService(boot_seconds=10, clock=clock)
    off = next(m for m in svc.list_miners() if m.status == "OFF")

    booting = svc.set_power(off.id, "ON")
    assert booting.status == "BOOTING"
    assert booting.hashrate_mhs is None

    # Still booting before the delay elapses...
    clock.advance(5)
    assert svc.get(off.id).status == "BOOTING"

    # ...then ON with a plausible hashrate once it does.
    clock.advance(6)
    now_on = svc.get(off.id)
    assert now_on.status == "ON"
    assert 50.0 <= now_on.hashrate_mhs <= 110.0


def test_turn_off_clears_hashrate():
    svc = MinerService(boot_seconds=0)
    on = next(m for m in svc.list_miners() if m.status == "ON")
    off = svc.set_power(on.id, "OFF")
    assert off.status == "OFF"
    assert off.hashrate_mhs is None
