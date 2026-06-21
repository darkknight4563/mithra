"""Dynamic load-balancing controller.

Plain, deterministic control logic — **no AI/LLM in the loop**. Every
LOOP_INTERVAL seconds it reads generator power, smooths it, and adds/removes a
single miner within a hysteresis dead-band so the fleet tracks available power
without thrashing. A hard fail-safe trips all miners OFF on a generator fault.

The controller owns the single shared `ControllerState` the API reads, plus
rolling buffers of recent readings (chart) and log events (activity feed).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from typing import Callable, Deque, Literal

from app.models import ControllerState, LogEvent, PlcReading
from app.services.miners import MinerService
from app.services.plc import PlcInterface, SimulatedPlc

Decision = Literal["ADD", "REMOVE", "NONE", "FAILSAFE"]

# How far the latest reading can stray from the moving average before we call it
# a fluctuation worth warning about.
_FLUCTUATION_THRESHOLD = 0.05


class Controller:
    def __init__(
        self,
        plc: PlcInterface,
        miners: MinerService,
        *,
        buffer_factor: float,
        hysteresis: float,
        loop_interval: int,
        mode: str = "SIMULATION",
        avg_window: int = 5,
        max_readings: int = 60,
        max_logs: int = 200,
        stagger_seconds: float = 2.0,
    ) -> None:
        self.plc = plc
        self.miners = miners
        self.buffer_factor = buffer_factor
        self.hysteresis = hysteresis
        self.loop_interval = loop_interval
        self.stagger_seconds = stagger_seconds

        self._avg: Deque[float] = deque(maxlen=avg_window)
        self.readings: Deque[PlcReading] = deque(maxlen=max_readings)
        self.logs: Deque[LogEvent] = deque(maxlen=max_logs)
        self._running = False

        self.state = ControllerState(
            mode=mode,  # type: ignore[arg-type]
            buffer_factor=buffer_factor,
            hysteresis=hysteresis,
            loop_interval=loop_interval,
            available_kw=0.0,
            mining_load_kw=0.0,
            active_miners=0,
            latest_plc_kw=0.0,
        )

    # ---- logging ---------------------------------------------------------

    def _log(self, level: str, message: str) -> LogEvent:
        event = LogEvent(level=level, message=message)  # type: ignore[arg-type]
        self.logs.append(event)
        return event

    # ---- one control-loop iteration -------------------------------------

    def tick(self) -> Decision:
        """Run a single iteration. Returns the decision taken.

        Synchronous and side-effect-contained so it can be unit-tested without
        an event loop; the async `run()` just calls it on a schedule.
        """
        t0 = time.perf_counter()
        reading = self.plc.read_power()
        self.readings.append(reading)
        self.state.latest_plc_kw = reading.generator_kw

        # 6. FAIL-SAFE: generator fault -> stop everything, immediately.
        if reading.status == "FAULT" or reading.generator_kw <= 0:
            for m in self.miners.list_miners():
                if m.status != "OFF":
                    self.miners.set_power(m.id, "OFF")
            self._avg.clear()
            self._log(
                "CRITICAL",
                "Generator fault — gas auto-diverts to flare, all miners stopped.",
            )
            self._update_state(available_power=0.0)
            self._log("INFO", f"Control loop iteration completed in {self._ms(t0)}ms")
            return "FAILSAFE"

        # 2 + 3. Smooth with a short moving average, then apply the buffer.
        self._avg.append(reading.generator_kw)
        avg = statistics.fmean(self._avg)
        available_power = avg * self.buffer_factor

        # 8. WARN on output fluctuation.
        if len(self._avg) >= 2 and avg > 0:
            if abs(reading.generator_kw - avg) / avg > _FLUCTUATION_THRESHOLD:
                self._log("WARN", "Generator output fluctuation detected")

        # 4. Current mining load. BOOTING miners are committed load too, so we
        # don't over-add while a unit is still spinning up.
        miners = self.miners.list_miners()
        on = [m for m in miners if m.status == "ON"]
        committed = [m for m in miners if m.status in ("ON", "BOOTING")]
        current_load = sum(m.power_kw for m in committed)
        off = sorted((m for m in miners if m.status == "OFF"), key=lambda m: m.priority)

        # 5. Decision with hysteresis dead-band. Check overload (REMOVE) first
        # for safety; ADD and REMOVE are otherwise mutually exclusive.
        decision: Decision = "NONE"
        if on and available_power < current_load * (1 - self.hysteresis):
            victim = max(on, key=lambda m: m.priority)  # lowest priority = highest number
            self.miners.set_power(victim.id, "OFF")
            self._log(
                "CRITICAL",
                f"Power overload detected - shutting down priority {victim.priority} miners",
            )
            decision = "REMOVE"
        elif off:
            candidate = off[0]  # next OFF miner by priority
            projected_load = current_load + candidate.power_kw
            if available_power > projected_load * (1 + self.hysteresis):
                self.miners.set_power(candidate.id, "ON")
                decision = "ADD"

        self._update_state(available_power=available_power)

        if decision in ("NONE", "ADD"):
            pct = (
                round(self.state.mining_load_kw / available_power * 100, 1)
                if available_power > 0
                else 0.0
            )
            self._log("INFO", f"Load balanced successfully, current usage: {pct}%")

        self._log("INFO", f"Control loop iteration completed in {self._ms(t0)}ms")
        return decision

    @staticmethod
    def _ms(t0: float) -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    def _update_state(self, available_power: float) -> None:
        miners = self.miners.list_miners()
        on = [m for m in miners if m.status == "ON"]
        committed = [m for m in miners if m.status in ("ON", "BOOTING")]
        self.state.available_kw = round(available_power, 2)
        self.state.mining_load_kw = round(sum(m.power_kw for m in committed), 2)
        self.state.active_miners = len(on)

    # ---- async background task ------------------------------------------

    async def run(self) -> None:
        """Run the control loop until `stop()` is called."""
        self._running = True
        self._log("INFO", "Control loop started")
        while self._running:
            decision = self.tick()
            # 7. Stagger: pause a short beat after any change before continuing.
            if decision in ("ADD", "REMOVE"):
                await asyncio.sleep(self.stagger_seconds)
            await asyncio.sleep(self.loop_interval)

    def stop(self) -> None:
        self._running = False

    # ---- scenario injection (dashboard Scenario Controls) ---------------

    def apply_scenario(self, name: str, enabled: bool = True) -> None:
        """Drive the SimulatedPlc's modifiers. No-op outside SIMULATION."""
        if not isinstance(self.plc, SimulatedPlc):
            self._log("WARN", f"Scenario '{name}' ignored (not in SIMULATION mode)")
            return
        if name == "ramp_up":
            self.plc.start_ramp(0.5)
            self._log("INFO", "Scenario: ramp_up enabled (+0.5 kW/s)")
        elif name == "sudden_drop":
            self.plc.sudden_drop(0.20)
            self._log("WARN", "Scenario: sudden_drop applied (-20%)")
        elif name == "noisy_gas":
            self.plc.set_noise(0.03 if enabled else 0.01)
            self._log("INFO", f"Scenario: noisy_gas {'enabled' if enabled else 'disabled'}")
        else:
            self._log("WARN", f"Unknown scenario '{name}'")

    # ---- read accessors for the API -------------------------------------

    def recent_readings(self) -> list[PlcReading]:
        return list(self.readings)

    def recent_logs(self) -> list[LogEvent]:
        return list(self.logs)


def build_controller(
    settings, clock: Callable[[], float] = time.monotonic
) -> Controller:
    """Construct a Controller wired to the configured PLC and a fresh fleet."""
    from app.services.plc import get_plc

    return Controller(
        plc=get_plc(settings),
        miners=MinerService(clock=clock),
        buffer_factor=settings.buffer_factor,
        hysteresis=settings.hysteresis,
        loop_interval=settings.loop_interval,
        mode=settings.mode,
    )


# Single shared instance the API reads and the background task drives.
from app.config import settings  # noqa: E402

controller = build_controller(settings)
