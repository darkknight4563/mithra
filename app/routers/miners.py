"""Miner REST endpoints."""

from fastapi import APIRouter, HTTPException

from app.schemas import MinerResponse, MinersResponse, PowerCommand
from app.routers.ws import broadcast_snapshot
from app.services.controller import controller

router = APIRouter(prefix="/api/miners", tags=["miners"])


@router.get("", response_model=MinersResponse)
async def list_miners() -> MinersResponse:
    """Return the full miner fleet."""
    return MinersResponse(miners=controller.miners.list_miners())


@router.post("/{miner_id}/power", response_model=MinerResponse)
async def set_power(miner_id: int, command: PowerCommand) -> MinerResponse:
    """Turn a miner ON or OFF, then broadcast the change to WS clients."""
    try:
        miner = controller.miners.set_power(miner_id, command.state)
    except KeyError:
        raise HTTPException(status_code=404, detail="Miner not found")
    controller.log("INFO", f"Miner {miner_id} power state changed to {command.state}")
    await broadcast_snapshot()
    return MinerResponse(miner=miner)
