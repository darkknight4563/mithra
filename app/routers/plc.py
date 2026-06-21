"""PLC REST endpoints."""

from fastapi import APIRouter

from app.schemas import PlcLatestResponse
from app.services.controller import controller

router = APIRouter(prefix="/api/plc", tags=["plc"])


@router.get("/latest", response_model=PlcLatestResponse)
async def latest() -> PlcLatestResponse:
    """Return the most recent generator reading (or a fresh read if none yet)."""
    readings = controller.recent_readings()
    reading = readings[-1] if readings else controller.plc.read_power()
    return PlcLatestResponse(reading=reading)
