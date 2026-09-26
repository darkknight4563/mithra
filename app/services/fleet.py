"""Real miner fleet over the CGMiner-family TCP API.

Every ASIC firmware that matters speaks the CGMiner API on TCP port 4028:
stock Bitmain firmware, Braiins OS / BOSminer, LuxOS, and the Bitaxe/ESP-Miner
family. Requests are a JSON object on a socket, replies are JSON terminated
by a NUL byte. Reading is uniform (`summary`); *control* is firmware-specific:

  * Braiins OS (bos)  — `pause` / `resume` stop and restart hashing while the
                        machine stays reachable.
  * LuxOS (luxos)     — `logon` for a session, then `curtail` with
                        `<session>,sleep` / `<session>,wakeup`.
  * Stock Bitmain     — has no pause command; only `summary` is supported and
                        set_power is refused. Use a smart PDU for those.

`CgminerFleetService` exposes the same surface as the simulated
`MinerService` (`list_miners`, `get`, `set_power`) so the controller does not
know or care which one it is driving. State is polled, not pushed: the fleet is
re-read at most every ``poll_interval`` seconds and cached in between, and the
miner's own reported hashrate decides ON vs BOOTING (a resumed machine reports
zero hashrate for a while — that is committed load the controller must count).

Transport is injectable so the whole adapter is unit-tested against canned
replies; nothing here needs a miner on the LAN to be exercised.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

from app.models import Miner

Firmware = Literal["bos", "luxos", "stock"]
Transport = Callable[[str, int, bytes, float], bytes]

_NUL = b"\x00"


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


# ---- inventory -------------------------------------------------------------


@dataclass
class MinerHost:
    """One physical miner as declared in fleet.json."""

    id: int
    ip: str
    priority: int
    nominal_kw: float
    firmware: Firmware = "bos"
    port: Optional[int] = None  # None -> service default
    label: str = ""


def load_fleet_config(path: str | Path) -> list[MinerHost]:
    """Parse fleet.json into MinerHost records (ids and priorities must be unique)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    hosts_raw = data["miners"] if isinstance(data, dict) else data
    hosts: list[MinerHost] = []
    for raw in hosts_raw:
        hosts.append(
            MinerHost(
                id=int(raw["id"]),
                ip=str(raw["ip"]),
                priority=int(raw.get("priority", raw["id"])),
                nominal_kw=float(raw["nominal_kw"]),
                firmware=raw.get("firmware", "bos"),
                port=raw.get("port"),
                label=raw.get("label", ""),
            )
        )
    ids = [h.id for h in hosts]
    if len(set(ids)) != len(ids):
        raise ValueError("fleet config: miner ids must be unique")
    return hosts


# ---- wire protocol ---------------------------------------------------------


def tcp_transport(host: str, port: int, payload: bytes, timeout: float) -> bytes:
    """Send one request and read one reply over a fresh TCP connection."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(_NUL):
                break
    return b"".join(chunks)


def parse_reply(raw: bytes) -> dict:
    """Decode a CGMiner API reply, tolerating the classic `}{` firmware bug."""
    text = raw.rstrip(_NUL).decode("utf-8", errors="replace").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Stock Bitmain firmware emits "}{" between objects in some replies.
        return json.loads(text.replace("}{", "},{"))


def _status_ok(reply: dict) -> tuple[bool, str]:
    """Return (ok, message) from the reply's STATUS block."""
    status = reply.get("STATUS")
    if isinstance(status, list) and status:
        status = status[0]
    if not isinstance(status, dict):
        return True, ""
    return status.get("STATUS", "S") in ("S", "I"), str(status.get("Msg", ""))


