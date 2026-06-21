"""Scripted demo endpoints for live investor / accelerator demonstrations."""

import asyncio

from fastapi import APIRouter

from app.services.controller import controller

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/run")
async def run() -> dict:
    """Kick off the full scripted demo sequence (non-blocking, ~74s)."""
    if controller.demo_active:
        return {"started": False, "reason": "a demo is already running"}
    asyncio.create_task(controller.run_demo())
    return {"started": True, "durationSeconds": 82}


@router.post("/failsafe")
async def failsafe() -> dict:
    """Kick off the standalone generator-fault / failsafe demonstration (~19s)."""
    if controller.demo_active:
        return {"started": False, "reason": "a demo is already running"}
    asyncio.create_task(controller.demo_failsafe())
    return {"started": True, "durationSeconds": 19}
