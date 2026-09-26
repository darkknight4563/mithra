"""Scenario-injection endpoints.

Superset: the written-spec POST /api/scenario plus the live frontend's
/api/sim/ramp, /api/sim/drop?pct=, /api/sim/noisy?on= routes. All drive the
same SimulatedPlc modifiers via the controller.
"""

from fastapi import APIRouter

from app.schemas import ScenarioCommand
from app.routers.ws import broadcast_snapshot
from app.services.controller import controller

router = APIRouter(prefix="/api", tags=["scenario"])


@router.post("/scenario")
async def scenario(command: ScenarioCommand) -> dict:
    """Apply a scenario by name (written-spec form) and broadcast."""
    controller.apply_scenario(command.scenario, command.enabled)
    await broadcast_snapshot()
    return {"success": True, "scenario": command.scenario, "enabled": command.enabled}


@router.post("/sim/ramp")
async def sim_ramp() -> dict:
    """Frontend alias: start the gradual power ramp-up."""
    controller.apply_scenario("ramp_up", True)
    await broadcast_snapshot()
    return {"success": True, "scenario": "ramp_up"}


@router.post("/sim/drop")
async def sim_drop(pct: float = 20.0) -> dict:
    """Frontend alias: apply an instant power drop of ``pct`` percent."""
    controller.apply_scenario("sudden_drop", True, pct=pct / 100.0)
    await broadcast_snapshot()
    return {"success": True, "scenario": "sudden_drop", "pct": pct}


@router.post("/sim/noisy")
async def sim_noisy(on: bool = True) -> dict:
    """Frontend alias: toggle the noisy-gas fluctuation on or off."""
    controller.apply_scenario("noisy_gas", enabled=on)
    await broadcast_snapshot()
    return {"success": True, "scenario": "noisy_gas", "enabled": on}


@router.post("/sim/underfreq")
async def sim_underfreq() -> dict:
    """Frontend alias: hold the bus under-frequency (overloaded engine) for 25 s."""
    controller.apply_scenario("underfrequency", True)
    await broadcast_snapshot()
    return {"success": True, "scenario": "underfrequency"}