def summary_hashrate_mhs(reply: dict) -> Optional[float]:
    """Extract a hashrate in MH/s from a `summary` reply, whatever the units."""
    block = reply.get("SUMMARY")
    if isinstance(block, list) and block:
        block = block[0]
    if not isinstance(block, dict):
        return None
    for key, factor in (
        ("MHS 5s", 1.0),
        ("MHS av", 1.0),
        ("GHS 5s", 1e3),
        ("GHS av", 1e3),
        ("THS 5s", 1e6),
        ("THS av", 1e6),
    ):
        if key in block:
            try:
                return float(block[key]) * factor
            except (TypeError, ValueError):
                continue
    return None


class CgminerClient:
    """Minimal CGMiner-API client bound to one miner."""

    def __init__(
        self,
        host: MinerHost,
        port: int,
        timeout: float,
        transport: Transport = tcp_transport,
    ) -> None:
        """Bind to a miner host with a port, timeout and transport function."""
        self.host = host
        self.port = host.port or port
        self.timeout = timeout
        self._transport = transport
        self._session: Optional[str] = None  # LuxOS session id

    def command(self, command: str, parameter: str = "") -> dict:
        """Send one command and return the decoded reply."""
        payload: dict = {"command": command}
        if parameter:
            payload["parameter"] = parameter
        raw = self._transport(
            self.host.ip, self.port, json.dumps(payload).encode(), self.timeout
        )
        return parse_reply(raw)

    def summary(self) -> dict:
        """Return the `summary` block: hashrate, uptime, accepted/rejected."""
        return self.command("summary")

    # ---- control ---------------------------------------------------------

    def _luxos_session(self) -> str:
        """Log on to LuxOS and cache the session id."""
        if self._session is None:
            reply = self.command("logon")
            session = reply.get("SESSION")
            if isinstance(session, list) and session:
                session = session[0]
            if not isinstance(session, dict) or "SessionID" not in session:
                raise RuntimeError("LuxOS logon did not return a SessionID")
            self._session = str(session["SessionID"])
        return self._session

    def pause(self) -> None:
        """Stop hashing (keeps the control board up and reachable)."""
        fw = self.host.firmware
        if fw == "bos":
            reply = self.command("pause")
        elif fw == "luxos":
            reply = self.command("curtail", f"{self._luxos_session()},sleep")
        else:
            raise RuntimeError(
                f"firmware {fw!r} has no pause command — use a smart PDU"
            )
        ok, msg = _status_ok(reply)
        if not ok:
            raise RuntimeError(f"pause refused: {msg}")

    def resume(self) -> None:
        """Restart hashing after a pause."""
        fw = self.host.firmware
        if fw == "bos":
            reply = self.command("resume")
        elif fw == "luxos":
            reply = self.command("curtail", f"{self._luxos_session()},wakeup")
        else:
            raise RuntimeError(
                f"firmware {fw!r} has no resume command — use a smart PDU"
            )
        ok, msg = _status_ok(reply)
        if not ok:
            raise RuntimeError(f"resume refused: {msg}")


# ---- fleet service ---------------------------------------------------------


@dataclass
class _Slot:
    """Cached view of one miner between polls."""

    host: MinerHost
    client: CgminerClient
    miner: Miner
    # What we last told it to do; drives ON/BOOTING/OFF classification.
    commanded: Literal["ON", "OFF"] = "ON"
    commanded_at: float = field(default_factory=time.monotonic)


