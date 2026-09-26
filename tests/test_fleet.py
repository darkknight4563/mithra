"""Tests for the real-fleet adapter — driven by a fake CGMiner API transport.

No sockets are opened. `FakeMinerNet` plays every miner on the "LAN": it
records the commands it receives and answers with canned JSON so the adapter's
classification (ON / BOOTING / OFF / ERROR) and its firmware-specific control
paths (BOS pause/resume, LuxOS logon+curtail, stock refusal) are all covered.
"""

import json

import pytest

from app.services.fleet import (
    CgminerFleetService,
    MinerHost,
    load_fleet_config,
    parse_reply,
    summary_hashrate_mhs,
)

from .conftest import FakeClock


def _reply(obj: dict) -> bytes:
    """Encode a reply the way real firmware does: JSON plus a trailing NUL."""
    return json.dumps(obj).encode() + b"\x00"


def _ok_status(msg: str = "OK") -> dict:
    return {"STATUS": [{"STATUS": "S", "Msg": msg}]}


class FakeMinerNet:
    """Answers CGMiner API requests for a set of miners keyed by IP."""

    def __init__(self) -> None:
        self.hashrate: dict[str, float] = {}  # ip -> reported "GHS 5s"
        self.reachable: set[str] = set()
        self.sent: list[tuple[str, str, str]] = []  # (ip, command, parameter)
        self.refuse: set[str] = set()  # ips that refuse control commands

    def __call__(self, ip: str, port: int, payload: bytes, timeout: float) -> bytes:
        if ip not in self.reachable:
            raise OSError("connection refused")
        req = json.loads(payload)
        cmd, param = req["command"], req.get("parameter", "")
        self.sent.append((ip, cmd, param))
        if cmd == "summary":
            return _reply({**_ok_status(), "SUMMARY": [{"GHS 5s": self.hashrate.get(ip, 0.0)}]})
        if cmd == "logon":
            return _reply({**_ok_status(), "SESSION": [{"SessionID": "abc123"}]})
        if cmd in ("pause", "resume", "curtail"):
            if ip in self.refuse:
                return _reply({"STATUS": [{"STATUS": "E", "Msg": "Access denied"}]})
            return _reply(_ok_status())
        return _reply({"STATUS": [{"STATUS": "E", "Msg": f"Invalid command {cmd}"}]})


@pytest.fixture
def net() -> FakeMinerNet:
    return FakeMinerNet()


def _hosts() -> list[MinerHost]:
    return [
        MinerHost(id=1, ip="10.0.0.1", priority=1, nominal_kw=3.2, firmware="bos"),
        MinerHost(id=2, ip="10.0.0.2", priority=2, nominal_kw=3.2, firmware="luxos"),
        MinerHost(id=3, ip="10.0.0.3", priority=3, nominal_kw=3.2, firmware="stock"),
    ]


def _service(net: FakeMinerNet, clock: FakeClock) -> CgminerFleetService:
    return CgminerFleetService(_hosts(), transport=net, clock=clock, poll_interval=5.0)


# ---- protocol helpers --------------------------------------------------------


def test_parse_reply_tolerates_bitmain_brace_bug():
    """Stock firmware's `}{` between objects is repaired before decoding."""
    raw = b'{"STATUS":[{"STATUS":"S"}],"SUMMARY":[{"GHS 5s":100}{"x":1}]}\x00'
    reply = parse_reply(raw)
    assert reply["SUMMARY"][0]["GHS 5s"] == 100


@pytest.mark.parametrize(
    "block, expected",
    [
        ({"GHS 5s": 95.5}, 95500.0),
        ({"MHS av": 1234.0}, 1234.0),
        ({"THS 5s": 0.2}, 200000.0),
        ({"Elapsed": 10}, None),
    ],
)
def test_summary_hashrate_units_normalised(block, expected):
    """GHS / MHS / THS keys all come back as MH/s."""
    assert summary_hashrate_mhs({"SUMMARY": [block]}) == expected


def test_load_fleet_config(tmp_path):
    """fleet.json is parsed into MinerHost records; duplicate ids are rejected."""
    cfg = tmp_path / "fleet.json"
    cfg.write_text(
        json.dumps(
            {
                "miners": [
                    {"id": 1, "ip": "10.0.0.1", "nominal_kw": 3.25, "firmware": "bos"},
                    {"id": 2, "ip": "10.0.0.2", "priority": 9, "nominal_kw": 3.0},
                ]
            }
        )
    )
    hosts = load_fleet_config(cfg)
    assert [h.id for h in hosts] == [1, 2]
    assert hosts[0].priority == 1  # defaults to id
    assert hosts[1].priority == 9
    assert hosts[1].firmware == "bos"  # default
    cfg.write_text(json.dumps([{"id": 1, "ip": "a", "nominal_kw": 1}] * 2))
    with pytest.raises(ValueError):
        load_fleet_config(cfg)


