"""Controller state + live config REST endpoints."""

from fastapi import APIRouter

from app.schemas import ConfigUpdate, ControllerStateOut, ControllerStateResponse
from app.routers.ws import broadcast_snapshot
from app.services.controller import controller

router = APIRouter(prefix="/api/controller", tags=["controller"])


@router.get("/state", response_model=ControllerStateResponse)
async def get_state() -> ControllerStateResponse:
    """Return the current controller state (camelCase superset)."""
    return ControllerStateResponse(state=ControllerStateOut.from_state(controller.state))


@router.put("/config", response_model=ControllerStateResponse)
async def update_config(config: ConfigUpdate) -> ControllerStateResponse:
    """Update live control-loop parameters and reflect them in shared state."""
    if config.bufferFactor is not None:
        controller.buffer_factor = config.bufferFactor
        controller.state.buffer_factor = config.bufferFactor
    if config.hysteresis is not None:
        controller.hysteresis = config.hysteresis
        controller.state.hysteresis = config.hysteresis
    interval = config.loopInterval if config.loopInterval is not None else config.loopIntervalSec
    if interval is not None:
        controller.loop_interval = interval
        controller.state.loop_interval = interval
    controller.log(
        "INFO",
        f"Controller config updated: buffer={controller.buffer_factor}, "
        f"hysteresis={controller.hysteresis}, loop={controller.loop_interval}s",
    )
    await broadcast_snapshot()
    return ControllerStateResponse(state=ControllerStateOut.from_state(controller.state))