class CgminerFleetService:
    """Real fleet adapter with the MinerService surface."""

    # A resumed miner that still shows zero hashrate is BOOTING; after this
    # many seconds with no hashrate we call it ERROR instead.
    BOOT_GRACE_SECONDS = 180.0

    def __init__(
        self,
        hosts: list[MinerHost],
        *,
        port: int = 4028,
        timeout: float = 3.0,
        poll_interval: float = 5.0,
        transport: Transport = tcp_transport,
        clock: Callable[[], float] = time.monotonic,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Build clients for every host; nothing is contacted until first poll."""
        self._clock = clock
        self._poll_interval = poll_interval
        self._last_poll: Optional[float] = None
        self._logger = logger or logging.getLogger(__name__)
        self._slots: dict[int, _Slot] = {}
        for h in hosts:
            client = CgminerClient(h, port=port, timeout=timeout, transport=transport)
            miner = Miner(
                id=h.id,
                ip=h.ip,
                priority=h.priority,
                status="OFF",
                power_kw=h.nominal_kw,
                hashrate_mhs=None,
            )
            self._slots[h.id] = _Slot(
                host=h, client=client, miner=miner, commanded_at=clock()
            )

    # ---- polling -----------------------------------------------------

    def _poll_one(self, slot: _Slot) -> None:
        """Refresh one miner's status from its `summary` reply."""
        m = slot.miner
        try:
            reply = slot.client.summary()
        except Exception as exc:  # unreachable / timeout / bad JSON
            self._logger.warning("miner %s (%s) unreachable: %s", m.id, m.ip, exc)
            m.status = "ERROR"
            m.hashrate_mhs = None
            return
        ok, msg = _status_ok(reply)
        if not ok:
            self._logger.warning("miner %s summary error: %s", m.id, msg)
            m.status = "ERROR"
            m.hashrate_mhs = None
            return
        hashrate = summary_hashrate_mhs(reply)
        m.last_seen = _utcnow()
        if slot.commanded == "OFF":
            # Paused (or asleep): reachable, not hashing.
            m.status = "OFF"
            m.hashrate_mhs = None
            return
        if hashrate and hashrate > 0:
            m.status = "ON"
            m.hashrate_mhs = round(hashrate, 1)
        elif self._clock() - slot.commanded_at <= self.BOOT_GRACE_SECONDS:
            m.status = "BOOTING"
            m.hashrate_mhs = None
        else:
            m.status = "ERROR"
            m.hashrate_mhs = None

    def refresh(self, force: bool = False) -> None:
        """Poll every miner if the cache is older than ``poll_interval``."""
        now = self._clock()
        if (
            not force
            and self._last_poll is not None
            and now - self._last_poll < self._poll_interval
        ):
            return
        # Poll concurrently: an unreachable miner costs one timeout, not one
        # timeout per miner, so the control loop is never stalled for minutes.
        slots = list(self._slots.values())
        if len(slots) <= 1:
            for slot in slots:
                self._poll_one(slot)
        else:
            with ThreadPoolExecutor(max_workers=min(16, len(slots))) as pool:
                list(pool.map(self._poll_one, slots))
        self._last_poll = now

    # ---- MinerService surface ----------------------------------------

    def list_miners(self) -> list[Miner]:
        """Return all miners (sorted by id) from the cache, polling if stale."""
        self.refresh()
        return [self._slots[i].miner for i in sorted(self._slots)]

    def get(self, miner_id: int) -> Miner:
        """Return a single miner by id, polling if stale."""
        self.refresh()
        return self._slots[miner_id].miner

    def set_power(self, miner_id: int, power: Literal["ON", "OFF"]) -> Miner:
        """Pause or resume a miner; the cached state reflects the command at once."""
        slot = self._slots[miner_id]
        m = slot.miner
        try:
            if power == "ON":
                slot.client.resume()
                slot.commanded = "ON"
                slot.commanded_at = self._clock()
                m.status = "BOOTING"
                m.hashrate_mhs = None
            elif power == "OFF":
                slot.client.pause()
                slot.commanded = "OFF"
                slot.commanded_at = self._clock()
                m.status = "OFF"
                m.hashrate_mhs = None
            else:  # pragma: no cover - guarded by typing
                raise ValueError(f"Unsupported power command: {power!r}")
        except Exception as exc:
            # A miner that will not obey is a miner we cannot count on.
            self._logger.error("miner %s (%s) %s failed: %s", m.id, m.ip, power, exc)
            m.status = "ERROR"
            m.hashrate_mhs = None
        m.last_seen = _utcnow()
        return m
