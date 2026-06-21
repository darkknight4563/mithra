"""Miner fleet service.

In SIMULATION mode this holds the in-memory state of 10 mining units and fakes
realistic behaviour: turning a miner ON transitions ON via a brief BOOTING
state, ON miners report a plausible hashrate, and one unit is left in ERROR to
mirror the real dashboard.

The same method surface (`list_miners`, `set_power`) is what a real fleet
adapter would expose, so a hardware-backed implementation can drop in later.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Callable, Literal

from app.models import Miner

NUM_MINERS = 10
_IP_PREFIX = "192.168.1."
_POWER_MIN = 2.88
_POWER_MAX = 3.25
_HASHRATE_MIN = 50.0
_HASHRATE_MAX = 110.0
_BOOT_SECONDS = 3.0

# Realistic starting mix: most ON, a few OFF, one stuck in ERROR.
_INITIAL_STATUS: dict[int, str] = {
    1: "ON",
    2: "ON",
    3: "ON",
    4: "OFF",
    5: "ON",
    6: "OFF",
    7: "ON",
    8: "OFF",
    9: "OFF",
    10: "ERROR",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MinerService:
    """Holds and mutates the simulated miner fleet."""

    def __init__(
        self,
        boot_seconds: float = _BOOT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        seed: int | None = 42,
    ) -> None:
        self.boot_seconds = boot_seconds
        self._clock = clock
        self._rng = random.Random(seed)
        self._miners: dict[int, Miner] = {}
        self._boot_eta: dict[int, float] = {}
        self._seed_fleet()

    def _seed_fleet(self) -> None:
        for i in range(NUM_MINERS):
            mid = i + 1
            # Vary nominal power linearly across the configured band.
            power = round(_POWER_MIN + i * (_POWER_MAX - _POWER_MIN) / (NUM_MINERS - 1), 2)
            status = _INITIAL_STATUS[mid]
            hashrate = (
                round(self._rng.uniform(_HASHRATE_MIN, _HASHRATE_MAX), 1)
                if status == "ON"
                else None
            )
            self._miners[mid] = Miner(
                id=mid,
                ip=f"{_IP_PREFIX}{100 + mid}",
                priority=mid,  # priority 1..10 (lower = higher priority)
                status=status,  # type: ignore[arg-type]
                power_kw=power,
                hashrate_mhs=hashrate,
                last_seen=_utcnow(),
            )

    def _refresh(self) -> None:
        """Promote any BOOTING miners whose boot delay has elapsed to ON."""
        now = self._clock()
        for mid, eta in list(self._boot_eta.items()):
            if now >= eta:
                miner = self._miners[mid]
                miner.status = "ON"
                miner.hashrate_mhs = round(
                    self._rng.uniform(_HASHRATE_MIN, _HASHRATE_MAX), 1
                )
                miner.last_seen = _utcnow()
                del self._boot_eta[mid]

    def list_miners(self) -> list[Miner]:
        """Return all miners (sorted by id), after applying boot transitions."""
        self._refresh()
        return [self._miners[mid] for mid in sorted(self._miners)]

    def get(self, miner_id: int) -> Miner:
        self._refresh()
        return self._miners[miner_id]

    def set_power(self, miner_id: int, power: Literal["ON", "OFF"]) -> Miner:
        """Turn a miner ON (via BOOTING) or OFF."""
        miner = self._miners[miner_id]
        if power == "ON":
            miner.status = "BOOTING"
            miner.hashrate_mhs = None
            self._boot_eta[miner_id] = self._clock() + self.boot_seconds
        elif power == "OFF":
            miner.status = "OFF"
            miner.hashrate_mhs = None
            self._boot_eta.pop(miner_id, None)
        else:  # pragma: no cover - guarded by typing
            raise ValueError(f"Unsupported power command: {power!r}")
        miner.last_seen = _utcnow()
        # If boot_seconds is 0 the miner should already read as ON.
        self._refresh()
        return self._miners[miner_id]
