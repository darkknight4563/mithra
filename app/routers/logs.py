"""Activity log REST endpoint."""

from typing import Optional

from fastapi import APIRouter

from app.schemas import LogsResponse
from app.services.controller import controller

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs", response_model=LogsResponse)
async def get_logs(level: Optional[str] = None, limit: int = 100) -> LogsResponse:
    """Return recent log events, newest first, optionally filtered by level."""
    items = list(reversed(controller.recent_logs()))  # newest first
    if level:
        items = [e for e in items if e.level == level]
    return LogsResponse(items=items[:limit])
