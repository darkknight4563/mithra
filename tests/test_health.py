"""Smoke test for the runnable foundation."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    """GET /health returns 200 with the ok status payload."""
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