# ---- classification ----------------------------------------------------------


def test_unreachable_miner_is_error(net):
    """A miner that does not answer on 4028 is reported as ERROR."""
    clock = FakeClock()
    svc = _service(net, clock)
    miners = svc.list_miners()
    assert {m.status for m in miners} == {"ERROR"}
    assert all(m.hashrate_mhs is None for m in miners)


def test_hashing_miner_is_on_with_hashrate(net):
    """A reachable, hashing miner is ON and carries its reported hashrate."""
    net.reachable = {"10.0.0.1"}
    net.hashrate["10.0.0.1"] = 120.0  # GH/s
    svc = _service(net, FakeClock())
    m = svc.get(1)
    assert m.status == "ON"
    assert m.hashrate_mhs == pytest.approx(120000.0)
    assert m.power_kw == 3.2  # nominal from inventory


def test_poll_is_cached_until_interval_elapses(net):
    """list_miners() does not hit the network again inside poll_interval."""
    net.reachable = {"10.0.0.1"}
    net.hashrate["10.0.0.1"] = 50.0
    clock = FakeClock()
    svc = _service(net, clock)
    svc.list_miners()
    svc.list_miners()
    assert len([s for s in net.sent if s[1] == "summary"]) == 1
    clock.advance(6)
    svc.list_miners()
    assert len([s for s in net.sent if s[1] == "summary"]) == 2


# ---- control ---------------------------------------------------------------


def test_bos_pause_and_resume_drive_status(net):
    """BOS: OFF sends `pause`; ON sends `resume` and reads BOOTING until hashing."""
    net.reachable = {"10.0.0.1"}
    net.hashrate["10.0.0.1"] = 80.0
    clock = FakeClock()
    svc = _service(net, clock)
    assert svc.get(1).status == "ON"

    m = svc.set_power(1, "OFF")
    assert m.status == "OFF"
    assert ("10.0.0.1", "pause", "") in net.sent
    clock.advance(6)
    net.hashrate["10.0.0.1"] = 0.0  # paused board reports no hashrate
    assert svc.get(1).status == "OFF"  # commanded OFF wins over hashrate

    m = svc.set_power(1, "ON")
    assert m.status == "BOOTING"
    assert ("10.0.0.1", "resume", "") in net.sent
    clock.advance(6)
    assert svc.get(1).status == "BOOTING"  # still zero hashrate, inside grace
    net.hashrate["10.0.0.1"] = 75.0
    clock.advance(6)
    assert svc.get(1).status == "ON"


def test_resumed_miner_without_hashrate_becomes_error_after_grace(net):
    """A resumed miner that never hashes is ERROR once the boot grace expires."""
    net.reachable = {"10.0.0.1"}
    clock = FakeClock()
    svc = _service(net, clock)
    svc.set_power(1, "ON")
    clock.advance(CgminerFleetService.BOOT_GRACE_SECONDS + 1)
    assert svc.get(1).status == "ERROR"


def test_luxos_uses_logon_then_curtail(net):
    """LuxOS: first control command logs on, then `curtail <session>,sleep`."""
    net.reachable = {"10.0.0.2"}
    svc = _service(net, FakeClock())
    svc.set_power(2, "OFF")
    cmds = [(c, p) for ip, c, p in net.sent if ip == "10.0.0.2"]
    assert ("logon", "") in cmds
    assert ("curtail", "abc123,sleep") in cmds
    net.sent.clear()
    svc.set_power(2, "ON")
    cmds = [(c, p) for ip, c, p in net.sent if ip == "10.0.0.2"]
    assert ("logon", "") not in cmds  # session cached
    assert ("curtail", "abc123,wakeup") in cmds


def test_stock_firmware_cannot_be_controlled(net):
    """Stock Bitmain has no pause: set_power marks the miner ERROR, no crash."""
    net.reachable = {"10.0.0.3"}
    net.hashrate["10.0.0.3"] = 90.0
    svc = _service(net, FakeClock())
    assert svc.get(3).status == "ON"
    m = svc.set_power(3, "OFF")
    assert m.status == "ERROR"
    assert not [s for s in net.sent if s[1] in ("pause", "curtail")]


def test_refused_control_command_marks_error(net):
    """An 'E' STATUS on pause/resume marks the miner ERROR instead of lying."""
    net.reachable = {"10.0.0.1"}
    net.hashrate["10.0.0.1"] = 90.0
    net.refuse = {"10.0.0.1"}
    svc = _service(net, FakeClock())
    m = svc.set_power(1, "OFF")
    assert m.status == "ERROR"
