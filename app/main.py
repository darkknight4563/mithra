"""FastAPI application entrypoint.

Wires CORS, the REST + WebSocket routers, and runs the control loop as a
background task for the lifetime of the app.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.models import HealthResponse
from app.routers import controller as controller_router
from app.routers import demo, logs, miners, plc, scenario, ws
from app.routers.ws import broadcast_snapshot
from app.services.controller import controller

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the control loop on startup and stop it cleanly on shutdown."""
    # Seed a believable recent log history so the Logs page isn't empty.
    controller.seed_history()
    # Start the control loop and have each tick push WS updates.
    controller.set_tick_hook(broadcast_snapshot)
    task = asyncio.create_task(controller.run())
    try:
        yield
    finally:
        controller.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plc.router)
app.include_router(miners.router)
app.include_router(controller_router.router)
app.include_router(logs.router)
app.include_router(scenario.router)
app.include_router(demo.router)
app.include_router(ws.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness probe used by the dashboard and orchestration."""
    return HealthResponse(status="ok")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """Serve the bundled control-room dashboard at the root URL (same-origin)."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
