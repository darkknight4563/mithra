"""Pydantic models shared across the API. Minimal for now — only the health payload."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response body for the GET /health endpoint."""

    status: str
