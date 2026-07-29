"""Verify a DEPLOYED Gatekeeper instance. Run against the Render URL, not localhost.

    python scripts/verify_live.py https://gatekeeper-backend.onrender.com

Deliberately not under tests/ — setup.cfg sets `testpaths = tests`, so pytest
never collects this. It needs a live URL and takes ~2 minutes (the scripted demo
alone is ~82s).

Uses only httpx + websockets, both already in requirements.txt.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter

import httpx
import websockets

DEMO_WATCH_SECONDS = 110  # demo runtime (~82s) + slack for a slow tick

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """Record one pass/fail line and echo it immediately."""
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def ws_url(base: str) -> str:
    """Derive the WebSocket URL from the base HTTP URL (https -> wss)."""
    return base.replace("https://", "wss://").replace("http://", "ws://") + "/ws"


# ---- 1. health, dashboard, mode ---------------------------------------------


def check_http(base: str) -> None:
    """Health endpoint, dashboard HTML, and the runtime MODE the app reports."""
    with httpx.Client(base_url=base, timeout=90.0) as c:
        t0 = time.perf_counter()
        r = c.get("/health")
        wake = time.perf_counter() - t0
        record(
            "/health returns 200 ok",
            r.status_code == 200 and r.json() == {"status": "ok"},
            f"{r.status_code}, first-byte {wake:.1f}s",
        )

        r = c.get("/")
        html = r.text
        record(
            "dashboard loads at /",
            r.status_code == 200 and "<html" in html.lower(),
            f"{r.status_code}, {len(html)} bytes",
        )
        record(
            "SIMULATION badge present in markup",
            "sim-badge" in html and "SIMULATION" in html,
        )
        record("Per-Site ROI tab present", 'data-view="roi"' in html)

        # The badge is static markup; this is the one that proves the running
        # process is actually in SIMULATION and talking to SimulatedPlc.
        r = c.get("/api/controller/state")
        mode = r.json()["state"]["mode"]
        record("runtime MODE is SIMULATION (not just the badge)", mode == "SIMULATION", mode)


# ---- 2. ROI API --------------------------------------------------------------


def check_roi(base: str) -> None:
    """ROI defaults feed the sliders and presets; calculate accepts them back."""
    with httpx.Client(base_url=base, timeout=60.0) as c:
        r = c.get("/api/roi/defaults")
        ok = r.status_code == 200
        fields: list = []
        presets: list = []
        if ok:
            body = r.json()
            fields, presets = body.get("fields", []), body.get("presets", [])
        record(
            "/api/roi/defaults builds the sliders",
            ok and len(fields) > 0,
            f"{len(fields)} fields",
        )
        record("region presets load", len(presets) > 0, f"{len(presets)} presets")
        record(
            "every slider carries range + citation",
            bool(fields)
            and all(
                all(k in f for k in ("key", "value", "min", "max", "step", "unit", "cite"))
                for f in fields
            ),
        )

        payload = {f["key"]: f["value"] for f in fields}
        r = c.post("/api/roi/calculate", json=payload)
        record("/api/roi/calculate accepts the defaults", r.status_code == 200, str(r.status_code))

        # Each preset's overrides must also be accepted, or the region dropdown
        # 422s the moment an operator touches it.
        bad = [
            p["id"]
            for p in presets
            if c.post("/api/roi/calculate", json={**payload, **p["overrides"]}).status_code != 200
        ]
        record("every region preset calculates", not bad, f"rejected: {bad}" if bad else "")


# ---- 3. single-controller coherence -----------------------------------------


async def check_single_controller(base: str) -> None:
    """Behavioural check that ONE controller owns the fleet.

    A second worker means a second controller with its own ControllerState. From
    outside, that looks like consecutive REST reads flip-flopping between two
    divergent value-sets, and REST disagreeing with the WS stream. So: hold the
    WS open, fire rapid REST reads inside a single loop interval (default 10s,
    so 20 reads over ~4s), and count distinct states.

    One controller -> 1 distinct state, or 2 if a tick lands mid-window.
    N controllers  -> N interleaved lineages, and an A-B-A alternating pattern.
    """
    async with websockets.connect(ws_url(base), open_timeout=60) as ws:
        # Drain the initial snapshot burst and keep the latest controller state.
        ws_state = None
        for _ in range(6):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg["type"] == "controller:state":
                ws_state = msg["data"]
        record("WebSocket connects and sends a snapshot", ws_state is not None)

        def key(s: dict) -> tuple:
            return (s["availableKw"], s["activeMiners"], s["latestPlcKw"])

        seen: list[tuple] = []
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as c:
            for _ in range(20):
                r = await c.get("/api/controller/state")
                seen.append(key(r.json()["state"]))
                await asyncio.sleep(0.2)

        distinct = Counter(seen)
        # Alternation = returned to an earlier value after leaving it. A single
        # controller's state only moves forward between ticks.
        alternating = any(
            seen[i] != seen[i + 1] and seen[i] in seen[i + 2:] for i in range(len(seen) - 2)
        )
        record(
            "successive REST reads never jump between divergent states",
            len(distinct) <= 2 and not alternating,
            f"{len(distinct)} distinct over 20 reads, alternating={alternating}",
        )
        record(
            "REST agrees with the WS stream",
            ws_state is not None and key(ws_state) in distinct,
            f"ws={key(ws_state) if ws_state else None} rest={list(distinct)}",
        )


# ---- 4. live chart + the full scripted demo ---------------------------------


async def check_demo(base: str) -> None:
    """Watch the ~82s demo end to end over a single WebSocket connection.

    Asserts the chart feed moves, the connection survives the whole run, the
    failsafe genuinely zeroes the fleet, and every phase is reached.
    """
    phases = [
        ("steady", "Steady state"),
        ("ramp", "Gas flow rising"),
        ("shed", "Sudden gas drop"),
        ("recover", "Gas flow recovering"),
        ("failsafe", "Injecting generator fault"),
        ("restore", "Generator restored"),
        ("complete", "Complete"),
    ]
    kws: list[float] = []
    fleet_zeroed = False
    disconnected = ""

    # The controller's log buffer is a deque(maxlen=200) and the demo emits more
    # than that over its ~82s, so the opening phase is EVICTED before the run
    # ends. Sample continuously instead of reading logs once at the end.
    messages: set[str] = set()
    stop = asyncio.Event()

    async def collect_logs() -> None:
        """Union log messages every few seconds so nothing ages out unseen."""
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as c:
            while not stop.is_set():
                try:
                    for e in (await c.get("/api/logs?limit=200")).json()["items"]:
                        messages.add(e["message"])
                except Exception:  # a sampling blip must not fail the demo check
                    pass
                await asyncio.sleep(6)

    async with websockets.connect(ws_url(base), open_timeout=60) as ws:
        async with httpx.AsyncClient(base_url=base, timeout=60.0) as c:
            r = await c.post("/api/demo/run")
            record("demo starts", r.status_code == 200 and r.json().get("started") is True, r.text)

        collector = asyncio.create_task(collect_logs())
        deadline = time.monotonic() + DEMO_WATCH_SECONDS
        try:
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=40)
                msg = json.loads(raw)
                if msg["type"] == "controller:state":
                    st = msg["data"]
                    kws.append(st["latestPlcKw"])
                    # The failsafe must actually stop the fleet, not just log.
                    if st["latestPlcKw"] == 0 and st["activeMiners"] == 0:
                        fleet_zeroed = True
        except asyncio.TimeoutError:
            disconnected = "no WS message for 40s"
        except websockets.ConnectionClosed as e:
            disconnected = f"closed: {e.code} {e.reason}"
        finally:
            stop.set()
            await collector

        record("WebSocket survived the full demo", not disconnected, disconnected)

    # Phases come from the demo's own narration, sampled throughout the run.
    text = " | ".join(sorted(messages))
    seen_phases = [name for name, needle in phases if needle in text]

    record(
        "chart feed updates live (PLC value moves)",
        len(set(kws)) > 3,
        f"{len(set(kws))} distinct kW over {len(kws)} updates",
    )
    record(
        "demo runs steady -> ramp -> shed -> recover -> failsafe -> recover",
        len(seen_phases) == len(phases),
        f"reached {seen_phases}",
    )
    record("failsafe zeroes the fleet (activeMiners == 0)", fleet_zeroed)
    record("failsafe shows gas diverting to flare", "auto-diverts to flare" in text)


async def main(base: str) -> int:
    """Run every check against ``base`` and return a process exit code."""
    base = base.rstrip("/")
    print(f"Verifying {base}\n")
    check_http(base)
    check_roi(base)
    await check_single_controller(base)
    await check_demo(base)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("\nNot automated — check by eye: print-to-PDF (ROI tab -> Print) renders")
    print("a clean one-page leave-behind with sliders/toolbar hidden.")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/verify_live.py https://your-service.onrender.com")
    sys.exit(asyncio.run(main(sys.argv[1])))
